from __future__ import annotations
import asyncio
import logging
import socket
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from libqretprop.qlcp.decoding import decode_packet_server
from libqretprop.qlcp.packets import DataPacket
from libqretprop.runtime.metrics import NULL_METRICS, Metrics


logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from collections.abc import Mapping

    from libqretprop.runtime.esp_device_session import ESPDeviceSession


UDP_PORT = 50001  # Distinct from the TCP port; a different number is useful for debugging.


@dataclass(frozen=True, slots=True)
class TelemetryReading:
    sensor_id: int
    sensor_name: str
    value: float
    unit_name: str
    sensor_type: str


@dataclass(frozen=True, slots=True)
class TelemetryBatch:
    """Internal telemetry ingest batch; not a stable public API contract."""

    device_name: str
    device_address: str
    connection_key: str
    timestamp_s: float
    readings: tuple[TelemetryReading, ...]


class BatchPublisher(Protocol):
    """Fan-out target for decoded telemetry batches (e.g. ``TelemetryStreamRuntime``)."""

    def publish_batch(self, batch: TelemetryBatch) -> None: ...


class SessionRegistry(Protocol):
    @property
    def devices(self) -> Mapping[str, ESPDeviceSession]: ...


class TelemetryIngest:
    """Processes UDP DATA datagrams after the socket loop receives them."""

    def __init__(
        self,
        runtime: SessionRegistry,
        *,
        metrics: Metrics | None = None,
    ) -> None:
        self.runtime = runtime
        self.metrics = metrics or NULL_METRICS

    def handle_datagram(self, data: bytes, address: str) -> TelemetryBatch | None:
        session = self.runtime.devices.get(address)
        if session is None:
            self.metrics.record_telemetry_datagram(len(data))
            self.metrics.record_telemetry_decode_error("unknown_device")
            logger.error(f"Received UDP packet from unknown device {address}")
            return None

        self.metrics.record_telemetry_datagram(len(data), device=session.name)

        try:
            packet = decode_packet_server(data)
        except Exception as e:
            self.metrics.record_telemetry_decode_error("decode")
            logger.error(f"Error decoding UDP packet from {address}: {e}")
            return None

        if not isinstance(packet, DataPacket):
            self.metrics.record_telemetry_decode_error("non_data")
            logger.error(f"Received non-DATA packet over UDP from {session.name}. Ignoring.")
            return None

        return self.handle_packet(packet, session)

    def handle_packet(self, packet: DataPacket, session: ESPDeviceSession) -> TelemetryBatch:
        self.metrics.record_telemetry_data_packet(session.name)
        timestamp_s = packet.timestamp / 1000.0 if session.last_sync_time is not None else time.monotonic()
        readings: list[TelemetryReading] = []

        for reading in packet.readings:
            sensor = session.qlcp_config.sensors_by_id.get(reading.sensor_id)
            if sensor is None:
                self.metrics.record_telemetry_decode_error("unknown_sensor")
                logger.error(
                    f"Received DATA reading for unknown sensor id {reading.sensor_id} from {session.name}. Ignoring.",
                )
                continue

            readings.append(
                TelemetryReading(
                    sensor_id=reading.sensor_id,
                    sensor_name=sensor.name,
                    value=reading.value,
                    unit_name=reading.unit.name,
                    sensor_type=sensor.type,
                ),
            )

        batch = TelemetryBatch(
            device_name=session.name,
            device_address=session.address,
            connection_key=session.connection_key,
            timestamp_s=timestamp_s,
            readings=tuple(readings),
        )
        self.metrics.record_telemetry_readings(session.name, len(batch.readings))
        return batch


class TelemetryUDPListener:
    """Owns the UDP socket receive loop for incoming DATA datagrams.

    Delegates decode/batch creation to ``TelemetryIngest`` and forwards decoded
    batches to one or more ``BatchPublisher`` targets. It owns no decode or
    sensor-mapping logic of its own.
    """

    def __init__(
        self,
        ingest: TelemetryIngest,
        *publishers: BatchPublisher,
        port: int = UDP_PORT,
        batch_size: int = 128,
        recv_buffer_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self.ingest = ingest
        self.publishers = publishers
        self.port = port
        # Max packets drained per event-loop tick before yielding to other tasks.
        self.batch_size = batch_size
        self.recv_buffer_bytes = recv_buffer_bytes

    async def run(self) -> None:
        """Bind the UDP socket and forward decoded batches until cancelled."""
        loop = asyncio.get_event_loop()
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.recv_buffer_bytes)
        udp_socket.bind(("0.0.0.0", self.port))  # noqa: S104
        udp_socket.setblocking(False)

        logger.info(f"UDP listener started on port {self.port}")

        while True:
            try:
                data, addr = await loop.sock_recvfrom(udp_socket, 4096)

                # Process the first packet plus any already-buffered ones, up to batch_size.
                # This keeps the UDP listener from monopolizing the event loop while other
                # tasks (e.g. TCP command handling) need to run.
                processed = 0
                while True:
                    device_ip = addr[0]
                    batch = self.ingest.handle_datagram(data, device_ip)
                    if batch is not None:
                        for publisher in self.publishers:
                            publisher.publish_batch(batch)
                    processed += 1
                    if processed >= self.batch_size:
                        break
                    try:
                        data, addr = udp_socket.recvfrom(4096)
                    except BlockingIOError:
                        break

                await asyncio.sleep(0)  # Yield to let other tasks run

            except asyncio.CancelledError:
                logger.info("UDP listener cancelled")
                udp_socket.close()
                raise
            except Exception as e:
                logger.error(f"Error in UDP listener: {e}")
                await asyncio.sleep(0.1)

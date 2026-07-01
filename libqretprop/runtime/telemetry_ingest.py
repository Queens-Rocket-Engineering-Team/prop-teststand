from __future__ import annotations
import asyncio
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from libqretprop.qlcp.decoding import decode_packet_server
from libqretprop.qlcp.packets import DataPacket
from libqretprop.runtime.metrics import Metrics


logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from libqretprop.runtime.esp_connection_runtime import ESPDeviceSession


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
    timestamp_source: Literal["device_synced", "server_receive"]
    timestamp_synced: bool


class TelemetryPublisher(Protocol):
    """A telemetry fan-out target that accepts decoded batches synchronously."""

    def publish_batch(self, batch: TelemetryBatch) -> None: ...


class TelemetryRuntime:
    """Owns UDP telemetry ingest, decode, sensor mapping, and fan-out."""

    def __init__(
        self,
        device_for_address: Callable[[str], ESPDeviceSession | None],
        *publishers: TelemetryPublisher,
        metrics: Metrics | None = None,
    ) -> None:
        self._device_for_address = device_for_address
        self.publishers = publishers
        self.metrics = metrics or Metrics()

    def handle_datagram(self, data: bytes, address: str) -> TelemetryBatch | None:
        session = self._session_for_udp_address(address)
        if session is None:
            self.metrics.record_telemetry_datagram(len(data))
            self.metrics.record_telemetry_unknown_source("unregistered_address")
            logger.error("Received UDP packet from unknown device %s", address)
            return None

        self.metrics.record_telemetry_datagram(len(data), device=session.name)

        try:
            packet = decode_packet_server(data)
        except Exception:
            self.metrics.record_telemetry_decode_error("decode")
            logger.exception("Error decoding UDP packet from %s", address)
            return None

        if not isinstance(packet, DataPacket):
            self.metrics.record_telemetry_decode_error("non_data")
            logger.error("Received non-DATA packet over UDP from %s. Ignoring.", session.name)
            return None

        return self.handle_packet(packet, session)

    def handle_packet(self, packet: DataPacket, session: ESPDeviceSession) -> TelemetryBatch:
        self.metrics.record_telemetry_data_packet(session.name)
        timestamp_s, timestamp_source, timestamp_synced = self._batch_timestamp(packet, session)
        readings: list[TelemetryReading] = []

        for reading in packet.readings:
            sensor = session.qlcp_config.sensors_by_id.get(reading.sensor_id)
            if sensor is None:
                self.metrics.record_telemetry_decode_error("unknown_sensor")
                logger.error(
                    "Received DATA reading for unknown sensor id %s from %s. Ignoring.",
                    reading.sensor_id,
                    session.name,
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
            timestamp_source=timestamp_source,
            timestamp_synced=timestamp_synced,
            readings=tuple(readings),
        )
        self.metrics.record_telemetry_readings(session.name, len(batch.readings))
        return batch

    def _session_for_udp_address(self, address: str) -> ESPDeviceSession | None:
        return self._device_for_address(address)

    @staticmethod
    def _batch_timestamp(
        packet: DataPacket,
        session: ESPDeviceSession,
    ) -> tuple[float, Literal["device_synced", "server_receive"], bool]:
        # TIMESYNC seeds the device clock from get_timestamp_ms(), so both share one axis; wraps at ~49.7 days.
        if session.last_sync_time is None:
            return time.monotonic(), "server_receive", False
        return packet.timestamp / 1000.0, "device_synced", True

    async def run_udp_listener(
        self,
        *,
        port: int = UDP_PORT,
        batch_size: int = 128,
        recv_buffer_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        """Bind the UDP socket and forward decoded batches until cancelled."""
        loop = asyncio.get_event_loop()
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, recv_buffer_bytes)
        udp_socket.bind(("0.0.0.0", port))  # noqa: S104
        udp_socket.setblocking(False)

        logger.info("UDP listener started on port %s", port)

        while True:
            try:
                data, addr = await loop.sock_recvfrom(udp_socket, 4096)

                # Process the first packet plus any already-buffered ones, up to batch_size.
                # This keeps the UDP listener from monopolizing the event loop while other
                # tasks (e.g. TCP command handling) need to run.
                processed = 0
                while True:
                    device_ip = addr[0]
                    batch = self.handle_datagram(data, device_ip)
                    if batch is not None:
                        for publisher in self.publishers:
                            publisher.publish_batch(batch)
                    processed += 1
                    if processed >= batch_size:
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
            except Exception:
                logger.exception("Error in UDP listener: %s")
                await asyncio.sleep(0.1)

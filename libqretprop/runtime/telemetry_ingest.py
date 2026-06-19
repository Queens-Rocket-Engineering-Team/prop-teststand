from __future__ import annotations
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import libqretprop.mylogging as ml
from libqretprop.qlcp.decoding import decode_packet_server
from libqretprop.qlcp.packets import DataPacket


if TYPE_CHECKING:
    from libqretprop.runtime.esp_device_session import ESPDeviceSession


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


class LegacyTelemetrySink(Protocol):
    def publish_batch(self, batch: TelemetryBatch) -> None: ...


class SessionRegistry(Protocol):
    @property
    def devices(self) -> Mapping[str, ESPDeviceSession]: ...


class LogLegacyTelemetrySink:
    """Preserves legacy GUI telemetry log lines while ingest is refactored."""

    def publish_batch(self, batch: TelemetryBatch) -> None:
        for reading in batch.readings:
            ml.log(
                f"{batch.device_name} {batch.timestamp_s:.3f} "
                f"{reading.sensor_name}:{reading.value:.2f}",
            )


class TelemetryIngest:
    """Processes UDP DATA datagrams after the socket loop receives them."""

    def __init__(
        self,
        runtime: SessionRegistry,
        *,
        legacy_sink: LegacyTelemetrySink | None = None,
    ) -> None:
        self.runtime = runtime
        self.legacy_sink = LogLegacyTelemetrySink() if legacy_sink is None else legacy_sink

    def handle_datagram(self, data: bytes, address: str) -> TelemetryBatch | None:
        session = self.runtime.devices.get(address)
        if session is None:
            ml.elog(f"Received UDP packet from unknown device {address}")
            return None

        try:
            packet = decode_packet_server(data)
        except Exception as e:
            ml.elog(f"Error decoding UDP packet from {address}: {e}")
            return None

        if not isinstance(packet, DataPacket):
            ml.elog(f"Received non-DATA packet over UDP from {session.name}. Ignoring.")
            return None

        return self.handle_packet(packet, session)

    def handle_packet(self, packet: DataPacket, session: ESPDeviceSession) -> TelemetryBatch:
        timestamp_s = packet.timestamp / 1000.0 if session.last_sync_time is not None else time.monotonic()
        readings: list[TelemetryReading] = []

        for reading in packet.readings:
            sensor = session.qlcp_config.sensors_by_id.get(reading.sensor_id)
            if sensor is None:
                ml.elog(
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
        self.legacy_sink.publish_batch(batch)
        return batch

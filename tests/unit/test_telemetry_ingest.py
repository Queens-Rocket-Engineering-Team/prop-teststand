from types import SimpleNamespace
from typing import Any, cast

import pytest

from libqretprop.qlcp.config_parser import parse_config
from libqretprop.qlcp.enums import PacketType, Unit
from libqretprop.qlcp.packets import AckPacket, DataPacket, SensorReading
from libqretprop.runtime.esp_connection_runtime import ESPDeviceSession
from libqretprop.runtime.metrics import Metrics
from libqretprop.runtime.telemetry_ingest import (
    TelemetryReading,
    TelemetryRuntime,
)


def _metrics_snapshot(metrics: Metrics) -> dict[str, Any]:
    return cast("dict[str, Any]", metrics.to_dict())


class FakeRuntime:
    def __init__(self) -> None:
        self.devices: dict[str, ESPDeviceSession] = {}


def _make_config() -> dict[str, Any]:
    return {
        "device_name": "PANDA",
        "device_type": "Sensor Monitor",
        "sensor_info": {
            "thermocouple": {
                "TC1": {
                    "sensor_index": "TC1",
                    "type": "K",
                    "unit": "C",
                },
                "TC2": {
                    "sensor_index": "TC2",
                    "type": "K",
                    "unit": "C",
                },
            },
        },
        "controls": {},
    }


def _make_session(*, address: str = "10.0.0.2", last_sync_time: float | None = 1.0) -> ESPDeviceSession:
    config = parse_config(_make_config())
    session = SimpleNamespace(
        name=config.name,
        address=address,
        connection_key="conn-a",
        qlcp_config=config,
        last_sync_time=last_sync_time,
    )
    return cast("ESPDeviceSession", session)


def test_data_packet_from_registered_session_produces_batch() -> None:
    runtime = FakeRuntime()
    session = _make_session()
    runtime.devices[session.address] = session
    ingest = TelemetryRuntime(runtime)
    packet = DataPacket(
        sequence=1,
        timestamp=12345,
        readings=[
            SensorReading(sensor_id=0, unit=Unit.CELSIUS, value=12.345),
            SensorReading(sensor_id=1, unit=Unit.CELSIUS, value=67.891),
        ],
    )

    batch = ingest.handle_datagram(packet.encode(), session.address)

    assert batch is not None
    assert batch.device_name == "PANDA"
    assert batch.device_address == session.address
    assert batch.connection_key == "conn-a"
    assert batch.timestamp_s == 12.345
    assert len(batch.readings) == 2
    assert batch.readings[0].value == pytest.approx(12.345)
    assert batch.readings[1].value == pytest.approx(67.891)
    assert batch.readings == (
        TelemetryReading(
            sensor_id=0,
            sensor_name="TC1",
            value=batch.readings[0].value,
            unit_name="CELSIUS",
            sensor_type="thermocouple",
        ),
        TelemetryReading(
            sensor_id=1,
            sensor_name="TC2",
            value=batch.readings[1].value,
            unit_name="CELSIUS",
            sensor_type="thermocouple",
        ),
    )


def test_unsynced_session_uses_monotonic_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    session = _make_session(last_sync_time=None)
    runtime.devices[session.address] = session
    ingest = TelemetryRuntime(runtime)
    monkeypatch.setattr("libqretprop.runtime.telemetry_ingest.time.monotonic", lambda: 42.25)
    packet = DataPacket(
        sequence=1,
        timestamp=12345,
        readings=[SensorReading(sensor_id=0, unit=Unit.CELSIUS, value=1.0)],
    )

    batch = ingest.handle_datagram(packet.encode(), session.address)

    assert batch is not None
    assert batch.timestamp_s == 42.25


def test_unknown_device_address_is_logged_and_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    errors: list[str] = []
    monkeypatch.setattr("libqretprop.runtime.telemetry_ingest.logger.error", errors.append)
    ingest = TelemetryRuntime(runtime)

    batch = ingest.handle_datagram(b"not decoded", "10.0.0.99")

    assert batch is None
    assert errors == ["Received UDP packet from unknown device 10.0.0.99"]


def test_decode_error_records_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    session = _make_session()
    runtime.devices[session.address] = session
    metrics = Metrics(time_fn=lambda: 100.0)
    monkeypatch.setattr("libqretprop.runtime.telemetry_ingest.logger.error", lambda *_args, **_kwargs: None)
    ingest = TelemetryRuntime(runtime, metrics=metrics)

    batch = ingest.handle_datagram(b"not decoded", session.address)

    assert batch is None
    snapshot = _metrics_snapshot(metrics)
    assert snapshot["telemetry"]["decode_errors"]["total"]["decode"] == 1
    assert snapshot["telemetry"]["ingest"]["by_device"]["PANDA"]["udp_bytes_total"] == len(b"not decoded")


def test_data_packets_record_throughput_without_packet_loss_estimate() -> None:
    runtime = FakeRuntime()
    session = _make_session()
    runtime.devices[session.address] = session
    metrics = Metrics(time_fn=lambda: 100.0)
    ingest = TelemetryRuntime(runtime, metrics=metrics)
    readings = [SensorReading(sensor_id=0, unit=Unit.CELSIUS, value=1.0)]

    ingest.handle_packet(DataPacket(sequence=254, timestamp=12345, readings=readings), session)
    ingest.handle_packet(DataPacket(sequence=1, timestamp=12346, readings=readings), session)

    snapshot = _metrics_snapshot(metrics)
    assert "loss" not in snapshot["telemetry"]
    assert snapshot["telemetry"]["ingest"]["by_device"]["PANDA"]["data_packets_total"] == 2
    assert "batches_total" not in snapshot["telemetry"]["ingest"]["by_device"]["PANDA"]
    assert snapshot["telemetry"]["ingest"]["by_device"]["PANDA"]["readings_total"] == 2


def test_non_data_packet_is_logged_and_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    session = _make_session()
    runtime.devices[session.address] = session
    errors: list[str] = []
    monkeypatch.setattr("libqretprop.runtime.telemetry_ingest.logger.error", errors.append)
    ingest = TelemetryRuntime(runtime)
    packet = AckPacket.create(PacketType.HEARTBEAT, ack_sequence=4)

    batch = ingest.handle_datagram(packet.encode(), session.address)

    assert batch is None
    assert errors == ["Received non-DATA packet over UDP from PANDA. Ignoring."]


def test_unknown_sensor_id_is_logged_and_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    session = _make_session()
    runtime.devices[session.address] = session
    errors: list[str] = []
    monkeypatch.setattr("libqretprop.runtime.telemetry_ingest.logger.error", errors.append)
    ingest = TelemetryRuntime(runtime)
    packet = DataPacket(
        sequence=1,
        timestamp=12345,
        readings=[SensorReading(sensor_id=99, unit=Unit.CELSIUS, value=1.0)],
    )

    batch = ingest.handle_datagram(packet.encode(), session.address)

    assert batch is not None
    assert batch.readings == ()
    assert errors == ["Received DATA reading for unknown sensor id 99 from PANDA. Ignoring."]

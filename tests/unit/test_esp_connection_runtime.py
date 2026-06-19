import asyncio
import socket
from types import SimpleNamespace
from typing import Any, cast

import pytest

from libqretprop.Devices.ESPDevice import ESPDevice
from libqretprop.qlcp.config_parser import parse_config
from libqretprop.qlcp.enums import ControlState, DeviceStatus, ErrorCode, PacketType
from libqretprop.qlcp.packets import (
    AckPacket,
    ControlStatus,
    NackPacket,
    SimplePacket,
    StatusPacket,
)
from libqretprop.runtime.command_tracker import CommandLifecycle, CommandTracker
from libqretprop.runtime.esp_connection_runtime import ESPConnectionRuntime
from libqretprop.state.system_state import SystemState


class FakeStateStream:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def publish(self, event: dict[str, object] | None) -> None:
        if event is not None:
            self.events.append(event)


class FakeDriver:
    def __init__(self) -> None:
        self.sent_packets: list[object] = []

    async def send_packet(self, packet: object) -> None:
        self.sent_packets.append(packet)


@pytest.fixture(autouse=True)
def _mute_runtime_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    from libqretprop.runtime import esp_connection_runtime

    monkeypatch.setattr(esp_connection_runtime.ml, "slog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(esp_connection_runtime.ml, "plog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(esp_connection_runtime.ml, "elog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(esp_connection_runtime.ml, "log", lambda *_args, **_kwargs: None)


def _make_config(name: str = "TEST-DEVICE") -> dict[str, Any]:
    return {
        "device_name": name,
        "device_type": "Sensor Monitor",
        "sensor_info": {
            "thermocouple": {
                "TC1": {
                    "sensor_index": "TC1",
                    "type": "K",
                    "unit": "C",
                },
            },
        },
        "controls": {
            "VALVE1": {
                "control_index": "VALVE1",
                "type": "solenoid",
                "default_state": "CLOSED",
            },
        },
    }


def _make_runtime() -> tuple[ESPConnectionRuntime, CommandTracker, SystemState, FakeStateStream]:
    tracker = CommandTracker()
    state = SystemState(command_tracker=tracker)
    stream = FakeStateStream()
    runtime = ESPConnectionRuntime(
        command_tracker=tracker,
        system_state=state,
        state_stream=stream,
    )
    return runtime, tracker, state, stream


def _make_device(
    runtime: ESPConnectionRuntime,
    *,
    address: str = "10.0.0.2",
    connection_key: str = "conn-a",
    name: str = "TEST-DEVICE",
) -> ESPDevice:
    config = parse_config(_make_config(name=name))
    control_states = {
        control.name.upper(): control.default.name
        for control in config.controls_by_id.values()
    }
    device = SimpleNamespace(
        address=address,
        connection_key=connection_key,
        connection_runtime=runtime,
        name=config.name,
        type=config.device_type,
        qlcp_config=config,
        controls={
            control.name.upper(): control
            for control in config.controls_by_id.values()
        },
        control_states=control_states,
        sensor_names=[],
        jsonConfig=config.raw_config,
        socket=None,
        driver=FakeDriver(),
        last_sync_time=None,
        _resync_pending=False,
        _missed_heartbeat_acks=0,
        is_responsive=True,
    )

    def set_control_state(control_name: str, state: str) -> None:
        device.control_states[control_name.upper()] = state

    device.setControlState = set_control_state
    return cast(ESPDevice, device)


def test_runtime_registers_valid_device() -> None:
    async def run() -> None:
        runtime, _tracker, state, stream = _make_runtime()
        server_sock, peer_sock = socket.socketpair()
        server_sock.setblocking(False)
        peer_sock.setblocking(False)

        try:
            device = await runtime.register_configured_device(
                server_sock,
                "10.0.0.2",
                _make_config(),
                config_sequence=12,
            )

            assert device.connection_key == "esp-1"
            assert device.connection_runtime is runtime
            assert runtime.devices["10.0.0.2"] is device
            assert state.snapshot().devices[0].name == "TEST-DEVICE"
            assert stream.events[0]["type"] == "device.registered"
        finally:
            runtime.close_all()
            peer_sock.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_runtime_replaces_existing_device_and_fails_pending_commands() -> None:
    runtime, tracker, state, stream = _make_runtime()
    old_device = _make_device(runtime, address="10.0.0.2", connection_key="conn-old")
    other_device = _make_device(runtime, address="10.0.0.4", connection_key="conn-other", name="OTHER")
    runtime.devices[old_device.address] = old_device
    runtime.devices[other_device.address] = other_device
    state.register_device(old_device)
    pending = tracker.mark_sent(
        connection_key=old_device.connection_key,
        device_name=old_device.name,
        device_address=old_device.address,
        packet_type=PacketType.CONTROL,
        packet_sequence=7,
        now=10.0,
    )

    runtime.disconnect_registered_devices_with_name(old_device.name)

    assert old_device.address not in runtime.devices
    assert runtime.devices[other_device.address] is other_device
    assert pending.state == CommandLifecycle.TIMED_OUT
    assert tracker.pending == ()
    assert stream.events[-1]["type"] == "device.disconnected"


def test_runtime_removal_marks_device_disconnected() -> None:
    runtime, _tracker, state, stream = _make_runtime()
    device = _make_device(runtime)
    runtime.devices[device.address] = device
    state.register_device(device)

    runtime.remove_device(device)

    assert device.address not in runtime.devices
    assert state.snapshot().devices[0].connected is False
    assert stream.events[-1]["type"] == "device.disconnected"


def test_runtime_ack_routes_through_tracker_and_updates_control_state() -> None:
    runtime, tracker, state, stream = _make_runtime()
    device = _make_device(runtime)
    runtime.devices[device.address] = device
    state.register_device(device)
    command = tracker.mark_sent(
        connection_key=device.connection_key,
        device_name=device.name,
        device_address=device.address,
        packet_type=PacketType.CONTROL,
        packet_sequence=12,
        now=10.0,
        control_id=0,
        control_name="VALVE1",
        requested_state=ControlState.OPEN,
    )

    runtime.handle_ack(
        device,
        AckPacket(
            sequence=20,
            timestamp=0,
            ack_packet_type=PacketType.CONTROL,
            ack_sequence=12,
        ),
    )

    assert command.state == CommandLifecycle.ACKED
    assert device.control_states["VALVE1"] == "OPEN"
    assert state.snapshot().devices[0].controls[0].reported_state == "OPEN"
    assert [event["type"] for event in stream.events[-2:]] == ["command.acked", "control.updated"]


def test_runtime_nack_routes_through_tracker_without_control_update() -> None:
    runtime, tracker, state, stream = _make_runtime()
    device = _make_device(runtime)
    runtime.devices[device.address] = device
    state.register_device(device)
    command = tracker.mark_sent(
        connection_key=device.connection_key,
        device_name=device.name,
        device_address=device.address,
        packet_type=PacketType.CONTROL,
        packet_sequence=12,
        now=10.0,
        control_id=0,
        control_name="VALVE1",
        requested_state=ControlState.OPEN,
    )

    runtime.handle_nack(
        device,
        NackPacket(
            sequence=20,
            timestamp=0,
            nack_packet_type=PacketType.CONTROL,
            nack_sequence=12,
            error_code=ErrorCode.INVALID_PARAM,
        ),
    )

    assert command.state == CommandLifecycle.NACKED
    assert device.control_states["VALVE1"] == "CLOSED"
    assert state.snapshot().devices[0].controls[0].reported_state is None
    assert stream.events[-1]["type"] == "command.nacked"


def test_runtime_status_updates_reported_control_state() -> None:
    runtime, _tracker, state, stream = _make_runtime()
    device = _make_device(runtime)
    runtime.devices[device.address] = device
    state.register_device(device)

    runtime.handle_status(
        device,
        StatusPacket(
            sequence=1,
            timestamp=0,
            status=DeviceStatus.ACTIVE,
            control_states=[ControlStatus(id=0, state=ControlState.OPEN)],
        ),
    )

    assert device.control_states["VALVE1"] == "OPEN"
    assert state.snapshot().devices[0].controls[0].reported_state == "OPEN"
    assert stream.events[-1]["type"] == "control.updated"


def test_runtime_uses_injected_legacy_log_sink() -> None:
    class LegacyLogSink:
        def __init__(self) -> None:
            self.messages: list[tuple[str, str, str | None]] = []

        def device_connected(self, device: ESPDevice) -> None:
            self.messages.append(("connected", device.name, None))

        def device_disconnected(self, device: ESPDevice) -> None:
            self.messages.append(("disconnected", device.name, None))

        def control_status(self, device: ESPDevice, control_name: str, state: str) -> None:
            self.messages.append(("status", f"{device.name}:{control_name}", state))

    runtime, _tracker, state, _stream = _make_runtime()
    legacy_log_sink = LegacyLogSink()
    runtime.legacy_log_sink = legacy_log_sink
    device = _make_device(runtime)
    runtime.devices[device.address] = device
    state.register_device(device)

    runtime.handle_status(
        device,
        StatusPacket(
            sequence=1,
            timestamp=0,
            status=DeviceStatus.ACTIVE,
            control_states=[ControlStatus(id=0, state=ControlState.OPEN)],
        ),
    )
    runtime.remove_device(device)

    assert legacy_log_sink.messages == [
        ("status", "TEST-DEVICE:VALVE1", "OPEN"),
        ("disconnected", "TEST-DEVICE", None),
    ]


def test_runtime_command_visibility_policy_for_status_request_and_estop() -> None:
    async def run() -> None:
        runtime, tracker, _state, stream = _make_runtime()
        device = _make_device(runtime)

        status_request = await runtime.send_tracked_command(
            device,
            SimplePacket(packet_type=PacketType.STATUS_REQUEST, sequence=30, timestamp=0),
        )
        estop = await runtime.send_tracked_command(
            device,
            SimplePacket(packet_type=PacketType.ESTOP, sequence=31, timestamp=0),
        )

        assert status_request.ack_expected is False
        assert estop.ack_expected is False
        assert tracker.pending == ()
        assert tracker.recent_completed == (estop,)
        assert stream.events == [
            {
                "type": "command.sent",
                "state_version": 1,
                "command": {
                    "command_id": estop.command_id,
                    "connection_key": device.connection_key,
                    "device_address": device.address,
                    "device_name": device.name,
                    "packet_type": "ESTOP",
                    "sequence": 31,
                    "state": "sent",
                    "sent_at": estop.sent_at,
                    "ack_expected": False,
                    "acked_at": None,
                    "nacked_at": None,
                    "timed_out_at": None,
                    "nack_error_code": None,
                    "control_id": None,
                    "control_name": None,
                    "requested_state": None,
                },
            },
        ]

    asyncio.run(run())

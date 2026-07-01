import asyncio
import socket
from types import SimpleNamespace
from typing import Any, cast

import orjson

from libqretprop.qlcp.config_parser import parse_config
from libqretprop.qlcp.enums import ControlState, DeviceStatus, ErrorCode, PacketType
from libqretprop.qlcp.packets import (
    AckPacket,
    ConfigPacket,
    ControlStatus,
    NackPacket,
    SimplePacket,
    StatusPacket,
)
from libqretprop.runtime.command_tracker import CommandLifecycle, CommandTracker
from libqretprop.runtime.esp_connection_runtime import ESPConnectionRuntime, ESPDeviceSession
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


def _make_session(
    runtime: ESPConnectionRuntime,
    *,
    address: str = "10.0.0.2",
    connection_key: str = "conn-a",
    name: str = "TEST-DEVICE",
) -> ESPDeviceSession:
    config = parse_config(_make_config(name=name))
    session = SimpleNamespace(
        address=address,
        connection_key=connection_key,
        name=config.name,
        type=config.device_type,
        qlcp_config=config,
        controls={control.name.upper(): control for control in config.controls_by_id.values()},
        monitor_task=None,
        heartbeat_task=None,
        driver=FakeDriver(),
        last_sync_time=None,
        missed_heartbeat_count=0,
        HEARTBEAT_ACK_MISS_LIMIT=3,
        is_connected=True,
    )

    def control_name_for_id(control_id: int | None) -> str | None:
        if control_id is None:
            return None
        control = config.controls_by_id.get(control_id)
        return None if control is None else control.name

    def record_heartbeat_ack(command: object | None) -> None:
        if command is not None:
            session.missed_heartbeat_count = 0

    def register_missed_heartbeat() -> bool:
        session.missed_heartbeat_count += 1
        return session.missed_heartbeat_count >= session.HEARTBEAT_ACK_MISS_LIMIT

    def close() -> None:
        session.is_connected = False

    session.control_name_for_id = control_name_for_id
    session.record_heartbeat_ack = record_heartbeat_ack
    session.register_missed_heartbeat = register_missed_heartbeat
    session.close = close
    return cast(ESPDeviceSession, session)


def test_runtime_registers_valid_device() -> None:
    async def run() -> None:
        runtime, _tracker, state, stream = _make_runtime()
        server_sock, peer_sock = socket.socketpair()
        server_sock.setblocking(False)
        peer_sock.setblocking(False)

        try:
            session = await runtime.register_configured_device(
                server_sock,
                "10.0.0.2",
                _make_config(),
                config_sequence=12,
            )

            assert isinstance(session, ESPDeviceSession)
            assert session.connection_key == "esp-1"
            assert session.monitor_task is not None
            assert session.heartbeat_task is not None
            assert runtime.devices.by_address("10.0.0.2") is session
            assert state.snapshot()["devices"][0]["name"] == "TEST-DEVICE"
            assert stream.events[0]["type"] == "device.registered"
        finally:
            runtime.close_all()
            peer_sock.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_accept_connection_registers_device_on_config() -> None:
    async def run() -> None:
        runtime, _tracker, state, stream = _make_runtime()
        server_sock, peer_sock = socket.socketpair()
        server_sock.setblocking(False)
        peer_sock.setblocking(False)

        try:
            peer_sock.sendall(ConfigPacket.create(orjson.dumps(_make_config()).decode("utf-8")).encode())

            session = await runtime.accept_connection(server_sock, "10.0.0.2")

            assert isinstance(session, ESPDeviceSession)
            assert runtime.devices.by_address("10.0.0.2") is session
            assert state.snapshot()["devices"][0]["name"] == "TEST-DEVICE"
            assert stream.events[0]["type"] == "device.registered"
        finally:
            runtime.close_all()
            peer_sock.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_accept_connection_closes_socket_when_peer_disconnects_before_config() -> None:
    async def run() -> None:
        runtime, _tracker, _state, _stream = _make_runtime()
        server_sock, peer_sock = socket.socketpair()
        server_sock.setblocking(False)
        peer_sock.setblocking(False)
        peer_sock.close()  # peer gone before sending CONFIG

        result = await runtime.accept_connection(server_sock, "10.0.0.2")

        assert result is None
        assert runtime.devices.by_address("10.0.0.2") is None
        assert server_sock.fileno() == -1  # accept_connection closed the socket

    asyncio.run(run())


def test_accept_connection_closes_socket_on_non_config_first_packet() -> None:
    async def run() -> None:
        runtime, _tracker, _state, _stream = _make_runtime()
        server_sock, peer_sock = socket.socketpair()
        server_sock.setblocking(False)
        peer_sock.setblocking(False)

        try:
            # A device-sent, server-decodable packet that is not CONFIG.
            peer_sock.sendall(AckPacket.create(PacketType.HEARTBEAT, ack_sequence=1).encode())

            result = await runtime.accept_connection(server_sock, "10.0.0.2")

            assert result is None
            assert runtime.devices.by_address("10.0.0.2") is None
            assert server_sock.fileno() == -1
        finally:
            peer_sock.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_runtime_replaces_existing_device_and_fails_pending_commands() -> None:
    runtime, tracker, state, stream = _make_runtime()
    old_device = _make_session(runtime, address="10.0.0.2", connection_key="conn-old")
    other_device = _make_session(runtime, address="10.0.0.4", connection_key="conn-other", name="OTHER")
    runtime.devices.register(old_device)
    runtime.devices.register(other_device)
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

    assert runtime.devices.by_address(old_device.address) is None
    assert runtime.devices.by_address(other_device.address) is other_device
    assert pending.state == CommandLifecycle.TIMED_OUT
    assert tracker.pending == ()
    assert stream.events[-1]["type"] == "device.disconnected"


def test_runtime_removal_marks_device_disconnected() -> None:
    runtime, _tracker, state, stream = _make_runtime()
    device = _make_session(runtime)
    runtime.devices.register(device)
    state.register_device(device)

    runtime.remove_device(device)

    assert runtime.devices.by_address(device.address) is None
    assert state.snapshot()["devices"][0]["connected"] is False
    assert stream.events[-1]["type"] == "device.disconnected"


def test_runtime_ack_routes_through_tracker_and_records_accepted_control_state() -> None:
    runtime, tracker, state, stream = _make_runtime()
    device = _make_session(runtime)
    runtime.devices.register(device)
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
    control = state.snapshot()["devices"][0]["controls"][0]
    assert control["accepted_state"] == "OPEN"
    assert control["reported_state"] is None
    assert [event["type"] for event in stream.events[-2:]] == ["command.acked", "control.accepted"]


def test_runtime_nack_routes_through_tracker_without_control_update() -> None:
    runtime, tracker, state, stream = _make_runtime()
    device = _make_session(runtime)
    runtime.devices.register(device)
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
    assert state.snapshot()["devices"][0]["controls"][0]["reported_state"] is None
    assert stream.events[-1]["type"] == "command.nacked"


def test_runtime_status_updates_reported_control_state() -> None:
    runtime, _tracker, state, stream = _make_runtime()
    device = _make_session(runtime)
    runtime.devices.register(device)
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

    assert state.snapshot()["devices"][0]["controls"][0]["reported_state"] == "OPEN"
    assert stream.events[-1]["type"] == "control.updated"


def test_runtime_command_visibility_policy_for_status_request_and_estop() -> None:
    async def run() -> None:
        runtime, tracker, _state, stream = _make_runtime()
        device = _make_session(runtime)

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


def test_status_packet_with_no_controls_does_not_error(caplog: Any) -> None:
    import logging

    runtime, _tracker, state, stream = _make_runtime()
    device = _make_session(runtime)
    runtime.devices.register(device)
    state.register_device(device)

    empty_status = StatusPacket(
        sequence=1,
        timestamp=0,
        status=DeviceStatus.ACTIVE,
        control_states=[],  # sensors-only board
    )

    with caplog.at_level(logging.ERROR):
        asyncio.run(runtime.handle_packet(device, empty_status))

    # Must not log an "unexpected packet type" error.
    assert not any("unexpected packet type" in record.message.lower() for record in caplog.records), (
        f"Unexpected error log: {[r.message for r in caplog.records]}"
    )


def test_remove_device_cleanup_before_mark_disconnected() -> None:
    close_called_at: list[str] = []

    runtime, _tracker, state, stream = _make_runtime()
    device = _make_session(runtime)
    runtime.devices.register(device)
    state.register_device(device)

    # Patch cleanup_device and mark_disconnected to record call order.
    original_cleanup = runtime.cleanup_device
    original_mark = runtime.system_state.mark_disconnected

    def recording_cleanup(session: Any, *, reason: str = "connection_cleanup") -> None:
        close_called_at.append("cleanup")
        original_cleanup(session, reason=reason)

    def recording_mark(session: Any) -> Any:
        close_called_at.append("mark_disconnected")
        return original_mark(session)

    runtime.cleanup_device = recording_cleanup  # type: ignore[assignment]
    runtime.system_state.mark_disconnected = recording_mark  # type: ignore[assignment]

    runtime.remove_device(device)

    assert close_called_at == ["cleanup", "mark_disconnected"], f"Wrong teardown order: {close_called_at}"


def test_close_all_cleanup_before_mark_disconnected() -> None:
    close_called_at: list[str] = []

    runtime, _tracker, state, stream = _make_runtime()
    device = _make_session(runtime)
    runtime.devices.register(device)
    state.register_device(device)

    original_cleanup = runtime.cleanup_device
    original_mark = runtime.system_state.mark_disconnected

    def recording_cleanup(session: Any, *, reason: str = "connection_cleanup") -> None:
        close_called_at.append("cleanup")
        original_cleanup(session, reason=reason)

    def recording_mark(session: Any) -> Any:
        close_called_at.append("mark_disconnected")
        return original_mark(session)

    runtime.cleanup_device = recording_cleanup  # type: ignore[assignment]
    runtime.system_state.mark_disconnected = recording_mark  # type: ignore[assignment]

    runtime.close_all()

    assert close_called_at == ["cleanup", "mark_disconnected"], f"Wrong teardown order in close_all: {close_called_at}"
    assert runtime.devices.snapshot_by_address() == {}, "Devices registry was not cleared"


def test_disconnection_metric_recorded_on_remove_device() -> None:
    from libqretprop.runtime.metrics import Metrics

    tracker = CommandTracker()
    state = SystemState(command_tracker=tracker)
    stream = FakeStateStream()
    metrics = Metrics()
    runtime = ESPConnectionRuntime(
        command_tracker=tracker,
        system_state=state,
        state_stream=stream,
        metrics=metrics,
    )
    device = _make_session(runtime)
    runtime.devices.register(device)
    state.register_device(device)

    runtime.remove_device(device)

    metrics_dict = metrics.to_dict()
    device_lifecycle = metrics_dict["device_lifecycle"]
    assert isinstance(device_lifecycle, dict)
    assert device_lifecycle["disconnections_total"] == {"connection_cleanup": 1}


def test_runtime_monitor_routes_packets_to_packet_handler() -> None:
    async def run() -> None:
        packet_seen = asyncio.Event()
        packets: list[object] = []
        runtime, _tracker, _state, _stream = _make_runtime()

        async def recording_handle_packet(session: ESPDeviceSession, packet: object) -> None:
            packets.append(packet)
            packet_seen.set()

        session_sock, peer_sock = socket.socketpair()
        session_sock.setblocking(False)
        peer_sock.setblocking(False)
        session = ESPDeviceSession(
            session_sock,
            "10.0.0.2",
            _make_config(),
            connection_key="conn-a",
        )
        runtime.handle_packet = recording_handle_packet  # type: ignore[method-assign]
        task = asyncio.create_task(runtime._monitor_session(session))

        try:
            loop = asyncio.get_running_loop()
            packet = AckPacket.create(PacketType.HEARTBEAT, ack_sequence=7)
            await loop.sock_sendall(peer_sock, packet.encode())
            await asyncio.wait_for(packet_seen.wait(), timeout=1)

            assert isinstance(packets[0], AckPacket)
            assert packets[0].ack_packet_type == PacketType.HEARTBEAT
            assert packets[0].ack_sequence == 7
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            session_sock.close()
            peer_sock.close()

    asyncio.run(run())

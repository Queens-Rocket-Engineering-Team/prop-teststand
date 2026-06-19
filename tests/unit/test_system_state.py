from types import SimpleNamespace
from typing import Any, cast

from libqretprop.qlcp.config_parser import parse_config
from libqretprop.qlcp.enums import ControlState, ErrorCode, PacketType
from libqretprop.runtime.command_tracker import CommandRecord, CommandTracker
from libqretprop.state.system_state import SystemState


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


def _make_device(
    *,
    address: str = "10.0.0.2",
    name: str = "TEST-DEVICE",
    connection_key: str = "conn-a",
    missed_heartbeat_acks: int = 0,
) -> Any:
    config = parse_config(_make_config(name=name))
    return SimpleNamespace(
        address=address,
        connection_key=connection_key,
        name=config.name,
        type=config.device_type,
        qlcp_config=config,
        last_sync_time=None,
        is_responsive=True,
        _missed_heartbeat_acks=missed_heartbeat_acks,
    )


def _make_state() -> tuple[SystemState, CommandTracker]:
    tracker = CommandTracker()
    return SystemState(command_tracker=tracker), tracker


def _mark_sent(
    tracker: CommandTracker,
    device: Any,
    *,
    packet_type: PacketType = PacketType.CONTROL,
    sequence: int = 12,
    now: float = 10.0,
    control_id: int | None = None,
    control_name: str | None = None,
    requested_state: ControlState | None = None,
    ack_expected: bool | None = None,
) -> CommandRecord:
    return tracker.mark_sent(
        connection_key=device.connection_key,
        device_name=device.name,
        device_address=device.address,
        packet_type=packet_type,
        packet_sequence=sequence,
        now=now,
        control_id=control_id,
        control_name=control_name,
        requested_state=requested_state,
        ack_expected=ack_expected,
    )


def test_register_device_produces_expected_snapshot() -> None:
    state, _ = _make_state()
    device = _make_device()

    event = state.register_device(device)
    snapshot = state.snapshot()

    assert event["type"] == "device.registered"
    assert event["state_version"] == 1
    assert state.state_version == 1
    assert snapshot.state_version == 1
    assert len(snapshot.devices) == 1

    device_snapshot = snapshot.devices[0]
    assert device_snapshot.name == "TEST-DEVICE"
    assert device_snapshot.device_type == "Sensor Monitor"
    assert device_snapshot.connected is True
    assert device_snapshot.address == "10.0.0.2"
    assert device_snapshot.sensors[0].name == "TC1"
    assert device_snapshot.sensors[0].unit == "CELSIUS"
    assert device_snapshot.controls[0].name == "VALVE1"
    assert device_snapshot.controls[0].reported_state is None
    assert device_snapshot.heartbeat.state == "ok"


def test_replacing_device_with_same_name_updates_snapshot() -> None:
    state, _ = _make_state()
    old_device = _make_device(address="10.0.0.2", name="TEST-DEVICE", connection_key="conn-a")
    new_device = _make_device(address="10.0.0.3", name="TEST-DEVICE", connection_key="conn-b")

    state.register_device(old_device)
    state.register_device(new_device)
    snapshot = state.snapshot()

    assert len(snapshot.devices) == 1
    assert snapshot.devices[0].address == "10.0.0.3"


def test_old_connection_disconnect_does_not_disconnect_replaced_device() -> None:
    state, _ = _make_state()
    old_device = _make_device(address="10.0.0.2", name="TEST-DEVICE", connection_key="conn-a")
    new_device = _make_device(address="10.0.0.3", name="TEST-DEVICE", connection_key="conn-b")

    state.register_device(old_device)
    state.register_device(new_device)
    state.mark_disconnected(old_device)
    snapshot = state.snapshot()

    assert len(snapshot.devices) == 1
    assert snapshot.devices[0].address == "10.0.0.3"
    assert snapshot.devices[0].connected is True


def test_old_connection_with_same_address_does_not_disconnect_replaced_device() -> None:
    state, _ = _make_state()
    old_device = _make_device(address="10.0.0.2", name="TEST-DEVICE", connection_key="conn-a")
    new_device = _make_device(address="10.0.0.2", name="TEST-DEVICE", connection_key="conn-b")

    state.register_device(old_device)
    state.register_device(new_device)
    state.mark_disconnected(old_device)
    snapshot = state.snapshot()

    assert len(snapshot.devices) == 1
    assert snapshot.devices[0].address == "10.0.0.2"
    assert snapshot.devices[0].connected is True


def test_status_update_changes_reported_control_state() -> None:
    state, _ = _make_state()
    device = _make_device()

    state.register_device(device)
    event = state.update_control_state(device, 0, ControlState.OPEN, now=42.0)
    snapshot = state.snapshot()

    assert event is not None
    assert event["type"] == "control.updated"
    assert event["state_version"] == 2
    assert event["device_name"] == "TEST-DEVICE"
    assert event["control_id"] == 0
    assert event["control_name"] == "VALVE1"
    assert event["reported_state"] == "OPEN"
    control = snapshot.devices[0].controls[0]
    assert control.reported_state == "OPEN"
    assert control.reported_timestamp == 42.0


def test_status_update_does_not_register_unconnected_device() -> None:
    state, _ = _make_state()
    device = _make_device()

    event = state.update_control_state(device, 0, ControlState.OPEN, now=42.0)
    snapshot = state.snapshot()

    assert event is None
    assert snapshot.state_version == 0
    assert snapshot.devices == []


def test_old_connection_status_does_not_update_replaced_device() -> None:
    state, _ = _make_state()
    old_device = _make_device(address="10.0.0.2", name="TEST-DEVICE", connection_key="conn-a")
    new_device = _make_device(address="10.0.0.3", name="TEST-DEVICE", connection_key="conn-b")

    state.register_device(old_device)
    state.register_device(new_device)
    state.update_control_state(old_device, 0, ControlState.OPEN, now=42.0)
    snapshot = state.snapshot()

    control = snapshot.devices[0].controls[0]
    assert control.reported_state is None


def test_pending_command_tracker_data_appears_in_snapshot() -> None:
    state, tracker = _make_state()
    device = _make_device()
    state.register_device(device)

    command = _mark_sent(
        tracker,
        device,
        control_id=0,
        control_name="VALVE1",
        requested_state=ControlState.CLOSED,
    )

    snapshot = state.snapshot()

    assert snapshot.commands.pending[0].command_id == command.command_id
    assert snapshot.commands.pending[0].connection_key == "conn-a"
    assert snapshot.commands.pending[0].device_name == "TEST-DEVICE"
    assert snapshot.commands.pending[0].packet_type == "CONTROL"
    assert snapshot.commands.pending[0].state == "sent"
    assert snapshot.commands.pending[0].ack_expected is True
    assert snapshot.commands.pending[0].control_name == "VALVE1"
    assert snapshot.commands.pending[0].requested_state == "CLOSED"
    assert snapshot.commands.recent == []
    assert snapshot.devices[0].controls[0].pending_command_id == command.command_id


def test_recent_completed_command_tracker_data_appears_in_snapshot() -> None:
    state, tracker = _make_state()
    device = _make_device()
    state.register_device(device)

    command = _mark_sent(
        tracker,
        device,
        control_id=0,
        control_name="VALVE1",
        requested_state=ControlState.CLOSED,
    )
    tracker.mark_acked(device.connection_key, PacketType.CONTROL, 12, now=11.0)

    snapshot = state.snapshot()

    assert snapshot.commands.pending == []
    assert snapshot.commands.recent[0].command_id == command.command_id
    assert snapshot.commands.recent[0].connection_key == "conn-a"
    assert snapshot.commands.recent[0].ack_expected is True
    assert snapshot.commands.recent[0].control_name == "VALVE1"
    assert snapshot.commands.recent[0].state == "acked"


def test_heartbeat_commands_are_summarized_not_listed_in_snapshot() -> None:
    state, tracker = _make_state()
    device = _make_device()
    state.register_device(device)

    _mark_sent(tracker, device, packet_type=PacketType.HEARTBEAT, now=10.0)
    tracker.mark_acked(device.connection_key, PacketType.HEARTBEAT, 12, now=11.0)

    snapshot = state.snapshot()

    assert snapshot.commands.pending == []
    assert snapshot.commands.recent == []
    assert snapshot.devices[0].heartbeat.state == "ok"
    assert snapshot.devices[0].heartbeat.last_sent_time == 10.0
    assert snapshot.devices[0].heartbeat.last_ack_time == 11.0
    assert snapshot.devices[0].heartbeat.pending is False


def test_pending_heartbeat_is_summarized_not_listed_in_snapshot() -> None:
    state, tracker = _make_state()
    device = _make_device()
    state.register_device(device)

    _mark_sent(tracker, device, packet_type=PacketType.HEARTBEAT, now=10.0)

    snapshot = state.snapshot()

    assert snapshot.commands.pending == []
    assert snapshot.devices[0].heartbeat.pending is True
    assert snapshot.devices[0].heartbeat.last_sent_time == 10.0


def test_missed_heartbeat_state_is_summarized() -> None:
    state, tracker = _make_state()
    device = _make_device(missed_heartbeat_acks=2)
    state.register_device(device)

    _mark_sent(tracker, device, packet_type=PacketType.HEARTBEAT, now=10.0)
    tracker.expire_pending(now=21.0, timeout_s=10.0)

    snapshot = state.snapshot()

    assert snapshot.devices[0].heartbeat.state == "missed"
    assert snapshot.devices[0].heartbeat.consecutive_misses == 2
    assert snapshot.devices[0].heartbeat.last_timeout_time == 21.0


def test_disconnected_device_is_marked_disconnected() -> None:
    state, _ = _make_state()
    device = _make_device()

    state.register_device(device)
    event = state.mark_disconnected(device)
    snapshot = state.snapshot()

    assert event is not None
    assert event["type"] == "device.disconnected"
    assert event["state_version"] == 2
    assert event["device_name"] == "TEST-DEVICE"
    assert snapshot.devices[0].connected is False
    assert snapshot.devices[0].heartbeat.state == "disconnected"


def test_command_lifecycle_events_increment_state_version() -> None:
    state, tracker = _make_state()
    device = _make_device()
    state.register_device(device)
    command = _mark_sent(
        tracker,
        device,
        control_id=0,
        control_name="VALVE1",
        requested_state=ControlState.CLOSED,
    )

    sent_event = state.record_command_sent(command)
    tracker.mark_acked(device.connection_key, PacketType.CONTROL, 12, now=11.0)
    acked_event = state.record_command_acked(command)

    assert sent_event is not None
    assert sent_event["type"] == "command.sent"
    assert sent_event["state_version"] == 2
    sent_payload = cast(dict[str, object], sent_event["command"])
    assert sent_payload["ack_expected"] is True
    assert sent_payload["control_name"] == "VALVE1"
    assert acked_event is not None
    assert acked_event["type"] == "command.acked"
    assert acked_event["state_version"] == 3
    acked_payload = cast(dict[str, object], acked_event["command"])
    assert acked_payload["ack_expected"] is True
    assert acked_payload["control_name"] == "VALVE1"
    assert state.state_version == 3


def test_command_nack_and_timeout_events_include_command_state() -> None:
    state, tracker = _make_state()
    device = _make_device()
    state.register_device(device)
    nacked_command = _mark_sent(
        tracker,
        device,
        sequence=12,
        control_id=0,
        control_name="VALVE1",
    )
    timed_out_command = _mark_sent(
        tracker,
        device,
        sequence=13,
        control_id=0,
        control_name="VALVE1",
    )

    tracker.mark_nacked(
        device.connection_key,
        PacketType.CONTROL,
        12,
        ErrorCode.INVALID_ID,
        now=11.0,
    )
    nacked_event = state.record_command_nacked(nacked_command)
    tracker.expire_pending(now=25.0, timeout_s=10.0)
    timed_out_event = state.record_command_timed_out(timed_out_command)

    assert nacked_event is not None
    assert nacked_event["type"] == "command.nacked"
    nacked_payload = cast(dict[str, object], nacked_event["command"])
    assert nacked_payload["state"] == "nacked"
    assert nacked_payload["control_name"] == "VALVE1"
    assert nacked_payload["nack_error_code"] == "INVALID_ID"
    assert timed_out_event is not None
    assert timed_out_event["type"] == "command.timed_out"
    timed_out_payload = cast(dict[str, object], timed_out_event["command"])
    assert timed_out_payload["state"] == "timed_out"
    assert timed_out_payload["control_name"] == "VALVE1"


def test_heartbeat_event_summarizes_heartbeat_state() -> None:
    state, tracker = _make_state()
    device = _make_device()
    state.register_device(device)
    heartbeat = _mark_sent(tracker, device, packet_type=PacketType.HEARTBEAT, now=10.0)

    event = state.record_command_sent(heartbeat)

    assert event is not None
    assert event["type"] == "heartbeat.updated"
    assert event["state_version"] == 2
    assert event["device_name"] == "TEST-DEVICE"
    assert event["heartbeat"] == {
        "state": "ok",
        "last_sent_time": 10.0,
        "last_ack_time": None,
        "pending": True,
        "consecutive_misses": 0,
        "last_timeout_time": None,
    }


def test_estop_commands_are_operator_visible_without_pending_ack() -> None:
    state, tracker = _make_state()
    device = _make_device()
    state.register_device(device)
    estop = _mark_sent(tracker, device, packet_type=PacketType.ESTOP)

    sent_event = state.record_command_sent(estop)
    snapshot = state.snapshot()
    expired = tracker.expire_pending(now=25.0, timeout_s=10.0)

    assert estop.ack_expected is False
    assert sent_event is not None
    assert sent_event["type"] == "command.sent"
    payload = cast(dict[str, object], sent_event["command"])
    assert payload["packet_type"] == "ESTOP"
    assert payload["ack_expected"] is False
    assert snapshot.commands.pending == []
    assert snapshot.commands.recent[0].command_id == estop.command_id
    assert snapshot.commands.recent[0].packet_type == "ESTOP"
    assert snapshot.commands.recent[0].ack_expected is False
    assert expired == []


def test_status_request_commands_are_not_operator_visible() -> None:
    state, tracker = _make_state()
    device = _make_device()
    state.register_device(device)
    status_request = _mark_sent(tracker, device, packet_type=PacketType.STATUS_REQUEST)

    sent_event = state.record_command_sent(status_request)
    pending_snapshot = state.snapshot()
    tracker.mark_acked(device.connection_key, PacketType.STATUS_REQUEST, 12, now=11.0)
    acked_event = state.record_command_acked(status_request)
    completed_snapshot = state.snapshot()

    assert sent_event is None
    assert acked_event is None
    assert status_request.ack_expected is False
    assert state.state_version == 1
    assert tracker.pending == ()
    assert pending_snapshot.commands.pending == []
    assert completed_snapshot.commands.recent == []


def test_snapshot_serializes_to_dict() -> None:
    state, _ = _make_state()
    device = _make_device()

    state.register_device(device)
    snapshot_dict = state.to_dict()

    assert snapshot_dict["devices"][0]["name"] == "TEST-DEVICE"
    assert snapshot_dict["state_version"] == 1
    assert snapshot_dict["commands"] == {"pending": [], "recent": []}

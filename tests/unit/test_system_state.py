from types import SimpleNamespace
from typing import Any

from libqretprop.qlcp.config_parser import parse_config
from libqretprop.qlcp.enums import ControlState, PacketType
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
    requested_state: ControlState | None = None,
) -> CommandRecord:
    return tracker.mark_sent(
        connection_key=device.connection_key,
        device_name=device.name,
        device_address=device.address,
        packet_type=packet_type,
        packet_sequence=sequence,
        now=now,
        control_id=control_id,
        requested_state=requested_state,
    )


def test_register_device_produces_expected_snapshot() -> None:
    state, _ = _make_state()
    device = _make_device()

    state.register_device(device)
    snapshot = state.snapshot()

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
    state.update_control_state(device, 0, ControlState.OPEN, now=42.0)
    snapshot = state.snapshot()

    control = snapshot.devices[0].controls[0]
    assert control.reported_state == "OPEN"
    assert control.reported_timestamp == 42.0


def test_status_update_does_not_register_unconnected_device() -> None:
    state, _ = _make_state()
    device = _make_device()

    state.update_control_state(device, 0, ControlState.OPEN, now=42.0)
    snapshot = state.snapshot()

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
        requested_state=ControlState.CLOSED,
    )

    snapshot = state.snapshot()

    assert snapshot.commands.pending[0].command_id == command.command_id
    assert snapshot.commands.pending[0].connection_key == "conn-a"
    assert snapshot.commands.pending[0].device_name == "TEST-DEVICE"
    assert snapshot.commands.pending[0].packet_type == "CONTROL"
    assert snapshot.commands.pending[0].state == "sent"
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
        requested_state=ControlState.CLOSED,
    )
    tracker.mark_acked(device.connection_key, PacketType.CONTROL, 12, now=11.0)

    snapshot = state.snapshot()

    assert snapshot.commands.pending == []
    assert snapshot.commands.recent[0].command_id == command.command_id
    assert snapshot.commands.recent[0].connection_key == "conn-a"
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
    state.mark_disconnected(device)
    snapshot = state.snapshot()

    assert snapshot.devices[0].connected is False
    assert snapshot.devices[0].heartbeat.state == "disconnected"


def test_snapshot_serializes_to_dict() -> None:
    state, _ = _make_state()
    device = _make_device()

    state.register_device(device)
    snapshot_dict = state.to_dict()

    assert snapshot_dict["devices"][0]["name"] == "TEST-DEVICE"
    assert snapshot_dict["commands"] == {"pending": [], "recent": []}

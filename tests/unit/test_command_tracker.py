from libqretprop.qlcp.enums import ControlState, ErrorCode, PacketType
from libqretprop.runtime.command_tracker import CommandLifecycle, CommandTracker


def test_mark_sent_creates_pending_command() -> None:
    tracker = CommandTracker()

    record = tracker.mark_sent(
        "device-a",
        PacketType.CONTROL,
        7,
        now=10.0,
        control_id=2,
        requested_state=ControlState.OPEN,
    )

    assert record.command_id == 1
    assert record.device_id == "device-a"
    assert record.packet_type == PacketType.CONTROL
    assert record.packet_sequence == 7
    assert record.sent_at == 10.0
    assert record.state == CommandLifecycle.SENT
    assert record.control_id == 2
    assert record.requested_state == ControlState.OPEN
    assert tracker.get_pending("device-a", PacketType.CONTROL, 7) is record


def test_ack_resolves_correct_command() -> None:
    tracker = CommandTracker()
    first = tracker.mark_sent("device-a", PacketType.CONTROL, 7, now=10.0)
    second = tracker.mark_sent("device-a", PacketType.STREAM_START, 8, now=11.0)

    acked = tracker.mark_acked("device-a", PacketType.CONTROL, 7, now=12.0)

    assert acked is first
    assert first.state == CommandLifecycle.ACKED
    assert first.acked_at == 12.0
    assert tracker.get_pending("device-a", PacketType.CONTROL, 7) is None
    assert tracker.get_pending("device-a", PacketType.STREAM_START, 8) is second


def test_nack_resolves_correct_command() -> None:
    tracker = CommandTracker()
    record = tracker.mark_sent("device-a", PacketType.CONTROL, 7, now=10.0)

    nacked = tracker.mark_nacked(
        "device-a",
        PacketType.CONTROL,
        7,
        ErrorCode.INVALID_ID,
        now=12.0,
    )

    assert nacked is record
    assert record.state == CommandLifecycle.NACKED
    assert record.nacked_at == 12.0
    assert record.nack_error_code == ErrorCode.INVALID_ID
    assert tracker.get_pending("device-a", PacketType.CONTROL, 7) is None


def test_unknown_ack_or_nack_returns_none() -> None:
    tracker = CommandTracker()

    assert tracker.mark_acked("unknown", PacketType.CONTROL, 7, now=12.0) is None
    assert tracker.mark_nacked("unknown", PacketType.CONTROL, 7, ErrorCode.INVALID_ID, now=12.0) is None


def test_same_sequence_on_different_devices_does_not_collide() -> None:
    tracker = CommandTracker()
    first = tracker.mark_sent("device-a", PacketType.CONTROL, 7, now=10.0)
    second = tracker.mark_sent("device-b", PacketType.CONTROL, 7, now=10.0)

    acked = tracker.mark_acked("device-b", PacketType.CONTROL, 7, now=12.0)

    assert acked is second
    assert first.state == CommandLifecycle.SENT
    assert tracker.get_pending("device-a", PacketType.CONTROL, 7) is first
    assert tracker.get_pending("device-b", PacketType.CONTROL, 7) is None


def test_same_sequence_with_different_packet_types_does_not_collide() -> None:
    tracker = CommandTracker()
    control = tracker.mark_sent("device-a", PacketType.CONTROL, 7, now=10.0)
    stream_start = tracker.mark_sent("device-a", PacketType.STREAM_START, 7, now=10.0)

    acked = tracker.mark_acked("device-a", PacketType.STREAM_START, 7, now=12.0)

    assert acked is stream_start
    assert control.state == CommandLifecycle.SENT
    assert tracker.get_pending("device-a", PacketType.CONTROL, 7) is control
    assert tracker.get_pending("device-a", PacketType.STREAM_START, 7) is None


def test_timeout_marks_pending_command_timed_out() -> None:
    tracker = CommandTracker()
    expired = tracker.mark_sent("device-a", PacketType.CONTROL, 7, now=10.0)
    fresh = tracker.mark_sent("device-a", PacketType.STREAM_START, 8, now=18.0)

    expired_records = tracker.expire_pending(now=20.0, timeout_s=10.0)

    assert expired_records == [expired]
    assert expired.state == CommandLifecycle.TIMED_OUT
    assert expired.timed_out_at == 20.0
    assert fresh.state == CommandLifecycle.SENT
    assert tracker.get_pending("device-a", PacketType.CONTROL, 7) is None
    assert tracker.get_pending("device-a", PacketType.STREAM_START, 8) is fresh


def test_completed_command_remains_in_history() -> None:
    tracker = CommandTracker()
    record = tracker.mark_sent("device-a", PacketType.CONTROL, 7, now=10.0)

    tracker.mark_acked("device-a", PacketType.CONTROL, 7, now=12.0)

    assert tracker.get_record(record.command_id) is record
    assert record in tracker.records

from typing import Any, cast

from libqretprop.qlcp.enums import ControlState, ErrorCode, PacketType
from libqretprop.runtime.command_tracker import CommandKey, CommandLifecycle, CommandRecord, CommandTracker
from libqretprop.runtime.metrics import Metrics


def _metrics_snapshot(metrics: Metrics) -> dict[str, Any]:
    return cast("dict[str, Any]", metrics.to_dict())


def _find_pending(
    tracker: CommandTracker,
    connection_key: str,
    packet_type: PacketType,
    packet_sequence: int,
) -> CommandRecord | None:
    key = CommandKey(connection_key, packet_type, packet_sequence)
    return next(
        (r for r in tracker.pending if r.key == key),
        None,
    )


def _mark_sent(
    tracker: CommandTracker,
    *,
    connection_key: str = "conn-a",
    device_name: str = "PANDA",
    device_address: str = "10.0.0.2",
    packet_type: PacketType = PacketType.CONTROL,
    sequence: int = 7,
    now: float = 10.0,
    control_id: int | None = None,
    control_name: str | None = None,
    requested_state: ControlState | None = None,
    ack_expected: bool | None = None,
) -> CommandRecord:
    return tracker.mark_sent(
        connection_key=connection_key,
        device_name=device_name,
        device_address=device_address,
        packet_type=packet_type,
        packet_sequence=sequence,
        now=now,
        control_id=control_id,
        control_name=control_name,
        requested_state=requested_state,
        ack_expected=ack_expected,
    )


def test_mark_sent_creates_pending_command() -> None:
    tracker = CommandTracker()

    record = _mark_sent(
        tracker,
        control_id=2,
        control_name="VALVE1",
        requested_state=ControlState.OPEN,
    )

    assert record.command_id == 1
    assert record.connection_key == "conn-a"
    assert record.device_name == "PANDA"
    assert record.device_address == "10.0.0.2"
    assert record.packet_type == PacketType.CONTROL
    assert record.packet_sequence == 7
    assert record.sent_at == 10.0
    assert record.ack_expected is True
    assert record.state == CommandLifecycle.SENT
    assert record.control_id == 2
    assert record.control_name == "VALVE1"
    assert record.requested_state == ControlState.OPEN
    assert _find_pending(tracker, "conn-a", PacketType.CONTROL, 7) is record


def test_ack_resolves_command_only_for_matching_connection_key() -> None:
    tracker = CommandTracker()
    first = _mark_sent(tracker, connection_key="conn-a", sequence=7, now=10.0)
    second = _mark_sent(tracker, connection_key="conn-b", sequence=7, now=11.0)

    acked = tracker.mark_acked("conn-b", PacketType.CONTROL, 7, now=12.0)

    assert acked is second
    assert second.state == CommandLifecycle.ACKED
    assert second.acked_at == 12.0
    assert first.state == CommandLifecycle.SENT
    assert _find_pending(tracker, "conn-a", PacketType.CONTROL, 7) is first
    assert _find_pending(tracker, "conn-b", PacketType.CONTROL, 7) is None


def test_ack_records_command_metrics() -> None:
    metrics = Metrics(time_fn=lambda: 100.0)
    tracker = CommandTracker(metrics=metrics)
    _mark_sent(tracker, packet_type=PacketType.CONTROL, sequence=7, now=10.0)

    tracker.mark_acked("conn-a", PacketType.CONTROL, 7, now=10.5)

    snapshot = _metrics_snapshot(metrics)
    assert snapshot["commands"]["outcomes_total"]["CONTROL"]["acked"] == 1
    assert snapshot["commands"]["rtt_seconds"]["CONTROL"]["count"] == 1
    assert snapshot["commands"]["rtt_seconds"]["CONTROL"]["last"] == 0.5


def test_nack_resolves_command_only_for_matching_connection_key() -> None:
    tracker = CommandTracker()
    first = _mark_sent(tracker, connection_key="conn-a", sequence=7, now=10.0)
    second = _mark_sent(tracker, connection_key="conn-b", sequence=7, now=11.0)

    nacked = tracker.mark_nacked(
        "conn-b",
        PacketType.CONTROL,
        7,
        ErrorCode.INVALID_ID,
        now=12.0,
    )

    assert nacked is second
    assert second.state == CommandLifecycle.NACKED
    assert second.nacked_at == 12.0
    assert second.nack_error_code == ErrorCode.INVALID_ID
    assert first.state == CommandLifecycle.SENT
    assert _find_pending(tracker, "conn-a", PacketType.CONTROL, 7) is first
    assert _find_pending(tracker, "conn-b", PacketType.CONTROL, 7) is None


def test_unknown_ack_or_nack_returns_none() -> None:
    tracker = CommandTracker()

    assert tracker.mark_acked("unknown", PacketType.CONTROL, 7, now=12.0) is None
    assert tracker.mark_nacked("unknown", PacketType.CONTROL, 7, ErrorCode.INVALID_ID, now=12.0) is None


def test_same_device_name_with_different_connection_keys_does_not_collide() -> None:
    tracker = CommandTracker()
    first = _mark_sent(
        tracker,
        connection_key="conn-a",
        device_name="PANDA",
        device_address="10.0.0.2",
        sequence=7,
        now=10.0,
    )
    second = _mark_sent(
        tracker,
        connection_key="conn-b",
        device_name="PANDA",
        device_address="10.0.0.2",
        sequence=7,
        now=11.0,
    )

    acked = tracker.mark_acked("conn-b", PacketType.CONTROL, 7, now=12.0)

    assert acked is second
    assert first.state == CommandLifecycle.SENT
    assert _find_pending(tracker, "conn-a", PacketType.CONTROL, 7) is first


def test_same_sequence_with_different_packet_types_does_not_collide() -> None:
    tracker = CommandTracker()
    control = _mark_sent(tracker, packet_type=PacketType.CONTROL, sequence=7, now=10.0)
    stream_start = _mark_sent(tracker, packet_type=PacketType.STREAM_START, sequence=7, now=10.0)

    acked = tracker.mark_acked("conn-a", PacketType.STREAM_START, 7, now=12.0)

    assert acked is stream_start
    assert control.state == CommandLifecycle.SENT
    assert _find_pending(tracker, "conn-a", PacketType.CONTROL, 7) is control
    assert _find_pending(tracker, "conn-a", PacketType.STREAM_START, 7) is None


def test_timeout_marks_pending_command_timed_out() -> None:
    tracker = CommandTracker()
    expired = _mark_sent(tracker, packet_type=PacketType.CONTROL, sequence=7, now=10.0)
    fresh = _mark_sent(tracker, packet_type=PacketType.STREAM_START, sequence=8, now=18.0)

    expired_records = tracker.expire_pending(now=20.0, timeout_s=10.0)

    assert expired_records == [expired]
    assert expired.state == CommandLifecycle.TIMED_OUT
    assert expired.timed_out_at == 20.0
    assert fresh.state == CommandLifecycle.SENT
    assert expired not in tracker.pending
    assert _find_pending(tracker, "conn-a", PacketType.STREAM_START, 8) is fresh


def test_timeout_can_be_scoped_to_one_connection() -> None:
    tracker = CommandTracker()
    expired = _mark_sent(tracker, connection_key="conn-a", sequence=7, now=10.0)
    other = _mark_sent(tracker, connection_key="conn-b", sequence=7, now=10.0)

    expired_records = tracker.expire_pending(now=21.0, timeout_s=10.0, connection_key="conn-a")

    assert expired_records == [expired]
    assert expired.state == CommandLifecycle.TIMED_OUT
    assert other.state == CommandLifecycle.SENT
    assert _find_pending(tracker, "conn-b", PacketType.CONTROL, 7) is other


def test_completed_operator_command_remains_in_recent_history() -> None:
    tracker = CommandTracker()
    record = _mark_sent(tracker)

    tracker.mark_acked("conn-a", PacketType.CONTROL, 7, now=12.0)

    assert record in tracker.recent_completed


def test_fire_and_forget_operator_command_is_recent_without_pending_ack() -> None:
    tracker = CommandTracker()

    record = _mark_sent(tracker, packet_type=PacketType.ESTOP)

    assert record.ack_expected is False
    assert tracker.pending == ()
    assert tracker.recent_completed == (record,)
    assert tracker.expire_pending(now=30.0, timeout_s=10.0) == []


def test_discard_removes_fire_and_forget_operator_command_from_recent_history() -> None:
    tracker = CommandTracker()
    record = _mark_sent(tracker, packet_type=PacketType.ESTOP)

    discarded = tracker.discard(record.command_id)

    assert discarded is record
    assert tracker.recent_completed == ()


def test_get_single_is_not_ack_expected_but_is_recent_history() -> None:
    tracker = CommandTracker()
    record = _mark_sent(tracker, packet_type=PacketType.GET_SINGLE)

    acked = tracker.mark_acked("conn-a", PacketType.GET_SINGLE, 7, now=12.0)

    assert record.ack_expected is False
    assert acked is None
    assert tracker.pending == ()
    assert record in tracker.recent_completed


def test_status_request_is_not_ack_expected_or_recent_history() -> None:
    tracker = CommandTracker()
    record = _mark_sent(tracker, packet_type=PacketType.STATUS_REQUEST)

    acked = tracker.mark_acked("conn-a", PacketType.STATUS_REQUEST, 7, now=12.0)

    assert record.ack_expected is False
    assert acked is None
    assert tracker.pending == ()
    assert record not in tracker.recent_completed


def test_recent_completed_history_is_bounded() -> None:
    tracker = CommandTracker(recent_completed_limit=2)
    first = _mark_sent(tracker, sequence=1, now=10.0)
    second = _mark_sent(tracker, sequence=2, now=11.0)
    third = _mark_sent(tracker, sequence=3, now=12.0)

    tracker.mark_acked("conn-a", PacketType.CONTROL, 1, now=13.0)
    tracker.mark_acked("conn-a", PacketType.CONTROL, 2, now=14.0)
    tracker.mark_acked("conn-a", PacketType.CONTROL, 3, now=15.0)

    assert first not in tracker.recent_completed
    assert tracker.recent_completed == (second, third)


def test_default_recent_completed_history_keeps_one_hundred_global_commands() -> None:
    tracker = CommandTracker()
    commands = [
        _mark_sent(tracker, sequence=sequence, now=float(sequence))
        for sequence in range(101)
    ]

    for sequence in range(101):
        tracker.mark_acked("conn-a", PacketType.CONTROL, sequence, now=float(sequence) + 1.0)

    assert len(tracker.recent_completed) == 100
    assert commands[0] not in tracker.recent_completed
    assert tracker.recent_completed[0] is commands[1]
    assert tracker.recent_completed[-1] is commands[-1]


def test_all_pending_commands_are_preserved_even_past_recent_limit() -> None:
    tracker = CommandTracker(recent_completed_limit=1)

    pending = [
        _mark_sent(tracker, sequence=sequence, now=float(sequence))
        for sequence in range(3)
    ]

    assert tracker.pending == tuple(pending)


def test_duplicate_pending_key_replaces_old_command_without_leak() -> None:
    tracker = CommandTracker()
    old = _mark_sent(tracker, sequence=7, now=10.0)
    new = _mark_sent(tracker, sequence=7, now=20.0)

    assert old.state == CommandLifecycle.TIMED_OUT
    assert old.timed_out_at == 20.0
    assert old.failure_reason == "duplicate_command_key"
    assert tracker.pending == (new,)
    assert _find_pending(tracker, "conn-a", PacketType.CONTROL, 7) is new
    assert old in tracker.recent_completed


def test_fail_connection_marks_pending_commands_for_that_connection_timed_out() -> None:
    tracker = CommandTracker()
    failed = _mark_sent(tracker, connection_key="conn-a", sequence=7, now=10.0)
    untouched = _mark_sent(tracker, connection_key="conn-b", sequence=7, now=10.0)

    failed_records = tracker.fail_connection("conn-a", now=20.0, reason="connection_cleanup")

    assert failed_records == [failed]
    assert failed.state == CommandLifecycle.TIMED_OUT
    assert failed.timed_out_at == 20.0
    assert failed.failure_reason == "connection_cleanup"
    assert untouched.state == CommandLifecycle.SENT
    assert failed not in tracker.pending
    assert untouched in tracker.pending


def test_fail_connection_prunes_maintenance_summaries_for_that_connection() -> None:
    tracker = CommandTracker()
    _mark_sent(tracker, connection_key="conn-a", packet_type=PacketType.HEARTBEAT, sequence=7, now=10.0)
    _mark_sent(tracker, connection_key="conn-b", packet_type=PacketType.HEARTBEAT, sequence=7, now=10.0)
    tracker.mark_acked("conn-a", PacketType.HEARTBEAT, 7, now=11.0)
    tracker.mark_acked("conn-b", PacketType.HEARTBEAT, 7, now=11.0)

    tracker.fail_connection("conn-a", now=20.0, reason="connection_cleanup")

    assert tracker.get_summary("conn-a", PacketType.HEARTBEAT) is None
    assert tracker.get_summary("conn-b", PacketType.HEARTBEAT) is not None


def test_maintenance_commands_update_summary_without_recent_history() -> None:
    tracker = CommandTracker()
    heartbeat = _mark_sent(tracker, packet_type=PacketType.HEARTBEAT, sequence=7, now=10.0)

    tracker.mark_acked("conn-a", PacketType.HEARTBEAT, 7, now=12.0)

    summary = tracker.get_summary("conn-a", PacketType.HEARTBEAT)
    assert summary is not None
    assert summary.connection_key == "conn-a"
    assert summary.device_name == "PANDA"
    assert summary.device_address == "10.0.0.2"
    assert summary.last_sent_at == 10.0
    assert summary.last_acked_at == 12.0
    assert summary.pending_count == 0
    assert heartbeat not in tracker.recent_completed


def test_maintenance_summaries_are_scoped_by_connection_key() -> None:
    tracker = CommandTracker()
    _mark_sent(tracker, connection_key="conn-a", packet_type=PacketType.HEARTBEAT, sequence=7, now=10.0)
    _mark_sent(tracker, connection_key="conn-b", packet_type=PacketType.HEARTBEAT, sequence=7, now=20.0)

    tracker.mark_acked("conn-b", PacketType.HEARTBEAT, 7, now=21.0)

    first_summary = tracker.get_summary("conn-a", PacketType.HEARTBEAT)
    second_summary = tracker.get_summary("conn-b", PacketType.HEARTBEAT)

    assert first_summary is not None
    assert second_summary is not None
    assert first_summary.pending_count == 1
    assert first_summary.last_acked_at is None
    assert second_summary.pending_count == 0
    assert second_summary.last_acked_at == 21.0


def test_maintenance_timeout_updates_summary() -> None:
    tracker = CommandTracker()
    _mark_sent(tracker, packet_type=PacketType.HEARTBEAT, sequence=7, now=10.0)

    tracker.expire_pending(now=21.0, timeout_s=10.0)

    summary = tracker.get_summary("conn-a", PacketType.HEARTBEAT)
    assert summary is not None
    assert summary.last_timed_out_at == 21.0
    assert summary.pending_count == 0

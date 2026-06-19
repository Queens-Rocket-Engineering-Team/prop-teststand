from __future__ import annotations
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from libqretprop.qlcp.enums import PacketType


if TYPE_CHECKING:
    from libqretprop.qlcp.enums import ControlState, ErrorCode


DEFAULT_RECENT_COMPLETED_LIMIT = 100
OPERATOR_VISIBLE_PACKET_TYPES = frozenset(
    {
        PacketType.CONTROL,
        PacketType.ESTOP,
        PacketType.STREAM_START,
        PacketType.STREAM_STOP,
        PacketType.GET_SINGLE,
    },
)
MAINTENANCE_PACKET_TYPES = frozenset(
    {
        PacketType.HEARTBEAT,
        PacketType.TIMESYNC,
    },
)
ACK_EXPECTED_PACKET_TYPES = frozenset(
    {
        PacketType.CONTROL,
        PacketType.STREAM_START,
        PacketType.STREAM_STOP,
        PacketType.GET_SINGLE,
        PacketType.HEARTBEAT,
        PacketType.TIMESYNC,
    },
)


class CommandLifecycle(StrEnum):
    """Lifecycle state for an outbound command packet."""

    SENT = "sent"
    ACKED = "acked"
    NACKED = "nacked"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class CommandKey:
    """ACK/NACK lookup key scoped to one TCP connection."""

    connection_key: str
    packet_type: PacketType
    packet_sequence: int


@dataclass
class CommandRecord:
    """Tracked lifecycle for a command sent over one device connection."""

    command_id: int
    connection_key: str
    device_name: str
    device_address: str
    packet_type: PacketType
    packet_sequence: int
    sent_at: float
    ack_expected: bool = True
    state: CommandLifecycle = CommandLifecycle.SENT
    acked_at: float | None = None
    nacked_at: float | None = None
    timed_out_at: float | None = None
    nack_error_code: ErrorCode | None = None
    failure_reason: str | None = None
    control_id: int | None = None
    control_name: str | None = None
    requested_state: ControlState | None = None

    @property
    def key(self) -> CommandKey:
        return CommandKey(
            connection_key=self.connection_key,
            packet_type=self.packet_type,
            packet_sequence=self.packet_sequence,
        )

    @property
    def is_pending(self) -> bool:
        return self.ack_expected and self.state == CommandLifecycle.SENT


@dataclass
class CommandSummary:
    """Compact lifecycle summary for maintenance/internal command types."""

    connection_key: str
    device_name: str
    device_address: str
    packet_type: PacketType
    last_sent_at: float | None = None
    last_acked_at: float | None = None
    last_nacked_at: float | None = None
    last_timed_out_at: float | None = None
    last_error_code: ErrorCode | None = None
    pending_count: int = 0


class CommandTracker:
    """Tracks outbound command packets until ACK, NACK, or timeout."""

    def __init__(
        self,
        *,
        recent_completed_limit: int = DEFAULT_RECENT_COMPLETED_LIMIT,
    ) -> None:
        if recent_completed_limit < 0:
            raise ValueError("recent_completed_limit must be non-negative")

        self._next_command_id = 1
        self._pending_records: dict[int, CommandRecord] = {}
        self._pending: dict[CommandKey, int] = {}
        self._recent_completed: deque[CommandRecord] = deque(maxlen=recent_completed_limit)
        self._summaries: dict[tuple[str, PacketType], CommandSummary] = {}

    @property
    def records(self) -> tuple[CommandRecord, ...]:
        return self.pending + self.recent_completed

    @property
    def pending(self) -> tuple[CommandRecord, ...]:
        return tuple(self._pending_records.values())

    @property
    def recent_completed(self) -> tuple[CommandRecord, ...]:
        return tuple(self._recent_completed)

    @property
    def maintenance_summaries(self) -> tuple[CommandSummary, ...]:
        return tuple(self._summaries.values())

    def get_summary(
        self,
        connection_key: str,
        packet_type: PacketType,
    ) -> CommandSummary | None:
        return self._summaries.get((connection_key, packet_type))

    def get_record(self, command_id: int) -> CommandRecord | None:
        pending_record = self._pending_records.get(command_id)
        if pending_record is not None:
            return pending_record

        for record in self._recent_completed:
            if record.command_id == command_id:
                return record
        return None

    def get_pending(
        self,
        connection_key: str,
        packet_type: PacketType,
        packet_sequence: int,
    ) -> CommandRecord | None:
        key = CommandKey(connection_key, packet_type, packet_sequence)
        command_id = self._pending.get(key)
        if command_id is None:
            return None
        return self._pending_records[command_id]

    def mark_sent(
        self,
        connection_key: str,
        device_name: str,
        device_address: str,
        packet_type: PacketType,
        packet_sequence: int,
        *,
        now: float | None = None,
        control_id: int | None = None,
        control_name: str | None = None,
        requested_state: ControlState | None = None,
        ack_expected: bool | None = None,
    ) -> CommandRecord:
        record = CommandRecord(
            command_id=self._next_command_id,
            connection_key=connection_key,
            device_name=device_name,
            device_address=device_address,
            packet_type=packet_type,
            packet_sequence=packet_sequence,
            sent_at=self._timestamp(now),
            ack_expected=self._ack_expected(packet_type) if ack_expected is None else ack_expected,
            control_id=control_id,
            control_name=control_name,
            requested_state=requested_state,
        )

        self._next_command_id += 1
        self._mark_sent_summary(record)
        if record.ack_expected:
            self._replace_duplicate_pending(record)
            self._pending_records[record.command_id] = record
            self._pending[record.key] = record.command_id
        else:
            self._complete_record(record)
        return record

    def mark_acked(
        self,
        connection_key: str,
        packet_type: PacketType,
        packet_sequence: int,
        *,
        now: float | None = None,
    ) -> CommandRecord | None:
        record = self._pop_pending(connection_key, packet_type, packet_sequence)
        if record is None:
            return None

        record.state = CommandLifecycle.ACKED
        record.acked_at = self._timestamp(now)
        self._complete_record(record)
        return record

    def mark_nacked(
        self,
        connection_key: str,
        packet_type: PacketType,
        packet_sequence: int,
        error_code: ErrorCode,
        *,
        now: float | None = None,
    ) -> CommandRecord | None:
        record = self._pop_pending(connection_key, packet_type, packet_sequence)
        if record is None:
            return None

        record.state = CommandLifecycle.NACKED
        record.nacked_at = self._timestamp(now)
        record.nack_error_code = error_code
        self._complete_record(record)
        return record

    def expire_pending(
        self,
        now: float,
        timeout_s: float,
        *,
        connection_key: str | None = None,
    ) -> list[CommandRecord]:
        expired: list[CommandRecord] = []

        for key, command_id in list(self._pending.items()):
            if connection_key is not None and key.connection_key != connection_key:
                continue

            record = self._pending_records[command_id]
            if now - record.sent_at < timeout_s:
                continue

            self._pending.pop(key)
            self._pending_records.pop(command_id)
            record.state = CommandLifecycle.TIMED_OUT
            record.timed_out_at = now
            self._complete_record(record)
            expired.append(record)

        return expired

    def fail_connection(
        self,
        connection_key: str,
        *,
        now: float | None = None,
        reason: str | None = None,
    ) -> list[CommandRecord]:
        failed: list[CommandRecord] = []
        timestamp = self._timestamp(now)

        for key, command_id in list(self._pending.items()):
            if key.connection_key != connection_key:
                continue

            record = self._pending_records.pop(command_id)
            self._pending.pop(key)
            record.state = CommandLifecycle.TIMED_OUT
            record.timed_out_at = timestamp
            record.failure_reason = reason
            self._complete_record(record)
            failed.append(record)

        self._remove_connection_summaries(connection_key)
        return failed

    def discard(self, command_id: int) -> CommandRecord | None:
        record = self._pending_records.pop(command_id, None)
        if record is not None:
            self._pending.pop(record.key, None)
            self._decrement_summary_pending(record)
            return record

        for recent_record in tuple(self._recent_completed):
            if recent_record.command_id != command_id:
                continue

            self._recent_completed.remove(recent_record)
            return recent_record

        return None

    def _pop_pending(
        self,
        connection_key: str,
        packet_type: PacketType,
        packet_sequence: int,
    ) -> CommandRecord | None:
        key = CommandKey(connection_key, packet_type, packet_sequence)
        command_id = self._pending.pop(key, None)
        if command_id is None:
            return None
        return self._pending_records.pop(command_id)

    def _replace_duplicate_pending(self, record: CommandRecord) -> None:
        existing_command_id = self._pending.pop(record.key, None)
        if existing_command_id is None:
            return

        existing_record = self._pending_records.pop(existing_command_id)
        existing_record.state = CommandLifecycle.TIMED_OUT
        existing_record.timed_out_at = record.sent_at
        existing_record.failure_reason = "duplicate_command_key"
        self._complete_record(existing_record)

    def _complete_record(self, record: CommandRecord) -> None:
        self._update_completed_summary(record)
        if record.packet_type in OPERATOR_VISIBLE_PACKET_TYPES:
            self._recent_completed.append(record)

    @staticmethod
    def _timestamp(now: float | None) -> float:
        return time.monotonic() if now is None else now

    def _mark_sent_summary(self, record: CommandRecord) -> None:
        summary = self._get_summary_for_record(record)
        if summary is None:
            return

        summary.device_name = record.device_name
        summary.device_address = record.device_address
        summary.last_sent_at = record.sent_at
        if record.ack_expected:
            summary.pending_count += 1

    def _update_completed_summary(self, record: CommandRecord) -> None:
        summary = self._get_summary_for_record(record)
        if summary is None:
            return

        self._decrement_summary_pending(record)
        if record.acked_at is not None:
            summary.last_acked_at = record.acked_at
        if record.nacked_at is not None:
            summary.last_nacked_at = record.nacked_at
            summary.last_error_code = record.nack_error_code
        if record.timed_out_at is not None:
            summary.last_timed_out_at = record.timed_out_at

    def _get_summary_for_record(self, record: CommandRecord) -> CommandSummary | None:
        if record.packet_type not in MAINTENANCE_PACKET_TYPES:
            return None

        key = (record.connection_key, record.packet_type)
        summary = self._summaries.get(key)
        if summary is None:
            summary = CommandSummary(
                connection_key=record.connection_key,
                device_name=record.device_name,
                device_address=record.device_address,
                packet_type=record.packet_type,
            )
            self._summaries[key] = summary
        return summary

    def _decrement_summary_pending(self, record: CommandRecord) -> None:
        summary = self._summaries.get((record.connection_key, record.packet_type))
        if summary is None:
            return

        summary.pending_count = max(0, summary.pending_count - 1)

    def _remove_connection_summaries(self, connection_key: str) -> None:
        for summary_key in list(self._summaries):
            if summary_key[0] == connection_key:
                self._summaries.pop(summary_key)

    @staticmethod
    def _ack_expected(packet_type: PacketType) -> bool:
        return packet_type in ACK_EXPECTED_PACKET_TYPES


command_tracker = CommandTracker()

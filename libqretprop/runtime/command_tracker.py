from __future__ import annotations
import logging
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from libqretprop.qlcp.enums import PacketType
from libqretprop.runtime.metrics import NULL_METRICS, Metrics


logger = logging.getLogger(__name__)


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
ACK_EXPECTED_PACKET_TYPES = frozenset(
    {
        PacketType.CONTROL,
        PacketType.STREAM_START,
        PacketType.STREAM_STOP,
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


class CommandTracker:
    """Tracks outbound command packets until ACK, NACK, or timeout."""

    def __init__(
        self,
        *,
        recent_completed_limit: int = DEFAULT_RECENT_COMPLETED_LIMIT,
        metrics: Metrics | None = None,
    ) -> None:
        self.metrics = metrics or NULL_METRICS
        self._next_command_id = 1
        self._pending_records: dict[int, CommandRecord] = {}
        self._pending: dict[CommandKey, int] = {}
        self._recent_completed: deque[CommandRecord] = deque(maxlen=recent_completed_limit)

    @property
    def pending(self) -> tuple[CommandRecord, ...]:
        return tuple(self._pending_records.values())

    @property
    def recent_completed(self) -> tuple[CommandRecord, ...]:
        return tuple(self._recent_completed)

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
        self.metrics.record_command_acked(
            record.packet_type,
            max(0.0, record.acked_at - record.sent_at),
        )
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
        self.metrics.record_command_nacked(
            record.packet_type,
            device=record.device_name,
            error_code=error_code,
        )
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
            self.metrics.record_command_timed_out(record.packet_type, device=record.device_name)
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
            self.metrics.record_command_connection_failed(
                record.packet_type,
                device=record.device_name,
                reason=reason,
            )
            self._complete_record(record)
            failed.append(record)

        return failed

    def discard(self, command_id: int) -> CommandRecord | None:
        record = self._pending_records.pop(command_id, None)
        if record is not None:
            self._pending.pop(record.key, None)
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
        logger.debug(
            f"Replaced duplicate pending command: {existing_record.packet_type.name} "
            f"seq={existing_record.packet_sequence} for {existing_record.device_name} (duplicate_command_key)",
        )
        self.metrics.record_command_timed_out(existing_record.packet_type, device=existing_record.device_name)
        self._complete_record(existing_record)

    def _complete_record(self, record: CommandRecord) -> None:
        if record.packet_type in OPERATOR_VISIBLE_PACKET_TYPES:
            self._recent_completed.append(record)

    @staticmethod
    def _timestamp(now: float | None) -> float:
        return time.monotonic() if now is None else now

    @staticmethod
    def _ack_expected(packet_type: PacketType) -> bool:
        return packet_type in ACK_EXPECTED_PACKET_TYPES

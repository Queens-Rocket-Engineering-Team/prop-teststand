from __future__ import annotations
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from libqretprop.qlcp.enums import ControlState, ErrorCode, PacketType


class CommandLifecycle(StrEnum):
    """Lifecycle state for an outbound command packet."""

    SENT = "sent"
    ACKED = "acked"
    NACKED = "nacked"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class CommandKey:
    """Lookup key for matching ACK/NACK packets to sent commands."""

    device_id: str
    packet_type: PacketType
    packet_sequence: int


@dataclass
class CommandRecord:
    """Tracked lifecycle for a single outbound QLCP command."""

    command_id: int
    device_id: str
    packet_type: PacketType
    packet_sequence: int
    sent_at: float
    state: CommandLifecycle = CommandLifecycle.SENT
    acked_at: float | None = None
    nacked_at: float | None = None
    timed_out_at: float | None = None
    nack_error_code: ErrorCode | None = None
    control_id: int | None = None
    requested_state: ControlState | None = None

    @property
    def key(self) -> CommandKey:
        return CommandKey(
            device_id=self.device_id,
            packet_type=self.packet_type,
            packet_sequence=self.packet_sequence,
        )

    @property
    def is_pending(self) -> bool:
        return self.state == CommandLifecycle.SENT


class CommandTracker:
    """Tracks outbound command packets until ACK, NACK, or timeout."""

    def __init__(self) -> None:
        self._next_command_id = 1
        self._records: dict[int, CommandRecord] = {}
        self._pending: dict[CommandKey, int] = {}

    @property
    def records(self) -> tuple[CommandRecord, ...]:
        return tuple(self._records.values())

    @property
    def pending(self) -> tuple[CommandRecord, ...]:
        return tuple(
            record
            for record in self._records.values()
            if record.is_pending
        )

    def get_record(self, command_id: int) -> CommandRecord | None:
        return self._records.get(command_id)

    def get_pending(
        self,
        device_id: str,
        packet_type: PacketType,
        packet_sequence: int,
    ) -> CommandRecord | None:
        key = CommandKey(device_id, packet_type, packet_sequence)
        command_id = self._pending.get(key)
        if command_id is None:
            return None
        return self._records[command_id]

    def mark_sent(
        self,
        device_id: str,
        packet_type: PacketType,
        packet_sequence: int,
        *,
        now: float | None = None,
        control_id: int | None = None,
        requested_state: ControlState | None = None,
    ) -> CommandRecord:
        record = CommandRecord(
            command_id=self._next_command_id,
            device_id=device_id,
            packet_type=packet_type,
            packet_sequence=packet_sequence,
            sent_at=self._timestamp(now),
            control_id=control_id,
            requested_state=requested_state,
        )

        self._next_command_id += 1
        self._records[record.command_id] = record
        self._pending[record.key] = record.command_id
        return record

    def mark_acked(
        self,
        device_id: str,
        packet_type: PacketType,
        packet_sequence: int,
        *,
        now: float | None = None,
    ) -> CommandRecord | None:
        record = self._pop_pending(device_id, packet_type, packet_sequence)
        if record is None:
            return None

        record.state = CommandLifecycle.ACKED
        record.acked_at = self._timestamp(now)
        return record

    def mark_nacked(
        self,
        device_id: str,
        packet_type: PacketType,
        packet_sequence: int,
        error_code: ErrorCode,
        *,
        now: float | None = None,
    ) -> CommandRecord | None:
        record = self._pop_pending(device_id, packet_type, packet_sequence)
        if record is None:
            return None

        record.state = CommandLifecycle.NACKED
        record.nacked_at = self._timestamp(now)
        record.nack_error_code = error_code
        return record

    def expire_pending(self, now: float, timeout_s: float) -> list[CommandRecord]:
        expired: list[CommandRecord] = []

        for key, command_id in list(self._pending.items()):
            record = self._records[command_id]
            if now - record.sent_at < timeout_s:
                continue

            self._pending.pop(key)
            record.state = CommandLifecycle.TIMED_OUT
            record.timed_out_at = now
            expired.append(record)

        return expired

    def discard(self, command_id: int) -> CommandRecord | None:
        record = self._records.pop(command_id, None)
        if record is None:
            return None

        self._pending.pop(record.key, None)
        return record

    def _pop_pending(
        self,
        device_id: str,
        packet_type: PacketType,
        packet_sequence: int,
    ) -> CommandRecord | None:
        key = CommandKey(device_id, packet_type, packet_sequence)
        command_id = self._pending.pop(key, None)
        if command_id is None:
            return None
        return self._records[command_id]

    @staticmethod
    def _timestamp(now: float | None) -> float:
        return time.monotonic() if now is None else now

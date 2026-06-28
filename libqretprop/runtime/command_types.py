from __future__ import annotations
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

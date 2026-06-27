from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SensorSnapshot:
    id: int
    name: str
    type: str
    index: str
    unit: str
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    id: int
    name: str
    type: str
    index: str
    default_state: str
    reported_state: str | None = None
    reported_timestamp: float | None = None
    pending_command_id: int | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class HeartbeatSnapshot:
    state: str
    last_sent_time: float | None = None
    last_ack_time: float | None = None
    pending: bool = False
    consecutive_misses: int = 0
    last_timeout_time: float | None = None


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    """Snapshot for one operational device.

    ``name`` is the stable operational identity from CONFIG.
    ``address`` is the current connection identity and can change after reconnect.
    """

    name: str
    device_type: str
    connected: bool
    address: str
    sensors: list[SensorSnapshot] = field(default_factory=list)
    controls: list[ControlSnapshot] = field(default_factory=list)
    last_sync_time: float | None = None
    heartbeat: HeartbeatSnapshot = field(default_factory=lambda: HeartbeatSnapshot(state="disconnected"))


@dataclass(frozen=True, slots=True)
class CommandSnapshot:
    """Snapshot for a command keyed by a single TCP connection session."""

    command_id: int
    connection_key: str
    device_address: str
    device_name: str | None
    packet_type: str
    sequence: int
    state: str
    sent_at: float
    ack_expected: bool = True
    acked_at: float | None = None
    nacked_at: float | None = None
    timed_out_at: float | None = None
    nack_error_code: str | None = None
    control_id: int | None = None
    control_name: str | None = None
    requested_state: str | None = None


@dataclass(frozen=True, slots=True)
class CommandCollectionSnapshot:
    pending: list[CommandSnapshot] = field(default_factory=list)
    recent: list[CommandSnapshot] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    state_version: int = 0
    devices: list[DeviceSnapshot] = field(default_factory=list)
    commands: CommandCollectionSnapshot = field(default_factory=CommandCollectionSnapshot)

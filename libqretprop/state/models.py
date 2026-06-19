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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "index": self.index,
            "unit": self.unit,
            "raw": self.raw,
        }


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "index": self.index,
            "default_state": self.default_state,
            "reported_state": self.reported_state,
            "reported_timestamp": self.reported_timestamp,
            "pending_command_id": self.pending_command_id,
            "raw": self.raw,
        }


@dataclass(frozen=True, slots=True)
class HeartbeatSnapshot:
    state: str
    last_sent_time: float | None = None
    last_ack_time: float | None = None
    pending: bool = False
    consecutive_misses: int = 0
    last_timeout_time: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "last_sent_time": self.last_sent_time,
            "last_ack_time": self.last_ack_time,
            "pending": self.pending,
            "consecutive_misses": self.consecutive_misses,
            "last_timeout_time": self.last_timeout_time,
        }


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
    is_responsive: bool = True
    missed_heartbeat_acks: int = 0
    heartbeat: HeartbeatSnapshot = field(default_factory=lambda: HeartbeatSnapshot(state="disconnected"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "device_type": self.device_type,
            "connected": self.connected,
            "address": self.address,
            "sensors": [sensor.to_dict() for sensor in self.sensors],
            "controls": [control.to_dict() for control in self.controls],
            "last_sync_time": self.last_sync_time,
            "is_responsive": self.is_responsive,
            "missed_heartbeat_acks": self.missed_heartbeat_acks,
            "heartbeat": self.heartbeat.to_dict(),
        }


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "connection_key": self.connection_key,
            "device_address": self.device_address,
            "device_name": self.device_name,
            "packet_type": self.packet_type,
            "sequence": self.sequence,
            "state": self.state,
            "sent_at": self.sent_at,
            "ack_expected": self.ack_expected,
            "acked_at": self.acked_at,
            "nacked_at": self.nacked_at,
            "timed_out_at": self.timed_out_at,
            "nack_error_code": self.nack_error_code,
            "control_id": self.control_id,
            "control_name": self.control_name,
            "requested_state": self.requested_state,
        }


@dataclass(frozen=True, slots=True)
class CommandCollectionSnapshot:
    pending: list[CommandSnapshot] = field(default_factory=list)
    recent: list[CommandSnapshot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending": [command.to_dict() for command in self.pending],
            "recent": [command.to_dict() for command in self.recent],
        }


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    state_version: int = 0
    devices: list[DeviceSnapshot] = field(default_factory=list)
    commands: CommandCollectionSnapshot = field(default_factory=CommandCollectionSnapshot)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_version": self.state_version,
            "devices": [device.to_dict() for device in self.devices],
            "commands": self.commands.to_dict(),
        }

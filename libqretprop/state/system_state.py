from __future__ import annotations
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Protocol

from libqretprop.qlcp.enums import ControlState, PacketType
from libqretprop.runtime.command_types import (
    MAINTENANCE_PACKET_TYPES,
    OPERATOR_VISIBLE_PACKET_TYPES,
    CommandRecord,
    CommandSummary,
)
from libqretprop.state.models import (
    CommandCollectionSnapshot,
    CommandSnapshot,
    ControlSnapshot,
    DeviceSnapshot,
    HeartbeatSnapshot,
    SensorSnapshot,
    SystemSnapshot,
)


if TYPE_CHECKING:
    from libqretprop.qlcp.config_models import ControlConfig, DeviceConfig, SensorConfig
    from libqretprop.runtime.esp_device_session import ESPDeviceSession


StateEvent = dict[str, object]


class CommandTrackerView(Protocol):
    @property
    def pending(self) -> tuple[CommandRecord, ...]: ...

    @property
    def recent_completed(self) -> tuple[CommandRecord, ...]: ...

    def get_summary(
        self,
        connection_key: str,
        packet_type: PacketType,
    ) -> CommandSummary | None: ...


@dataclass(slots=True)
class _ReportedControlState:
    state: str
    timestamp: float


@dataclass(slots=True)
class _DeviceState:
    device_name: str
    address: str
    connection_key: str
    config: DeviceConfig
    connected: bool
    device: ESPDeviceSession | None = None
    reported_controls: dict[int, _ReportedControlState] = field(default_factory=dict)
    disconnected_at: float | None = None


class SystemState:
    """Read-only projection keyed by operational device identity."""

    def __init__(self, *, command_tracker: CommandTrackerView) -> None:
        self._devices_by_name: dict[str, _DeviceState] = {}
        self._command_tracker = command_tracker
        self._state_version = 0

    @property
    def state_version(self) -> int:
        return self._state_version

    def register_device(self, device: ESPDeviceSession) -> StateEvent:
        self._devices_by_name[device.name] = _DeviceState(
            device_name=device.name,
            address=device.address,
            connection_key=device.connection_key,
            config=device.qlcp_config,
            connected=True,
            device=device,
        )
        return self._make_event(
            "device.registered",
            device=asdict(self._snapshot_device(self._devices_by_name[device.name])),
        )

    def mark_disconnected(self, device: ESPDeviceSession) -> StateEvent | None:
        device_state = self._devices_by_name.get(device.name)
        if device_state is None or device_state.connection_key != device.connection_key:
            return None

        device_state.connected = False
        device_state.device = device
        device_state.disconnected_at = time.monotonic()
        return self._make_event(
            "device.disconnected",
            device_name=device_state.device_name,
            device_address=device_state.address,
            connection_key=device_state.connection_key,
        )

    def update_control_state(
        self,
        device: ESPDeviceSession,
        control_id: int,
        state: ControlState | str,
        *,
        now: float | None = None,
    ) -> StateEvent | None:
        device_state = self._devices_by_name.get(device.name)
        if device_state is None:
            return None
        if device_state.connection_key != device.connection_key:
            return None

        control = device_state.config.controls_by_id.get(control_id)
        if control is None:
            return None

        device_state.reported_controls[control_id] = _ReportedControlState(
            state=self._control_state_name(state),
            timestamp=time.monotonic() if now is None else now,
        )
        control_snapshot = self._snapshot_control(
            device_state,
            control,
        )
        return self._make_event(
            "control.updated",
            device_name=device_state.device_name,
            control_id=control_id,
            control_name=control_snapshot.name,
            reported_state=control_snapshot.reported_state,
            reported_timestamp=control_snapshot.reported_timestamp,
            pending_command_id=control_snapshot.pending_command_id,
        )

    def record_command_sent(self, command: CommandRecord) -> StateEvent | None:
        return self._command_event("command.sent", command)

    def record_command_acked(self, command: CommandRecord) -> StateEvent | None:
        return self._command_event("command.acked", command)

    def record_command_nacked(self, command: CommandRecord) -> StateEvent | None:
        return self._command_event("command.nacked", command)

    def record_command_timed_out(self, command: CommandRecord) -> StateEvent | None:
        return self._command_event("command.timed_out", command)

    def snapshot(self) -> SystemSnapshot:
        devices = [
            self._snapshot_device(device_state)
            for device_state in sorted(self._devices_by_name.values(), key=lambda item: item.device_name)
        ]
        commands = self._snapshot_commands()

        return SystemSnapshot(
            state_version=self._state_version,
            devices=devices,
            commands=commands,
        )

    def to_dict(self) -> dict:
        return asdict(self.snapshot())

    def _snapshot_device(self, device_state: _DeviceState) -> DeviceSnapshot:
        config = device_state.config
        device = device_state.device

        return DeviceSnapshot(
            name=config.name,
            device_type=config.device_type,
            connected=device_state.connected,
            address=device_state.address,
            sensors=[
                self._snapshot_sensor(sensor)
                for sensor in sorted(config.sensors_by_id.values(), key=lambda sensor: sensor.id)
            ],
            controls=[
                self._snapshot_control(device_state, control)
                for control in sorted(config.controls_by_id.values(), key=lambda control: control.id)
            ],
            last_sync_time=device.last_sync_time if device is not None else None,
            heartbeat=self._snapshot_heartbeat(device_state),
        )

    @staticmethod
    def _snapshot_sensor(sensor: SensorConfig) -> SensorSnapshot:
        return SensorSnapshot(
            id=sensor.id,
            name=sensor.name,
            type=sensor.type,
            index=sensor.sensor_index,
            unit=sensor.unit.name,
            raw=sensor.raw,
        )

    def _snapshot_control(
        self,
        device_state: _DeviceState,
        control: ControlConfig,
    ) -> ControlSnapshot:
        reported_state = device_state.reported_controls.get(control.id)

        return ControlSnapshot(
            id=control.id,
            name=control.name,
            type=control.control_type,
            index=control.control_index,
            default_state=control.default.name,
            reported_state=reported_state.state if reported_state is not None else None,
            reported_timestamp=reported_state.timestamp if reported_state is not None else None,
            pending_command_id=self._pending_command_id(device_state, control.id),
            raw=control.raw,
        )

    def _pending_command_id(self, device_state: _DeviceState, control_id: int) -> int | None:
        device = device_state.device
        if device is None:
            return None

        pending_commands = [
            command
            for command in self._command_tracker.pending
            if (
                command.connection_key == device.connection_key
                and command.packet_type == PacketType.CONTROL
                and command.control_id == control_id
            )
        ]
        if not pending_commands:
            return None

        return max(command.command_id for command in pending_commands)

    def _snapshot_heartbeat(self, device_state: _DeviceState) -> HeartbeatSnapshot:
        device = device_state.device
        consecutive_misses = device.missed_heartbeat_count if device is not None else 0
        heartbeat_summary = (
            self._command_tracker.get_summary(device_state.connection_key, PacketType.HEARTBEAT)
            if device is not None
            else None
        )

        if not device_state.connected:
            state = "disconnected"
        elif consecutive_misses > 0:
            state = "missed"
        else:
            state = "ok"

        return HeartbeatSnapshot(
            state=state,
            last_sent_time=heartbeat_summary.last_sent_at if heartbeat_summary is not None else None,
            last_ack_time=heartbeat_summary.last_acked_at if heartbeat_summary is not None else None,
            pending=bool(heartbeat_summary and heartbeat_summary.pending_count > 0),
            consecutive_misses=consecutive_misses,
            last_timeout_time=heartbeat_summary.last_timed_out_at if heartbeat_summary is not None else None,
        )

    def _snapshot_commands(self) -> CommandCollectionSnapshot:
        pending = [
            self._snapshot_command(command)
            for command in sorted(self._command_tracker.pending, key=lambda command: command.command_id)
            if command.packet_type in OPERATOR_VISIBLE_PACKET_TYPES
        ]
        recent = [
            self._snapshot_command(command)
            for command in sorted(self._command_tracker.recent_completed, key=lambda command: command.command_id)
        ]

        return CommandCollectionSnapshot(pending=pending, recent=recent)

    @staticmethod
    def _snapshot_command(command: CommandRecord) -> CommandSnapshot:
        return CommandSnapshot(
            command_id=command.command_id,
            connection_key=command.connection_key,
            device_address=command.device_address,
            device_name=command.device_name,
            packet_type=command.packet_type.name,
            sequence=command.packet_sequence,
            state=command.state.value,
            sent_at=command.sent_at,
            ack_expected=command.ack_expected,
            acked_at=command.acked_at,
            nacked_at=command.nacked_at,
            timed_out_at=command.timed_out_at,
            nack_error_code=command.nack_error_code.name if command.nack_error_code is not None else None,
            control_id=command.control_id,
            control_name=command.control_name,
            requested_state=command.requested_state.name if command.requested_state is not None else None,
        )

    @staticmethod
    def _control_state_name(state: ControlState | str) -> str:
        if isinstance(state, ControlState):
            return state.name
        return state.upper()

    def _command_event(self, event_type: str, command: CommandRecord) -> StateEvent | None:
        if command.packet_type == PacketType.HEARTBEAT:
            return self._heartbeat_event(command.connection_key)
        if command.packet_type in MAINTENANCE_PACKET_TYPES:
            return None
        if command.packet_type not in OPERATOR_VISIBLE_PACKET_TYPES:
            return None

        return self._make_event(
            event_type,
            command=asdict(self._snapshot_command(command)),
        )

    def _heartbeat_event(self, connection_key: str) -> StateEvent | None:
        device_state = self._device_state_for_connection(connection_key)
        if device_state is None:
            return None

        return self._make_event(
            "heartbeat.updated",
            device_name=device_state.device_name,
            device_address=device_state.address,
            connection_key=device_state.connection_key,
            heartbeat=asdict(self._snapshot_heartbeat(device_state)),
        )

    def _device_state_for_connection(self, connection_key: str) -> _DeviceState | None:
        for device_state in self._devices_by_name.values():
            if device_state.connection_key == connection_key:
                return device_state
        return None

    def _make_event(self, event_type: str, **payload: object) -> StateEvent:
        self._state_version += 1
        return {
            "type": event_type,
            "state_version": self._state_version,
            **payload,
        }

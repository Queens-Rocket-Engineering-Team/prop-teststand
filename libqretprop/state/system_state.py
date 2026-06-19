from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from libqretprop.qlcp.enums import ControlState, PacketType
from libqretprop.runtime.command_tracker import (
    OPERATOR_VISIBLE_PACKET_TYPES,
    CommandRecord,
    CommandTracker,
)
from libqretprop.runtime.command_tracker import (
    command_tracker as runtime_command_tracker,
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
    from libqretprop.Devices.ESPDevice import ESPDevice
    from libqretprop.qlcp.config_models import ControlConfig, DeviceConfig, SensorConfig


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
    device: ESPDevice | None = None
    reported_controls: dict[int, _ReportedControlState] = field(default_factory=dict)
    disconnected_at: float | None = None


class SystemState:
    """Read-only projection keyed by operational device identity."""

    def __init__(self, *, command_tracker: CommandTracker | None = None) -> None:
        self._devices_by_name: dict[str, _DeviceState] = {}
        self._command_tracker = runtime_command_tracker if command_tracker is None else command_tracker

    def register_device(self, device: ESPDevice) -> None:
        self._devices_by_name[device.name] = _DeviceState(
            device_name=device.name,
            address=device.address,
            connection_key=device.connection_key,
            config=device.qlcp_config,
            connected=True,
            device=device,
        )

    def mark_disconnected(self, device: ESPDevice) -> None:
        device_state = self._devices_by_name.get(device.name)
        if device_state is None or device_state.connection_key != device.connection_key:
            return

        device_state.connected = False
        device_state.device = device
        device_state.disconnected_at = time.monotonic()

    def update_control_state(
        self,
        device: ESPDevice,
        control_id: int,
        state: ControlState | str,
        *,
        now: float | None = None,
    ) -> None:
        device_state = self._devices_by_name.get(device.name)
        if device_state is None:
            return
        if device_state.connection_key != device.connection_key:
            return

        device_state.reported_controls[control_id] = _ReportedControlState(
            state=self._control_state_name(state),
            timestamp=time.monotonic() if now is None else now,
        )

    def snapshot(self) -> SystemSnapshot:
        devices = [
            self._snapshot_device(device_state)
            for device_state in sorted(self._devices_by_name.values(), key=lambda item: item.device_name)
        ]
        commands = self._snapshot_commands()

        return SystemSnapshot(
            devices=devices,
            commands=commands,
        )

    def to_dict(self) -> dict:
        return self.snapshot().to_dict()

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
            last_sync_time=getattr(device, "last_sync_time", None),
            is_responsive=bool(getattr(device, "is_responsive", False)) if device is not None else False,
            missed_heartbeat_acks=int(getattr(device, "_missed_heartbeat_acks", 0)) if device is not None else 0,
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
        missed_heartbeat_acks = int(getattr(device, "_missed_heartbeat_acks", 0)) if device is not None else 0
        heartbeat_summary = (
            self._command_tracker.get_summary(device_state.connection_key, PacketType.HEARTBEAT)
            if device is not None
            else None
        )

        if not device_state.connected:
            state = "disconnected"
        elif missed_heartbeat_acks > 0:
            state = "missed"
        else:
            state = "ok"

        return HeartbeatSnapshot(
            state=state,
            last_sent_time=heartbeat_summary.last_sent_at if heartbeat_summary is not None else None,
            last_ack_time=heartbeat_summary.last_acked_at if heartbeat_summary is not None else None,
            pending=bool(heartbeat_summary and heartbeat_summary.pending_count > 0),
            consecutive_misses=missed_heartbeat_acks,
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
            acked_at=command.acked_at,
            nacked_at=command.nacked_at,
            timed_out_at=command.timed_out_at,
            nack_error_code=command.nack_error_code.name if command.nack_error_code is not None else None,
            control_id=command.control_id,
            requested_state=command.requested_state.name if command.requested_state is not None else None,
        )

    @staticmethod
    def _control_state_name(state: ControlState | str) -> str:
        if isinstance(state, ControlState):
            return state.name
        return state.upper()


system_state = SystemState()

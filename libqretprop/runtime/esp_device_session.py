from __future__ import annotations
import asyncio
import socket
from typing import TYPE_CHECKING, Any, ClassVar

import libqretprop.mylogging as ml
from libqretprop.drivers.esp import ESPDriver, ESPDriverConnectionClosedError
from libqretprop.qlcp.config_parser import parse_config


if TYPE_CHECKING:
    from libqretprop.qlcp.config_models import ControlConfig, DeviceConfig, SensorConfig
    from libqretprop.runtime.command_tracker import CommandRecord
    from libqretprop.runtime.esp_connection_runtime import ESPConnectionRuntime


class ESPDeviceSession:
    """One active configured TCP session for a QLCP/ESP device."""

    RESYNC_INTERVAL_S: ClassVar[float] = 600.0
    COMMAND_ACK_TIMEOUT_S: ClassVar[float] = 10.0
    HEARTBEAT_INTERVAL_S: ClassVar[float] = 5.0
    HEARTBEAT_ACK_MISS_LIMIT: ClassVar[int] = 3

    def __init__(
        self,
        tcp_socket: socket.socket,
        address: str,
        config: dict[str, Any] | DeviceConfig,
        *,
        connection_key: str,
    ) -> None:
        parsed_config = parse_config(config) if isinstance(config, dict) else config

        self.socket: socket.socket | None = tcp_socket
        self.address = address
        self.connection_key = connection_key
        self.qlcp_config = parsed_config
        self.driver = ESPDriver(tcp_socket, address, config=parsed_config)

        self.control_states: dict[str, str] = {
            control_name: control.default.name for control_name, control in self.controls.items()
        }

        self.last_sync_time: float | None = None
        self._resync_pending = False
        self.is_responsive = True
        self._missed_heartbeat_acks = 0

        self.monitor_task: asyncio.Task[Any] | None = None
        self.heartbeat_task: asyncio.Task[Any] | None = None

    def start(
        self,
        runtime: ESPConnectionRuntime,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        task_loop = asyncio.get_running_loop() if loop is None else loop
        self.monitor_task = task_loop.create_task(self.monitor(runtime))
        self.heartbeat_task = task_loop.create_task(self.heartbeat(runtime))

    @property
    def name(self) -> str:
        return self.qlcp_config.name

    @property
    def type(self) -> str:
        return self.qlcp_config.device_type

    @property
    def sensors(self) -> dict[str, SensorConfig]:
        return {
            sensor.name: sensor
            for sensor in self.qlcp_config.sensors_by_id.values()
        }

    @property
    def controls(self) -> dict[str, ControlConfig]:
        return {
            control.name.upper(): control
            for control in self.qlcp_config.controls_by_id.values()
        }

    @property
    def sensor_names(self) -> list[str]:
        return list(self.sensors)

    async def monitor(self, runtime: ESPConnectionRuntime) -> None:
        """Read TCP packets and delegate session side effects to the runtime."""
        try:
            while True:
                if self.socket is None:
                    ml.elog(f"Device {self.name} has no socket.")
                    runtime.remove_device(self)
                    break

                try:
                    packet = await self.driver.read_packet()
                except ESPDriverConnectionClosedError:
                    ml.elog(f"Device {self.name} disconnected.")
                    runtime.remove_device(self)
                    break

                ml.plog(f"Decoded {type(packet).__name__} from {self.name}")
                await runtime.handle_packet(self, packet)

                if runtime.needs_resync(self):
                    self._resync_pending = True
                    await runtime.send_timesync(self)

        except asyncio.CancelledError:
            ml.slog(f"Stopped monitoring {self.name}")
            raise
        except Exception as e:
            ml.elog(f"Error receiving response from {self.name}: {e}")
            runtime.remove_device(self)

    async def heartbeat(self, runtime: ESPConnectionRuntime) -> None:
        """Run this session's heartbeat loop while policy stays in runtime."""
        while True:
            if self.socket:
                if runtime.expire_command_timeouts(self):
                    break

                if not await runtime.send_heartbeat(self):
                    break

            await asyncio.sleep(self.HEARTBEAT_INTERVAL_S)

    def record_heartbeat_ack(self, command: CommandRecord | None) -> None:
        if command is None:
            return

        self._missed_heartbeat_acks = 0
        self.is_responsive = True

    def set_control_state(self, control_name: str, state: str) -> None:
        """Update this session's local control-state cache."""
        self.control_states[control_name.upper()] = state

    def control_name_for_id(self, control_id: int | None) -> str | None:
        if control_id is None:
            return None

        control = self.qlcp_config.controls_by_id.get(control_id)
        if control is None:
            return None

        return control.name

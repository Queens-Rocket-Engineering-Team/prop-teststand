from __future__ import annotations
import asyncio
import socket
from itertools import count
from typing import TYPE_CHECKING, Any, ClassVar

from libqretprop.drivers.esp import ESPDriver
from libqretprop.qlcp.config_parser import parse_config
from libqretprop.runtime.command_tracker import CommandRecord


if TYPE_CHECKING:
    from libqretprop.qlcp.config_models import ControlConfig, SensorConfig
    from libqretprop.runtime.esp_connection_runtime import ESPConnectionRuntime


_connection_counter = count(1)


class ESPDevice:
    """A top level class representing a connected ESP32 device.

    Parameters
    ----------
        jsonConfig (dict): The JSON configuration of the device, streamed back from the ESP32 on initial connection.
        address (str): The IP address of the ESP32 device.

    """

    RESYNC_INTERVAL_S: ClassVar[float] = 600.0  # 10 minutes
    COMMAND_ACK_TIMEOUT_S: ClassVar[float] = 10.0
    HEARTBEAT_INTERVAL_S: ClassVar[float] = 5.0
    HEARTBEAT_ACK_MISS_LIMIT: ClassVar[int] = 3

    def __init__(
        self,
        tcp_socket: socket.socket,
        address: str,
        jsonConfig: dict[str, Any],
        *,
        connection_key: str | None = None,
        connection_runtime: ESPConnectionRuntime | None = None,
    ) -> None:
        parsed_config = parse_config(jsonConfig)

        self.socket: socket.socket | None = tcp_socket
        self.address = address
        self.connection_key = connection_key or f"esp-{next(_connection_counter)}"
        self.connection_runtime = connection_runtime
        self.qlcp_config = parsed_config
        self.driver = ESPDriver(tcp_socket, address, config=parsed_config)
        self.jsonConfig = jsonConfig
        self.listenerTask: asyncio.Task[Any]

        self.name = parsed_config.name
        self.type = parsed_config.device_type
        self.sensors: dict[str, SensorConfig] = {
            sensor.name: sensor for sensor in parsed_config.sensors_by_id.values()
        }
        self.controls: dict[str, ControlConfig] = {
            control.name.upper(): control for control in parsed_config.controls_by_id.values()
        }
        self.control_states: dict[str, str] = {
            control_name: control.default.name for control_name, control in self.controls.items()
        }
        self.sensor_names: list[str] = list(self.sensors.keys())

        # Timesync state: track when last sync completed for periodic resync
        self.last_sync_time: float | None = None  # server monotonic time of last sync
        self._resync_pending: bool = False

        self.is_responsive: bool = True
        self._missed_heartbeat_acks: int = 0

        self.heartbeat_task = asyncio.create_task(self.heartbeat())

    def handleHeartbeatAck(self, command: CommandRecord | None) -> None:
        if command is None:
            return

        self._missed_heartbeat_acks = 0
        self.is_responsive = True

    def setControlState(self, controlName: str, state: str) -> None:
        self.control_states[controlName.upper()] = state

    def _expireCommandTimeouts(self) -> bool:
        return self._connectionRuntime().expire_command_timeouts(self)

    async def heartbeat(self) -> None:
        """Send a heartbeat to the device every 5 seconds to keep TCP alive."""
        while True:
            if self.socket:
                if self._expireCommandTimeouts():
                    break

                if not await self._connectionRuntime().send_heartbeat(self):
                    break

            await asyncio.sleep(self.HEARTBEAT_INTERVAL_S)

    def _connectionRuntime(self) -> ESPConnectionRuntime:
        if self.connection_runtime is not None:
            return self.connection_runtime

        from libqretprop.runtime.esp_connection_runtime import esp_runtime  # noqa: PLC0415

        return esp_runtime

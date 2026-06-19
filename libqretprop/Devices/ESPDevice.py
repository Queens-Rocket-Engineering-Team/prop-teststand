import asyncio
import socket
import time
from itertools import count
from typing import TYPE_CHECKING, Any, ClassVar

import libqretprop.mylogging as ml
from libqretprop.drivers.esp import ESPDriver
from libqretprop.qlcp.config_parser import parse_config
from libqretprop.qlcp.enums import PacketType
from libqretprop.qlcp.packets import SimplePacket
from libqretprop.runtime.command_tracker import CommandRecord, command_tracker


if TYPE_CHECKING:
    from libqretprop.qlcp.config_models import ControlConfig, SensorConfig


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
    ) -> None:
        parsed_config = parse_config(jsonConfig)

        self.socket: socket.socket | None = tcp_socket
        self.address = address
        self.connection_key = f"esp-{next(_connection_counter)}"
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
        expired_commands = command_tracker.expire_pending(
            now=time.monotonic(),
            timeout_s=self.COMMAND_ACK_TIMEOUT_S,
            connection_key=self.connection_key,
        )

        for expired in expired_commands:
            if expired.packet_type == PacketType.HEARTBEAT:
                if self._handleMissedHeartbeat(expired):
                    return True
            else:
                ml.plog(
                    f"{self.name} command timeout: {expired.packet_type.name} seq={expired.packet_sequence}",
                )

        return False

    def _handleMissedHeartbeat(self, command: CommandRecord) -> bool:
        self._missed_heartbeat_acks += 1

        if self._missed_heartbeat_acks < self.HEARTBEAT_ACK_MISS_LIMIT:
            ml.plog(
                f"{self.name} missed HEARTBEAT ACK seq={command.packet_sequence} "
                f"({self._missed_heartbeat_acks}/{self.HEARTBEAT_ACK_MISS_LIMIT})",
            )
            return False

        from libqretprop.DeviceControllers import deviceTools  # noqa: PLC0415

        self.is_responsive = False
        ml.elog(f"{self.name} marked unresponsive: missed {self._missed_heartbeat_acks} HEARTBEAT ACKs")
        deviceTools.removeDevice(self)
        return True

    async def heartbeat(self) -> None:
        """Send a heartbeat to the device every 5 seconds to keep TCP alive."""
        while True:
            if self.socket:
                if self._expireCommandTimeouts():
                    break

                command: CommandRecord | None = None
                try:
                    packet = SimplePacket.create(PacketType.HEARTBEAT)
                    command = command_tracker.mark_sent(
                        connection_key=self.connection_key,
                        device_name=self.name,
                        device_address=self.address,
                        packet_type=PacketType.HEARTBEAT,
                        packet_sequence=packet.sequence,
                        now=time.monotonic(),
                    )
                    await self.driver.send_packet(packet)
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    if command is not None:
                        command_tracker.discard(command.command_id)
                    from libqretprop.DeviceControllers import deviceTools  # noqa: PLC0415

                    ml.elog(f"{self.name} heartbeat send failed: {e}")
                    deviceTools.removeDevice(self)
                    break

            await asyncio.sleep(self.HEARTBEAT_INTERVAL_S)

import asyncio
import socket
from typing import Any, ClassVar

import libqretprop.mylogging as ml
from libqretprop.drivers.esp import ESPDriver
from libqretprop.qlcp.config_models import ControlConfig, SensorConfig
from libqretprop.qlcp.config_parser import parse_config
from libqretprop.qlcp.enums import PacketType
from libqretprop.qlcp.packets import SimplePacket


class ESPDevice:
    """A top level class representing a connected ESP32 device.

    Parameters
    ----------
        jsonConfig (dict): The JSON configuration of the device, streamed back from the ESP32 on initial connection.
        address (str): The IP address of the ESP32 device.

    """

    RESYNC_INTERVAL_S: ClassVar[float] = 600.0  # 10 minutes
    HEARTBEAT_INTERVAL_S: ClassVar[float] = 5.0
    HEARTBEAT_ACK_MISS_LIMIT: ClassVar[int] = 3

    def __init__(
        self,
        socket: socket.socket,
        address: str,
        jsonConfig: dict[str, Any],
    ) -> None:
        parsed_config = parse_config(jsonConfig)

        self.socket = socket
        self.address = address
        self.qlcp_config = parsed_config
        self.driver = ESPDriver(socket, address, config=parsed_config)
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

        # Pending control commands awaiting ACK (sequence -> (control_name, state))
        self._pending_controls: dict[int, tuple[str, str]] = {}

        self.is_responsive: bool = True
        self._heartbeat_ack_pending: bool = False
        self._last_heartbeat_sequence: int | None = None
        self._missed_heartbeat_acks: int = 0

        self.heartbeat_task = asyncio.create_task(self.heartbeat())

    def handleHeartbeatAck(self, ack_sequence: int) -> None:
        if self._heartbeat_ack_pending and ack_sequence != self._last_heartbeat_sequence:
            ml.plog(
                f"{self.name} HEARTBEAT ACK sequence mismatch: expected {self._last_heartbeat_sequence}, got {ack_sequence}",
            )

        self._heartbeat_ack_pending = False
        self._missed_heartbeat_acks = 0
        self.is_responsive = True

    def setControlState(self, controlName: str, state: str) -> None:
        self.control_states[controlName.upper()] = state

    async def heartbeat(self) -> None:
        """Send a heartbeat to the device every 5 seconds to keep TCP alive."""
        while True:
            if self.socket:
                if self._heartbeat_ack_pending:
                    self._missed_heartbeat_acks += 1
                    if self._missed_heartbeat_acks >= self.HEARTBEAT_ACK_MISS_LIMIT:
                        from libqretprop.DeviceControllers import deviceTools  # noqa: PLC0415

                        self.is_responsive = False
                        ml.elog(f"{self.name} marked unresponsive: missed {self._missed_heartbeat_acks} HEARTBEAT ACKs")
                        deviceTools.removeDevice(self)
                        break

                try:
                    packet = SimplePacket.create(PacketType.HEARTBEAT)
                    await self.driver.send_packet(packet)
                    self._last_heartbeat_sequence = packet.sequence
                    self._heartbeat_ack_pending = True
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    from libqretprop.DeviceControllers import deviceTools  # noqa: PLC0415

                    ml.elog(f"{self.name} heartbeat send failed: {e}")
                    deviceTools.removeDevice(self)
                    break

            await asyncio.sleep(self.HEARTBEAT_INTERVAL_S)

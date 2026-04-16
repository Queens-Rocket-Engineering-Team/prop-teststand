import asyncio
import socket
from typing import TYPE_CHECKING, Any, ClassVar

import libqretprop.mylogging as ml
from libqretprop.DeviceControllers import deviceTools
from libqretprop.protocol import PacketType, SimplePacket

if TYPE_CHECKING:
    from libqretprop.Devices.SensorMonitor import SensorMonitor


class ESPDevice:
    """A top level class representing the configuration of a connected ESP32 device.

    Currently, the only supported device (subclass) is the Sensor Monitor.

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
        self.socket = socket
        self.address = address
        self.jsonConfig = jsonConfig
        self.listenerTask: asyncio.Task[Any]

        self.name: str = jsonConfig["device_name"]
        self.type = jsonConfig["device_type"]

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
                f"{self.name} HEARTBEAT ACK sequence mismatch: expected {self._last_heartbeat_sequence}, got {ack_sequence}"
            )

        self._heartbeat_ack_pending = False
        self._missed_heartbeat_acks = 0
        self.is_responsive = True

    async def heartbeat(self) -> None:
        """Send a heartbeat to the device every 5 seconds to keep TCP alive."""
        while True:
            if self.socket:
                if self._heartbeat_ack_pending:
                    self._missed_heartbeat_acks += 1
                    if self._missed_heartbeat_acks >= self.HEARTBEAT_ACK_MISS_LIMIT:
                        self.is_responsive = False
                        ml.elog(f"{self.name} marked unresponsive: missed {self._missed_heartbeat_acks} HEARTBEAT ACKs")
                        deviceTools.removeDevice(self)
                        break

                try:
                    packet = SimplePacket.create(PacketType.HEARTBEAT)
                    loop = asyncio.get_event_loop()
                    await loop.sock_sendall(self.socket, packet.pack())
                    self._last_heartbeat_sequence = packet.header.sequence
                    self._heartbeat_ack_pending = True
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    ml.elog(f"{self.name} heartbeat send failed: {e}")
                    deviceTools.removeDevice(self)
                    break

            await asyncio.sleep(self.HEARTBEAT_INTERVAL_S)

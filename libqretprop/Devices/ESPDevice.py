import asyncio
import socket
from typing import TYPE_CHECKING, Any, ClassVar


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

    def __init__(self,
                 socket: socket.socket,
                 address: str,
                 jsonConfig: dict[str, Any],
                 ) -> None:

        self.socket = socket
        self.address = address
        self.jsonConfig = jsonConfig
        self.listenerTask: asyncio.Task[Any]

        self.name: str = jsonConfig["deviceName"]
        self.type = jsonConfig["deviceType"]

        # Timesync state: track when last sync completed for periodic resync
        self.last_sync_time: float | None = None  # server monotonic time of last sync
        self._resync_pending: bool = False

        asyncio.create_task(self.heartbeat())

    async def heartbeat(self) -> None:
        """Send a heartbeat to the device every 5 seconds to keep TCP alive."""
        while True:
            if self.socket:
                import contextlib
                from libqretprop.protocol import SimplePacket, PacketType
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    packet = SimplePacket.create(PacketType.HEARTBEAT)
                    loop = asyncio.get_event_loop()
                    await loop.sock_sendall(self.socket, packet.pack())
            await asyncio.sleep(5)

import asyncio
import json
import socket
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from libqretprop.Devices.SensorMonitor import SensorMonitor

class ESPDevice:
    """A top level class representing the configuration of a connected ESP32 device.

    Currently, the only supported device (subclass) is the Sensor Monitor.  To
    define a device call the fromConfigBytes method and pass the byte stream of
    the configuration file received from the device. This will return an object
    of the appropriate type, assuming the device type in the configuration file
    is recognized.

    Parameters
    ----------
        jsonConfig (dict): The JSON configuration of the device, streamed back from the ESP32 on initial connection.
        address (str): The IP address of the ESP32 device.

    """
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

        heartbeatTask = asyncio.create_task(self.heartbeat())

    async def heartbeat(self) -> None:
        """Send a heartbeat message to the device to keep the connection alive.

        Sometimes TCP connections will drop if no data is sent for awhile, but the socket wont come through as closed.
        This just makes sure the connection stays alive by sending a heartbeat message every couple of seconds.

        """
        while True:
            if self.socket:
                import contextlib
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.socket.sendall(b"BEAT\n")
            await asyncio.sleep(5)  # Wait for 5 seconds before sending the next heartbeat

    @staticmethod
    def fromConfigBytes(socket: socket.socket, address: str, configBytes: bytes) -> "SensorMonitor": # Delay evaluation of SensorMonitor to avoid circular imports
        configJson = json.loads(configBytes.decode("utf-8"))
        deviceType = configJson["deviceType"]

        if deviceType in {"Sensor Monitor", "Simulated Sensor Monitor"}:
            # To avoid circular imports, import the SensorMonitor class only within the scope of this function
            from libqretprop.Devices.SensorMonitor import SensorMonitor

            return SensorMonitor(socket, address, configJson)

        err = f"Device type {deviceType} not recognized."
        raise ValueError(err)


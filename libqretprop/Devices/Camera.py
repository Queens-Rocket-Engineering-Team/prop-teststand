from onvif import ONVIFCamera
from zeep.transports import AsyncTransport, Transport
import libqretprop.configManager as config
import asyncio


class Camera:
    """A top level class representing the configuration of a connected Camera device.

    Parameters
    ----------
        address (str): The IP address of the camera.
        port (int): The tcp port of the camera's ONVIF service.

    """
    def __init__(self,
                 address: str,
                 port: int,
                 ) -> None:

        self.address = address
        self.port = port
    async def connect(self):
        # All cameras are setup with these credentials
        self.camera = ONVIFCamera(self.address, self.port, config.serverConfig["accounts"]["camera"]["username"], config.serverConfig["accounts"]["camera"]["password"], transport=AsyncTransport())

        # ONVIF Services
        self.ptz = self.camera.create_ptz_service()
        self.media = self.camera.create_media_service()

        # Token (needed for PTZ and media commands)
        self.token = self.media.GetProfiles()[0].token
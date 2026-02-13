import asyncio
import os

import onvif

import libqretprop.configManager as config


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

    async def connect(self) -> None:
        try:
            # Load wsdl files for ONVIF
            wsdl_path = os.path.join(os.path.dirname(onvif.__file__), 'wsdl/')

            self.camera = onvif.ONVIFCamera(self.address, self.port, config.serverConfig["accounts"]["camera"]["username"], config.serverConfig["accounts"]["camera"]["password"], wsdl_path)
            await asyncio.wait_for(self.camera.update_xaddrs(), timeout=5)


            # ONVIF Services
            self.devicemgmt = await self.camera.create_devicemgmt_service()
            self.ptz = await self.camera.create_ptz_service()
            self.media = await self.camera.create_media_service()

            # Get hostname
            hostname = await self.devicemgmt.GetHostname()

            if "Name" in hostname:
                self.hostname = hostname["Name"]
            else:
                self.hostname = "Camera"

            # Token (needed for PTZ and media commands)
            self.token = (await self.media.GetProfiles())[0].token
        except asyncio.TimeoutError as e:
            raise Exception("Connection timed out") from e
        except Exception:
            raise
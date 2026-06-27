import asyncio
import os

import onvif


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

        # Whether the camera is currently recording via media server
        self.recording = False

    @property
    def stream_path(self) -> str:
        return f"/{self.address}"

    def rtsp_stream_source(self, username: str, password: str) -> str:
        return f"rtsp://{username}:{password}@{self.address}/stream1"

    def set_recording(self, recording: bool) -> None:
        self.recording = recording

    async def connect(self, username: str, password: str) -> None:
        try:
            # Load wsdl files for ONVIF
            wsdl_path = os.path.join(os.path.dirname(onvif.__file__), 'wsdl/')

            self.camera = onvif.ONVIFCamera(self.address, self.port, username, password, wsdl_path)
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
            if self.camera is not None:
                await self.camera.close()
            raise Exception("Connection timed out") from e
        except Exception:
            if self.camera is not None:
                await self.camera.close()
            raise

    async def move_relative(self, x: float, y: float) -> None:
        """Move the camera by relative pan/tilt amounts."""
        await self.ptz.RelativeMove({"ProfileToken": self.token, "Translation": {"PanTilt": {"x": x, "y": y}, "Zoom": {"x": 0}}})

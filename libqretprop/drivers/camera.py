import asyncio
import os
from datetime import UTC, datetime

import onvif


class Camera:
    """ONVIF connection and low-level services for a single camera device."""
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

            # Set the camera clock to current server time (UTC)
            now = datetime.now(UTC)
            time_params = self.devicemgmt.create_type("SetSystemDateAndTime")
            time_params.DateTimeType = "Manual"
            time_params.DaylightSavings = False
            time_params.TimeZone = {"TZ": "UTC0"}
            time_params.UTCDateTime = {
                "Date": {"Year": now.year, "Month": now.month, "Day": now.day},
                "Time": {"Hour": now.hour, "Minute": now.minute, "Second": now.second},
            }
            await self.devicemgmt.SetSystemDateAndTime(time_params)

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

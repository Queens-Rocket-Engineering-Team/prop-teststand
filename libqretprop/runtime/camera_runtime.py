import asyncio
import logging
import re
from pathlib import Path
from typing import TypedDict

import aiohttp

import libqretprop.config_manager as config
from libqretprop.drivers.camera import Camera
from libqretprop.integrations.mediamtx import MediaMTXClient


logger = logging.getLogger(__name__)


class RecordingFileData(TypedDict):
    filename: str
    camera_ip: str | None
    camera_hostname: str | None
    size_bytes: int
    modified_unix_ms: int


class CameraRuntime:
    def __init__(self, mediamtx: MediaMTXClient) -> None:
        self._registry: dict[str, Camera] = {}
        self._mediamtx = mediamtx

    def cameras(self) -> list[Camera]:
        return list(self._registry.values())

    def get_recordings_root(self) -> Path:
        mediamtx_config = config.server_config["services"]["mediamtx"]
        recordings_dir = mediamtx_config.get("recordings_dir")
        if not recordings_dir:
            raise RuntimeError("MediaMTX recordings_dir is not configured")
        return Path(recordings_dir).resolve()

    def _extract_ip_from_filename(self, filename: str) -> str | None:
        ip_match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", filename)
        if ip_match is None:
            return None
        return ip_match.group(1)

    def list_recording_files(self, ip: str | None = None) -> list[RecordingFileData]:
        recordings_root = self.get_recordings_root()
        if not recordings_root.exists() or not recordings_root.is_dir():
            message = f"Recording directory is unavailable: {recordings_root}"
            raise RuntimeError(message)

        camera_host_by_ip: dict[str, str] = {cam.address: cam.hostname for cam in self._registry.values()}
        recordings: list[RecordingFileData] = []

        for file_path in recordings_root.glob("*.mp4"):
            if not file_path.is_file():
                continue

            camera_ip = self._extract_ip_from_filename(file_path.name)
            if ip is not None and camera_ip != ip:
                continue

            stat = file_path.stat()
            recordings.append(
                {
                    "filename": file_path.name,
                    "camera_ip": camera_ip,
                    "camera_hostname": camera_host_by_ip.get(camera_ip) if camera_ip is not None else None,
                    "size_bytes": stat.st_size,
                    "modified_unix_ms": int(stat.st_mtime * 1000),
                },
            )

        recordings.sort(key=lambda rec: rec["modified_unix_ms"], reverse=True)
        return recordings

    def get_recording_file_path(self, filename: str) -> Path:
        recordings_root = self.get_recordings_root()
        safe_filename = Path(filename).name
        if safe_filename != filename:
            raise ValueError("Invalid recording filename")

        file_path = (recordings_root / safe_filename).resolve()
        if file_path.parent != recordings_root:
            raise ValueError("Invalid recording path")

        if not file_path.exists() or not file_path.is_file():
            message = f"Recording not found: {safe_filename}"
            raise FileNotFoundError(message)

        return file_path

    async def connect_all_cameras(self) -> None:
        """Connect to all configured cameras and register them."""
        async with aiohttp.ClientSession() as http_client:
            cam_username, cam_password = self._camera_credentials()

            for camera in config.server_config["cameras"]:
                camera_ip = camera["ip"]
                camera_port = camera["onvif_port"]

                camera_object = await self.register_camera(camera_ip, camera_port)
                if camera_object is None or not self._mediamtx_configured():
                    continue

                await self._configure_media_server_for_camera(
                    http_client,
                    camera_object,
                    username=cam_username,
                    password=cam_password,
                )

    async def register_camera(self, ip: str, port: int) -> Camera | None:
        """Register a camera with its IP and ONVIF port."""
        # Reset camera in registry if exists
        self._registry.pop(ip, None)

        logger.info(f"Attempting to connect to camera at {ip}")

        try:
            # Create camera object and connect to it
            camera_object = Camera(ip, port)
            await camera_object.connect()

            logger.info(f"Connected to camera {camera_object.hostname} ({ip})")

            self._registry[ip] = camera_object
            return camera_object
        except Exception as e:
            logger.error(f"Failed to connect to camera at {ip}: {e}")
            return None

    async def move_camera(self, ip: str, x: float, y: float) -> None:
        """Move a camera by relative pan/tilt amounts."""
        try:
            cam = self._require_camera(ip)
            await cam.move_relative(x, y)
        except Exception as e:
            logger.error(f"Failed to move camera at {ip}: {e}")

    async def start_camera_recording(self, ip: str) -> None:
        await self._set_camera_recording(ip, recording=True)

    async def stop_camera_recording(self, ip: str) -> None:
        await self._set_camera_recording(ip, recording=False)

    def _camera_credentials(self) -> tuple[str, str]:
        return (
            config.server_config["accounts"]["camera"]["username"],
            config.server_config["accounts"]["camera"]["password"],
        )

    def _mediamtx_configured(self) -> bool:
        return config.server_config["services"]["mediamtx"] is not None

    def _require_camera(self, ip: str) -> Camera:
        camera = self._registry.get(ip)
        if camera is None:
            raise Exception("Camera does not exist")
        return camera

    def _record_path_for(self, camera: Camera) -> str:
        # MediaMTX resolves recordPath inside its own container filesystem.
        return str(Path("/recordings") / f"{camera.hostname}_%path_%Y%m%d_%H%M%S_%f")

    async def _configure_media_server_for_camera(
        self,
        http_client: aiohttp.ClientSession,
        camera: Camera,
        *,
        username: str,
        password: str,
    ) -> None:
        try:
            logger.info(f"Configuring media server for camera {camera.hostname} ({camera.address})")
            await self._mediamtx.add_path(
                http_client,
                camera.address,
                source=camera.rtsp_stream_source(username, password),
                record_path=self._record_path_for(camera),
            )
        except asyncio.TimeoutError:
            logger.error(f"Media server configuration timed out for {camera.hostname} ({camera.address})")

        await self._refresh_media_recording_state(http_client, camera)

    async def _refresh_media_recording_state(self, http_client: aiohttp.ClientSession, camera: Camera) -> None:
        record_state = await self._mediamtx.get_path_record_state(http_client, camera.address)
        if record_state is None:
            camera.set_recording(False)
            logger.error(f"Could not determine recording state from MediaMTX path {camera.address}; defaulting to False")
        else:
            camera.set_recording(record_state)

    async def _set_camera_recording(self, ip: str, *, recording: bool) -> None:
        action = "start" if recording else "stop"
        action_title = "Starting" if recording else "Stopping"

        try:
            camera = self._require_camera(ip)
        except Exception as e:
            logger.error(f"Failed to {action} recording for camera at {ip}: {e}")
            raise Exception(f"Camera {ip} does not exist") from e

        # PATCH media server /v3/config/paths/patch/{ip} with {"record": true|false}
        if self._mediamtx_configured():
            async with aiohttp.ClientSession() as http_client:
                try:
                    logger.info(f"{action_title} recording for camera at {ip} via media server API")
                    response = await self._mediamtx.set_recording(http_client, ip, record=recording)

                    if response.status != 200:
                        raise Exception(f"Media server API returned status {response.status}")

                    # Mirror the MediaMTX recording state in the camera registry.
                    camera.set_recording(recording)
                except asyncio.TimeoutError:
                    logger.error(f"Media server API request to {action} recording timed out for camera at {ip}")
                    raise Exception("Media server API request timed out")
                except Exception as e:
                    logger.error(f"Failed to {action} recording for camera at {ip}: {e}")
                    raise Exception(f"Failed to {action} recording for camera at {ip}: {e}")
        else:
            logger.error(f"Failed to {action} recording for camera at {ip}: Media server is not configured")

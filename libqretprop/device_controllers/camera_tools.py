import asyncio
import logging
import re
from pathlib import Path
from typing import TypedDict

import aiohttp

import libqretprop.config_manager as config
from libqretprop.devices.Camera import Camera


logger = logging.getLogger(__name__)
camera_registry: dict[str, Camera] = {}


class RecordingFileData(TypedDict):
    filename: str
    camera_ip: str | None
    camera_hostname: str | None
    size_bytes: int
    modified_unix_ms: int


def get_recordings_root() -> Path:
    mediamtx_config = config.server_config["services"]["mediamtx"]
    recordings_dir = mediamtx_config.get("recordings_dir")
    if not recordings_dir:
        raise RuntimeError("MediaMTX recordings_dir is not configured")
    return Path(recordings_dir).resolve()


def _parse_mediamtx_record_flag(data: dict) -> bool | None:
    record_flag = data.get("record")
    if isinstance(record_flag, bool):
        return record_flag

    item = data.get("item")
    if isinstance(item, dict):
        record_flag = item.get("record")
        if isinstance(record_flag, bool):
            return record_flag

    return None


async def _get_mediamtx_path_record_state(http_client: aiohttp.ClientSession, path_name: str) -> bool | None:
    mediamtx_config = config.server_config["services"]["mediamtx"]
    mediamtx_ip = mediamtx_config["ip"]
    mediamtx_port = mediamtx_config["api_port"]

    try:
        response = await http_client.get(
            f"http://{mediamtx_ip}:{mediamtx_port}/v3/config/paths/get/{path_name}",
            timeout=aiohttp.ClientTimeout(10),
        )

        if response.status == 404:
            return None

        if response.status != 200:
            logger.error(f"Failed to read MediaMTX path config for {path_name}: status {response.status}")
            return None

        data = await response.json()
        if not isinstance(data, dict):
            return None

        return _parse_mediamtx_record_flag(data)
    except TimeoutError:
        logger.error(f"MediaMTX path config request timed out for {path_name}")
    except Exception as e:
        logger.error(f"Failed reading MediaMTX path config for {path_name}: {e}")

    return None


def _extract_ip_from_filename(filename: str) -> str | None:
    ip_match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", filename)
    if ip_match is None:
        return None
    return ip_match.group(1)


def list_recording_files(ip: str | None = None) -> list[RecordingFileData]:
    recordings_root = get_recordings_root()
    if not recordings_root.exists() or not recordings_root.is_dir():
        message = f"Recording directory is unavailable: {recordings_root}"
        raise RuntimeError(message)

    camera_host_by_ip: dict[str, str] = {cam.address: cam.hostname for cam in camera_registry.values()}
    recordings: list[RecordingFileData] = []

    for file_path in recordings_root.glob("*.mp4"):
        if not file_path.is_file():
            continue

        camera_ip = _extract_ip_from_filename(file_path.name)
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


def get_recording_file_path(filename: str) -> Path:
    recordings_root = get_recordings_root()
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


async def connect_all_cameras() -> None:
    """Connect to all configured cameras and register them."""
    async with aiohttp.ClientSession() as http_client:
        cam_username = config.server_config["accounts"]["camera"]["username"]
        cam_password = config.server_config["accounts"]["camera"]["password"]

        for camera in config.server_config["cameras"]:
            camera_ip = camera["ip"]
            camera_port = camera["onvif_port"]

            # Register camera in camera registry
            await register_camera(camera_ip, camera_port)

            # Configure camera RTSP relay in media server
            # Only configure for successful camera connections
            if (camera_ip in camera_registry) and (config.server_config["services"]["mediamtx"] is not None):
                mediamtx_ip = config.server_config["services"]["mediamtx"]["ip"]
                mediamtx_port = config.server_config["services"]["mediamtx"]["api_port"]

                cam = camera_registry[camera_ip]
                # MediaMTX resolves recordPath inside its own container filesystem.
                record_path = str(Path("/recordings") / f"{cam.hostname}_%path_%Y%m%d_%H%M%S_%f")
                try:
                    logger.info(f"Configuring media server for camera {cam.hostname} ({camera_ip})")
                    await http_client.post(f"http://{mediamtx_ip}:{mediamtx_port}/v3/config/paths/add/{cam.address}", json={
                        "source": f"rtsp://{cam_username}:{cam_password}@{cam.address}/stream1",
                        "sourceOnDemand": False,  # Always pull stream even if no viewers to ensure recording works
                        "recordPath": record_path,
                        "recordSegmentDuration": "2h",  # 2 hours per recording file segment to accommodate long sessions
                    }, timeout=aiohttp.ClientTimeout(10))
                except asyncio.TimeoutError:
                    logger.error(f"Media server configuration timed out for {cam.hostname} ({camera_ip})")

                record_state = await _get_mediamtx_path_record_state(http_client, cam.address)
                if record_state is None:
                    cam.recording = False
                    logger.error(f"Could not determine recording state from MediaMTX path {cam.address}; defaulting to False")
                else:
                    cam.recording = record_state

async def register_camera(ip: str, port: int) -> None:
    """Register a camera with its IP and ONVIF port."""
    # Reset camera in registry if exists
    camera_registry.pop(ip, None)

    logger.info(f"Attempting to connect to camera at {ip}")

    try:
        # Create camera object and connect to it
        camera_object = Camera(ip, port)
        await camera_object.connect()

        logger.info(f"Connected to camera {camera_object.hostname} ({ip})")

        camera_registry[ip] = camera_object
    except Exception as e:
        logger.error(f"Failed to connect to camera at {ip}: {e}")
async def move_camera(ip: str, x: float, y: float) -> None:
    """Move a camera by relative pan/tilt amounts."""
    try:
        if ip not in camera_registry:
            raise Exception("Camera does not exist")

        cam = camera_registry[ip]

        await cam.ptz.RelativeMove({"ProfileToken": cam.token, "Translation": {"PanTilt": {"x": x, "y": y}, "Zoom": {"x": 0}}})
    except Exception as e:
        logger.error(f"Failed to move camera at {ip}: {e}")

async def start_camera_recording(ip: str) -> None:
    if ip not in camera_registry:
        logger.error(f"Failed to start recording for camera at {ip}: Camera does not exist")
        raise Exception(f"Camera {ip} does not exist")

    # PATCH media server /v3/config/paths/patch/{ip} with {"record": true}
    if config.server_config["services"]["mediamtx"] is not None:
        mediamtx_ip = config.server_config["services"]["mediamtx"]["ip"]
        mediamtx_port = config.server_config["services"]["mediamtx"]["api_port"]

        async with aiohttp.ClientSession() as http_client:
            try:
                logger.info(f"Starting recording for camera at {ip} via media server API")
                response = await http_client.patch(f"http://{mediamtx_ip}:{mediamtx_port}/v3/config/paths/patch/{ip}", json={
                    "record": True,
                }, timeout=aiohttp.ClientTimeout(10))

                if response.status != 200:
                    raise Exception(f"Media server API returned status {response.status}")

                # Mark camera as recording in registry
                cam = camera_registry[ip]
                cam.recording = True
            except asyncio.TimeoutError:
                logger.error(f"Media server API request to start recording timed out for camera at {ip}")
                raise Exception("Media server API request timed out")
            except Exception as e:
                logger.error(f"Failed to start recording for camera at {ip}: {e}")
                raise Exception(f"Failed to start recording for camera at {ip}: {e}")
    else:
        logger.error(f"Failed to start recording for camera at {ip}: Media server is not configured")

async def stop_camera_recording(ip: str) -> None:
    if ip not in camera_registry:
        logger.error(f"Failed to stop recording for camera at {ip}: Camera does not exist")
        raise Exception(f"Camera {ip} does not exist")

    # PATCH media server /v3/config/paths/patch/{ip} with {"record": false}
    if config.server_config["services"]["mediamtx"] is not None:
        mediamtx_ip = config.server_config["services"]["mediamtx"]["ip"]
        mediamtx_port = config.server_config["services"]["mediamtx"]["api_port"]

        async with aiohttp.ClientSession() as http_client:
            try:
                logger.info(f"Stopping recording for camera at {ip} via media server API")
                response = await http_client.patch(f"http://{mediamtx_ip}:{mediamtx_port}/v3/config/paths/patch/{ip}", json={
                    "record": False,
                }, timeout=aiohttp.ClientTimeout(10))

                if response.status != 200:
                    raise Exception(f"Media server API returned status {response.status}")

                # Mark camera as not recording in registry
                cam = camera_registry[ip]
                cam.recording = False
            except asyncio.TimeoutError:
                logger.error(f"Media server API request to stop recording timed out for camera at {ip}")
                raise Exception("Media server API request timed out")
            except Exception as e:
                logger.error(f"Failed to stop recording for camera at {ip}: {e}")
                raise Exception(f"Failed to stop recording for camera at {ip}: {e}")
    else:
        logger.error(f"Failed to stop recording for camera at {ip}: Media server is not configured")

import asyncio
import re
from pathlib import Path
from typing import TypedDict

import aiohttp

import libqretprop.configManager as config
import libqretprop.mylogging as ml
from libqretprop.Devices.Camera import Camera


cameraRegistry: dict[str, Camera] = {}


class RecordingFileData(TypedDict):
    filename: str
    camera_ip: str | None
    camera_hostname: str | None
    size_bytes: int
    modified_unix_ms: int


def getRecordingsRoot() -> Path:
    mediamtxConfig = config.serverConfig["services"]["mediamtx"]
    recordingsDir = mediamtxConfig.get("recordings_dir")
    if not recordingsDir:
        raise RuntimeError("MediaMTX recordings_dir is not configured")
    return Path(recordingsDir).resolve()


def _extractIpFromFilename(filename: str) -> str | None:
    ipMatch = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", filename)
    if ipMatch is None:
        return None
    return ipMatch.group(1)


def listRecordingFiles(ip: str | None = None) -> list[RecordingFileData]:
    recordingsRoot = getRecordingsRoot()
    if not recordingsRoot.exists() or not recordingsRoot.is_dir():
        message = f"Recording directory is unavailable: {recordingsRoot}"
        raise RuntimeError(message)

    cameraHostByIp: dict[str, str] = {cam.address: cam.hostname for cam in cameraRegistry.values()}
    recordings: list[RecordingFileData] = []

    for filePath in recordingsRoot.glob("*.mp4"):
        if not filePath.is_file():
            continue

        cameraIp = _extractIpFromFilename(filePath.name)
        if ip is not None and cameraIp != ip:
            continue

        stat = filePath.stat()
        recordings.append(
            {
                "filename": filePath.name,
                "camera_ip": cameraIp,
                "camera_hostname": cameraHostByIp.get(cameraIp) if cameraIp is not None else None,
                "size_bytes": stat.st_size,
                "modified_unix_ms": int(stat.st_mtime * 1000),
            },
        )

    recordings.sort(key=lambda rec: rec["modified_unix_ms"], reverse=True)
    return recordings


def getRecordingFilePath(filename: str) -> Path:
    recordingsRoot = getRecordingsRoot()
    safeFilename = Path(filename).name
    if safeFilename != filename:
        raise ValueError("Invalid recording filename")

    filePath = (recordingsRoot / safeFilename).resolve()
    if filePath.parent != recordingsRoot:
        raise ValueError("Invalid recording path")

    if not filePath.exists() or not filePath.is_file():
        message = f"Recording not found: {safeFilename}"
        raise FileNotFoundError(message)

    return filePath
"""
Connect to all camera defined in cameraConfig and register them
"""
async def connectAllCameras() -> None:
    async with aiohttp.ClientSession() as httpClient:
        cam_username = config.serverConfig["accounts"]["camera"]["username"]
        cam_password = config.serverConfig["accounts"]["camera"]["password"]

        for camera in config.serverConfig["cameras"]:
            camera_ip = camera["ip"]
            camera_port = camera["onvif_port"]

            # Register camera in camera registry
            await registerCamera(camera_ip, camera_port)

            # Configure camera RTSP relay in media server
            # Only configure for successful camera connections
            if (camera_ip in cameraRegistry) and (config.serverConfig["services"]["mediamtx"] is not None):
                mediamtx_ip = config.serverConfig["services"]["mediamtx"]["ip"]
                mediamtx_port = config.serverConfig["services"]["mediamtx"]["api_port"]

                cam = cameraRegistry[camera_ip]
                # MediaMTX resolves recordPath inside its own container filesystem.
                record_path = str(Path("/recordings") / f"{cam.hostname}_%path_%Y%m%d_%H%M%S_%f")
                try:
                    ml.slog(f"Configuring media server for camera {cam.hostname} ({camera_ip})")
                    await httpClient.post(f"http://{mediamtx_ip}:{mediamtx_port}/v3/config/paths/add/{cam.address}", json={
                        "source": f"rtsp://{cam_username}:{cam_password}@{cam.address}/stream1",
                        "sourceOnDemand": True,
                        "recordPath": record_path,
                        "recordSegmentDuration": "2h",  # 2 hours per recording file segment to accommodate long sessions
                    }, timeout=aiohttp.ClientTimeout(10))
                except asyncio.TimeoutError:
                    ml.elog(f"Media server configuration timed out for {cam.hostname} ({camera_ip})")


"""Register a camera with its IP and port

Args:
    ip (str): The IP address of the camera
    port (int): The tcp port of the camera's ONVIF service
"""
async def registerCamera(ip: str, port: int) -> None:
    # Reset camera in registry if exists
    cameraRegistry.pop(ip, None)

    ml.slog(f"Attempting to connect to camera at {ip}")

    try:
        # Create camera object and connect to it
        cameraObject = Camera(ip, port)
        await cameraObject.connect()

        ml.slog(f"Connected to camera {cameraObject.hostname} ({ip})")

        cameraRegistry[ip] = cameraObject
    except Exception as e:
        ml.elog(f"Failed to connect to camera at {ip}: {e}")

"""Move a camera at given IP by relative x (pan) and y (tilt) amounts

Args:
    ip (str): The IP address of the camera
    x (float): The relative x movement (pan)
    y (float): The relative y movement (tilt)
"""
async def moveCamera(ip: str, x: float, y: float) -> None:
    try:
        if ip not in cameraRegistry:
            raise Exception("Camera does not exist")

        cam = cameraRegistry[ip]

        await cam.ptz.RelativeMove({"ProfileToken": cam.token, "Translation": {"PanTilt": {"x": x, "y": y}, "Zoom": {"x": 0}}})
    except Exception as e:
        ml.elog(f"Failed to move camera at {ip}: {e}")

"""Get the RTSP stream URL for a camera at given IP

Args:
    ip (str): The IP address of the camera
"""
async def getStreamURL(ip: str) -> str:
    try:
        if ip not in cameraRegistry:
            raise Exception("Camera does not exist")

        cam = cameraRegistry[ip]

        streamSetup = {
            "Stream": "RTP-Unicast",
            "Transport": {"Protocol": "RTSP"},
        }
        streamUri = await cam.media.GetStreamUri({"ProfileToken": cam.token, "StreamSetup": streamSetup})

        return streamUri.Uri
    except Exception as e:
        ml.elog(f"Failed to get stream URL for camera at {ip}: {e}")
        raise

async def startCameraRecording(ip: str) -> None:
    if ip not in cameraRegistry:
        ml.elog(f"Failed to start recording for camera at {ip}: Camera does not exist")
        raise Exception(f"Camera {ip} does not exist")

    # PATCH media server /v3/config/paths/patch/{ip} with {"record": true}
    if config.serverConfig["services"]["mediamtx"] is not None:
        mediamtx_ip = config.serverConfig["services"]["mediamtx"]["ip"]
        mediamtx_port = config.serverConfig["services"]["mediamtx"]["api_port"]

        async with aiohttp.ClientSession() as httpClient:
            try:
                ml.slog(f"Starting recording for camera at {ip} via media server API")
                response = await httpClient.patch(f"http://{mediamtx_ip}:{mediamtx_port}/v3/config/paths/patch/{ip}", json={
                    "record": True,
                }, timeout=aiohttp.ClientTimeout(10))

                if response.status != 200:
                    raise Exception(f"Media server API returned status {response.status}")

                # Mark camera as recording in registry
                cam = cameraRegistry[ip]
                cam.recording = True
            except asyncio.TimeoutError:
                ml.elog(f"Media server API request to start recording timed out for camera at {ip}")
                raise Exception("Media server API request timed out")
            except Exception as e:
                ml.elog(f"Failed to start recording for camera at {ip}: {e}")
                raise Exception(f"Failed to start recording for camera at {ip}: {e}")
    else:
        ml.elog(f"Failed to start recording for camera at {ip}: Media server is not configured")

async def stopCameraRecording(ip: str) -> None:
    if ip not in cameraRegistry:
        ml.elog(f"Failed to stop recording for camera at {ip}: Camera does not exist")
        raise Exception(f"Camera {ip} does not exist")

    # PATCH media server /v3/config/paths/patch/{ip} with {"record": false}
    if config.serverConfig["services"]["mediamtx"] is not None:
        mediamtx_ip = config.serverConfig["services"]["mediamtx"]["ip"]
        mediamtx_port = config.serverConfig["services"]["mediamtx"]["api_port"]

        async with aiohttp.ClientSession() as httpClient:
            try:
                ml.slog(f"Stopping recording for camera at {ip} via media server API")
                response = await httpClient.patch(f"http://{mediamtx_ip}:{mediamtx_port}/v3/config/paths/patch/{ip}", json={
                    "record": False,
                }, timeout=aiohttp.ClientTimeout(10))

                if response.status != 200:
                    raise Exception(f"Media server API returned status {response.status}")

                # Mark camera as not recording in registry
                cam = cameraRegistry[ip]
                cam.recording = False
            except asyncio.TimeoutError:
                ml.elog(f"Media server API request to stop recording timed out for camera at {ip}")
                raise Exception("Media server API request timed out")
            except Exception as e:
                ml.elog(f"Failed to stop recording for camera at {ip}: {e}")
                raise Exception(f"Failed to stop recording for camera at {ip}: {e}")
    else:
        ml.elog(f"Failed to stop recording for camera at {ip}: Media server is not configured")

async def getCameraRecordings(ip: str) -> list[str]:
    if ip not in cameraRegistry:
        ml.elog(f"Failed to get recordings for camera at {ip}: Camera does not exist")
        raise Exception(f"Camera {ip} does not exist")

    if config.serverConfig["services"]["mediamtx"] is not None:
        mediamtx_ip = config.serverConfig["services"]["mediamtx"]["ip"]
        mediamtx_port = config.serverConfig["services"]["mediamtx"]["api_port"]

        async with aiohttp.ClientSession() as httpClient:
            try:
                ml.slog(f"Getting recordings for camera at {ip} via media server API")
                response = await httpClient.get(f"http://{mediamtx_ip}:{mediamtx_port}/v3/recordings/{ip}", timeout=aiohttp.ClientTimeout(10))

                if response.status != 200:
                    raise Exception(f"Media server API returned status {response.status}")

                data = await response.json()
                return data.get("recordings", [])
            except asyncio.TimeoutError:
                ml.elog(f"Media server API request to get recordings timed out for camera at {ip}")
                raise Exception("Media server API request timed out")
            except Exception as e:
                ml.elog(f"Failed to get recordings for camera at {ip}: {e}")
                raise Exception(f"Failed to get recordings for camera at {ip}: {e}")
    else:
        ml.elog(f"Failed to get recordings for camera at {ip}: Media server is not configured")
        raise Exception("Media server is not configured")

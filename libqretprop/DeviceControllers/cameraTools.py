import aiohttp

import libqretprop.configManager as config
import libqretprop.mylogging as ml
from libqretprop.Devices.Camera import Camera


cameraRegistry : dict[str, Camera] = {}
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
            if (camera_ip in cameraRegistry) and (config.serverConfig["mediamtx"] is not None):
                mediamtx_ip = config.serverConfig["mediamtx"]["ip"]
                mediamtx_port = config.serverConfig["mediamtx"]["port"]

                cam = cameraRegistry[camera_ip]
                try:
                    ml.slog(f"Configuring media server for camera {cam.hostname} ({camera_ip})")
                    await httpClient.post(f"http://{mediamtx_ip}:{mediamtx_port}/v3/config/paths/add/{cam.address}", json={
                        "source": f"rtsp://{cam_username}:{cam_password}@{cam.address}/stream1",
                        "sourceOnDemand": True,
                    }, timeout=aiohttp.ClientTimeout(10))
                except TimeoutError:
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
    cam = cameraRegistry[ip]

    streamSetup = {
        "Stream": "RTP-Unicast",
        "Transport": {"Protocol": "RTSP"},
    }
    streamUri = await cam.media.GetStreamUri({"ProfileToken": cam.token, "StreamSetup": streamSetup})

    return streamUri.Uri

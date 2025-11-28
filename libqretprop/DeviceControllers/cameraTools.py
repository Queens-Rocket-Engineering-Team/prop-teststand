from onvif import ONVIFCamera, ONVIFService
from libqretprop.Devices.Camera import Camera
import libqretprop.configManager as config
import aiohttp

cameraRegistry : dict[str, Camera] = {}
"""
Connect to all camera defined in cameraConfig and register them
"""
async def connectAllCameras():
    httpClient : aiohttp.ClientSession = aiohttp.ClientSession()

    cam_username = config.serverConfig["accounts"]["camera"]["username"]
    cam_password = config.serverConfig["accounts"]["camera"]["password"]


    for camera in config.serverConfig["cameras"]:
        print(camera)
        # Register camera in camera registry
        await registerCamera(camera["ip"], camera["onvif_port"])

        # Configure camera RTSP relay in media server
        cam = cameraRegistry[camera["ip"]]
        await httpClient.post(f"http://localhost:9997/v3/config/paths/add/{cam.address}", json={
            "source": f"rtsp://{cam_username}:{cam_password}@{cam.address}/stream1",
            "sourceOnDemand": True,
        })

    await httpClient.close()


"""Register a camera with its IP and port

Args:
    ip (str): The IP address of the camera
    port (int): The tcp port of the camera's ONVIF service
"""
async def registerCamera(ip: str, port: int) -> None:
    print("CONNECTING")

    # Create camera object and connect to it
    cameraObject = Camera(ip, port)

    print("SETUP")

    await cameraObject.connect()

    print("CONNECTED")

    cameraRegistry[ip] = cameraObject

"""Move a camera at given IP by relative x (pan) and y (tilt) amounts

Args:
    ip (str): The IP address of the camera
    x (float): The relative x movement (pan)
    y (float): The relative y movement (tilt)
"""
def moveCamera(ip: str, x: float, y: float) -> None:
    cam = cameraRegistry[ip]

    cam.ptz.RelativeMove({"ProfileToken": cam.token, "Translation": {"PanTilt": {"x": x, "y": y}, "Zoom": {"x": 0}}})

"""Get the RTSP stream URL for a camera at given IP

Args:
    ip (str): The IP address of the camera
"""
def getStreamURL(ip: str) -> str:
    cam = cameraRegistry[ip]

    streamSetup = {
        "Stream": "RTP-Unicast",
        "Transport": {"Protocol": "RTSP"}
    }
    streamUri = cam.media.GetStreamUri({"ProfileToken": cam.token, "StreamSetup": streamSetup})

    return streamUri.Uri
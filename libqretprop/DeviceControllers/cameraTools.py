from onvif import ONVIFCamera, ONVIFService
from libqretprop.Devices.Camera import Camera

cameraConfig = [
    # (IP, Port)
    ("192.168.1.5", 2020),
    # TODO: Add rest of cameras here
]

cameraRegistry : dict[str, Camera] = {}

"""
Connect to all camera defined in cameraConfig and register them
"""
async def connectAllCameras():
    for camera in cameraConfig:
        await registerCamera(camera[0], camera[1])

"""Register a camera with its IP and port

Args:
    ip (str): The IP address of the camera
    port (int): The tcp port of the camera's ONVIF service
"""
async def registerCamera(ip: str, port: int) -> None:
    cameraObject = Camera(ip, port)
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

    return streamUri.MediaUri.Uri
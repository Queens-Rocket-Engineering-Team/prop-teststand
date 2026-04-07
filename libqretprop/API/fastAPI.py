import json
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Annotated, Any, Literal

import asyncio
from fastapi.responses import FileResponse
import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from mumble import Mumble
from wave import Wave_write
from pathlib import Path

from libqretprop import mylogging as ml
from libqretprop.DeviceControllers import cameraTools, deviceTools, kasaTools
from libqretprop.Devices.SensorMonitor import SensorMonitor
from libqretprop.GuiDataStream import router as log_router
import libqretprop.mumbleRecording as mumbleRecording
import libqretprop.configManager as config


if TYPE_CHECKING:
    from collections.abc import Callable


app = FastAPI()
app.include_router(log_router)
security = HTTPBasic()


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Middleware to log requests to ml.slog instead of stdout."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        ml.slog(
            f'{request.client.host}:{request.client.port} - "{request.method} {request.url.path} HTTP/1.1" {response.status_code} ({duration_ms:.0f}ms)'
        )
        return response


app.add_middleware(AccessLogMiddleware)

# Server runs exclusively on propnet and is not publicly available
# CSRF is not a concern here
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Hardcoded creds
ALLOWED_USERS = {
    "noah": "stinkylion",
    "admin": "propteambestteam",
}


async def startAPI() -> None:
    """Start the FastAPI server."""
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        loop="asyncio",
        log_level="warning",  # Suppress INFO-level access logs
        access_log=False,  # Disable uvicorn access logging (handled by middleware)
    )
    server = uvicorn.Server(config)
    await server.serve()


def authUser(creds: HTTPBasicCredentials = Depends(security)) -> str:
    validCreds = creds.username in ALLOWED_USERS and creds.password == ALLOWED_USERS[creds.username]

    if not validCreds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username


# Command request models
# ------------------------
class CommandRequest(BaseModel):
    command: Literal["GETS", "STREAM", "STOP", "CONTROL"]
    args: list[str] = []


class CommandResponse(BaseModel):
    status: str
    message: str


class AutoDiscoveryConfig(BaseModel):
    enabled: bool
    intervalSeconds: float

class CameraInfo(BaseModel):
    ip: str
    hostname: str
    stream_path: str


class CameraList(BaseModel):
    cameras: list[CameraInfo]

class KasaDeviceInfo(BaseModel):
    alias: str
    host: str
    model: str
    active: bool

# API Endpoints
# ------------------------


@app.get("/")  # Define a GET endpoint at the root “/”
async def readRoot() -> dict:
    return {"message": "Welcome to the Prop Control API! Authenticate through the /auth endpoint."}


@app.get("/auth")  # Define a GET endpoint at “/auth”
async def readAuth(user: Annotated[str, Depends(authUser)]) -> dict:
    if user == "noah":
        return {"message": "Welcome back Mr Stark!"}

    return {"message": f"Authenticated as, {user}!"}


@app.get("/health")
async def getHealth() -> dict:
    return {"message": "The server is alive!"}


@app.post(
    "/v1/command",
    summary="Send a command to the devices on the network",
)  # Define a POST endpoint for device commands at “/command”
async def sendDeviceCommand(
    cmd: CommandRequest,
    bgTasks: BackgroundTasks,
) -> CommandResponse:
    ml.slog(f"Command sent: '{cmd.command} {cmd.args}'")

    # Map the relevant command name to their functions
    commandMap: dict[str, Callable] = {
        "GETS": deviceTools.getSingle,
        "STREAM": deviceTools.startStreaming,
        "STOP": deviceTools.stopStreaming,
        "CONTROL": deviceTools.setControl,
    }

    devices = deviceTools.getRegisteredDevices()

    func = commandMap.get(cmd.command)
    if func is None:
        raise HTTPException(400, f"Unknown command {cmd.command!r}")

    # Track if any commands were sent to at least one device, to avoid returning success if all targets were invalid
    anyCommandsSent = False

    # Run the command in the background to not block the API
    for device in devices.values():
        if cmd.command == "STREAM":
            # Convert frequency argument to int
            if not cmd.args:
                raise HTTPException(400, "STREAM requires a frequency argument")
            try:
                freq = int(cmd.args[0])
                anyCommandsSent = True
                bgTasks.add_task(func, device, freq)
            except ValueError:
                raise HTTPException(400, f"STREAM frequency must be an integer, got '{cmd.args[0]}'")
        elif cmd.command == "CONTROL":
            # CONTROL needs controlName and controlState
            if len(cmd.args) < 2:
                raise HTTPException(400, "CONTROL requires control name and state")

            controlName = cmd.args[0].upper()
            controlState = cmd.args[1].upper()

            if not isinstance(device, SensorMonitor) or controlName not in device.controls:
                continue

            anyCommandsSent = True
            bgTasks.add_task(func, device, controlName, controlState)
        else:
            anyCommandsSent = True
            bgTasks.add_task(func, device, *cmd.args)

    if not anyCommandsSent:
        raise HTTPException(400, "No valid target devices for the command")

    return CommandResponse(
        status="sent",
        message=f"Command '{cmd.command}' with args: {cmd.args} sent to {', '.join(device.name for device in devices.values())} devices.",
    )


@app.get("/v1/cameras", summary="Get the list of connected cameras")
async def getCameras() -> CameraList:
    cameras = cameraTools.cameraRegistry

    cameraDataList = []

    for cam in cameras.values():
        cameraData = CameraInfo(ip=cam.address, hostname=cam.hostname, stream_path=f"/{cam.address}")
        cameraDataList.append(cameraData)

    return CameraList(cameras=cameraDataList)


@app.post("/v1/cameras/reconnect", summary="Reconnect all cameras")
async def reconnectCameras() -> CameraList:
    ml.slog(f"User sent camera reconnect")
    await cameraTools.connectAllCameras()
    return await getCameras()


@app.post("/v1/camera", summary="Control a camera's movement")
async def controlCamera(
    ip: str,
    x_movement: float,
    y_movement: float,
    bgTasks: BackgroundTasks,
) -> CommandResponse:

    ml.slog(f"User sent camera move command to {ip}: <{x_movement}, {y_movement}>")

    bgTasks.add_task(
        cameraTools.moveCamera,
        ip,
        x_movement,
        y_movement,
    )

    return CommandResponse(
        status="sent",
        message=f"User sent camera move command to {ip}: <{x_movement}, {y_movement}>",
    )

@app.get("/v1/kasa", summary="Get the list of discovered Kasa devices")
async def getKasaDevices() -> list[KasaDeviceInfo]:
    devices = list(kasaTools.kasaRegistry.values())

    deviceDataList = []

    try:
        for dev in devices:
            await dev.update()  # Update device info before reporting
            alias = dev.alias if dev.alias is not None else ""
            deviceDataList.append(KasaDeviceInfo(alias=alias, host=dev.host, model=dev.model, active=dev.is_on))

        return deviceDataList
    except Exception as e:
        ml.elog(f"Failed to get Kasa device info: {e}")
        raise HTTPException(500, "Failed to get Kasa device info")

@app.get("/v1/kasa/discover", summary="Discover Kasa devices on the network")
async def discoverKasaDevices() -> list[KasaDeviceInfo]:
    ml.slog(f"User sent Kasa discover command")
    await kasaTools.discoverKasaDevices()

    return await getKasaDevices()

@app.post("/v1/kasa", summary="Control a Kasa device's power state")
async def controlKasaDevice(
    host: str,
    active: bool,
) -> KasaDeviceInfo:

    ml.slog(f"User sent Kasa control command to {host}: active={active}")

    if host not in kasaTools.kasaRegistry:
        raise HTTPException(404, f"No Kasa device found at {host}")

    try:
        await kasaTools.setKasaDeviceState(host, active)

        dev = kasaTools.kasaRegistry[host]
        alias = dev.alias if dev.alias is not None else ""
        return KasaDeviceInfo(alias=alias, host=dev.host, model=dev.model, active=dev.is_on)
    except Exception as e:
        ml.slog(f"Error while controlling Kasa device at {host} (active={active}): {repr(e)}")
        raise HTTPException(500, f"Failed to control Kasa device at {host}")


@app.get("/v1/autodiscovery", summary="Get autodiscovery settings")
async def getAutodiscoverySettings() -> AutoDiscoveryConfig:
    return AutoDiscoveryConfig(
        enabled=deviceTools.AUTODISCOVER_ENABLED,
        intervalSeconds=deviceTools.AUTODISCOVER_INTERVAL_S,
    )


@app.post("/v1/autodiscovery", summary="Update autodiscovery settings")
async def updateAutodiscoverySettings(
    enabled: bool | None = None,
    intervalSeconds: float | None = None,
) -> AutoDiscoveryConfig:
    if intervalSeconds is not None and intervalSeconds <= 0:
        raise HTTPException(400, "intervalSeconds must be greater than 0")

    if enabled is not None:
        deviceTools.AUTODISCOVER_ENABLED = enabled

    if intervalSeconds is not None:
        deviceTools.AUTODISCOVER_INTERVAL_S = intervalSeconds

    ml.slog(
        f"User updated autodiscovery: enabled={deviceTools.AUTODISCOVER_ENABLED}, "
        f"intervalSeconds={deviceTools.AUTODISCOVER_INTERVAL_S}s"
    )

    return AutoDiscoveryConfig(
        enabled=deviceTools.AUTODISCOVER_ENABLED,
        intervalSeconds=deviceTools.AUTODISCOVER_INTERVAL_S,
    )


class ConfigsResponse(BaseModel):
    count: int
    configs: dict[str, dict[str, Any]]


@app.get("/config", summary="Get the sensor and control config", response_model=ConfigsResponse)
async def getServerConfig() -> ConfigsResponse:
    configs: dict[str, dict] = {}
    for dev in deviceTools.deviceRegistry.values():
        cfg = dev.jsonConfig
        if isinstance(cfg, str):
            cfg = json.loads(cfg)  # avoid double-encoding
        configs[getattr(dev, "name", getattr(dev, "id", "unknown"))] = cfg
    return ConfigsResponse(count=len(configs), configs=configs)

@app.get("/status", summary="Gets the current state of each valve. Status is reported to redis log channel.")
async def getStatus() -> None:
    devices = deviceTools.getRegisteredDevices()

    # Trigger a status request to all devices to get their latest states for the response and to log to the redis log channel
    for device in devices.values():
        await deviceTools.getStatus(device)


@dataclass
class AudioRecordingState:
    mumble: Mumble | None = None
    wav: Wave_write | None = None
    file_name: str | None = None
    lock: Lock = field(default_factory=Lock)

def getAudioRecordingState(request: Request) -> AudioRecordingState:
    state = getattr(request.app.state, "audio_recording", None)
    if state is None:
        state = AudioRecordingState()
        request.app.state.audio_recording = state
    return state

@app.post("/v1/audio/start")
def start(request: Request) -> dict[str, str]:
    audioState = getAudioRecordingState(request)

    with audioState.lock:
        if audioState.mumble is not None:
            return {"error": "already recording"}

        MUMBLE_HOST = config.serverConfig["services"]["mumble"]["ip"]
        MUMBLE_PORT = config.serverConfig["services"]["mumble"]["port"]
        MUMBLE_TEMP_RECORDING_DIR = config.serverConfig["services"]["mumble"]["temp_recording_dir"]

        mumble, wav, file_name = mumbleRecording.start_recording(MUMBLE_HOST, MUMBLE_PORT, "", MUMBLE_TEMP_RECORDING_DIR)
        audioState.mumble = mumble
        audioState.wav = wav
        audioState.file_name = file_name

        return {"status": "started"}

@app.post("/v1/audio/stop")
def stop(request: Request) -> dict[str, str | None]:
    audioState = getAudioRecordingState(request)

    MUMBLE_TEMP_RECORDING_DIR = config.serverConfig["services"]["mumble"]["temp_recording_dir"]
    MUMBLE_RECORDING_DIR = config.serverConfig["services"]["mumble"]["recording_dir"]

    with audioState.lock:
        if audioState.mumble is None or audioState.wav is None:
            return {"error": "not recording"}

        file_name = audioState.file_name

        mumbleRecording.stop_recording(audioState.mumble, audioState.wav, MUMBLE_TEMP_RECORDING_DIR, MUMBLE_RECORDING_DIR, file_name if file_name else "recording-unknown")

        audioState.mumble = None
        audioState.wav = None
        audioState.file_name = None

        return {"status": "stopped", "file": file_name}


@app.get("/v1/audio/files")
def list_recordings() -> dict[str, list[dict[str, str]]]:
    MUMBLE_RECORDING_DIR = config.serverConfig["services"]["mumble"]["recording_dir"]
    RECORDINGS_DIR = Path(MUMBLE_RECORDING_DIR)

    files = [
        {
            "filename": f.name,
            "download_path": f"/v1/audio/files/{f.name}",
        }
        for f in RECORDINGS_DIR.iterdir()
        if f.suffix == ".opus"
    ]

    # Sort files by modified time, newest first
    files.sort(key=lambda f: (RECORDINGS_DIR / f["filename"]).stat().st_mtime, reverse=True)
    return {"files": files}

@app.get("/v1/audio/files/{filename}")
def download_recording(filename: str) -> FileResponse:
    MUMBLE_RECORDING_DIR = config.serverConfig["services"]["mumble"]["recording_dir"]
    RECORDINGS_DIR = Path(MUMBLE_RECORDING_DIR)

    path = (RECORDINGS_DIR / filename).resolve()
    if not str(path).startswith(str(RECORDINGS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="audio/opus", filename=filename)

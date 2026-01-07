import json
from typing import TYPE_CHECKING, Annotated, Any, Literal

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from libqretprop import mylogging as ml
from libqretprop.DeviceControllers import cameraTools, deviceTools


if TYPE_CHECKING:
    from collections.abc import Callable


app = FastAPI()
security = HTTPBasic()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Hardcoded creds
ALLOWED_USERS = {
    "noah":  "stinkylion",
    "admin": "propteambestteam",
}

async def startAPI() -> None:
    """Start the FastAPI server."""
    config = uvicorn.Config(
         app, host="0.0.0.0", port=8000,
            loop="asyncio", log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


def authUser(creds: HTTPBasicCredentials = Depends(security)) -> str:
    validCreds = (
        creds.username in ALLOWED_USERS and creds.password == ALLOWED_USERS[creds.username]
    )

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

class CameraInfo(BaseModel):
    ip: str
    stream_path: str

class CameraList(BaseModel):
    cameras: list[CameraInfo]

# API Endpoints
# ------------------------

@app.get("/")               # Define a GET endpoint at the root “/”
async def readRoot() -> dict:
    return {"message": "Welcome to the Prop Control API! Authenticate through the /auth endpoint."}

@app.get("/auth")           # Define a GET endpoint at “/auth”
async def readAuth(user: Annotated[str, Depends(authUser)]) -> dict:
    if user == "noah":
        return {"message": "Welcome back Mr Stark!"}

    return {"message": f"Authenticated as, {user}!"}

@app.get("/health")
async def getHealth() -> dict:
    return {"message": "The server is alive!"}

@app.post("/v1/command",
          summary="Send a command to the devices on the network",
          dependencies=[Depends(authUser)],
          )  # Define a POST endpoint for device commands at “/command”
async def sendDeviceCommand(
    cmd: CommandRequest,
    bgTasks: BackgroundTasks,
    user: Annotated[str, Depends(authUser)],
) -> CommandResponse:

    ml.slog(f"User: {user} sent '{cmd.command} {cmd.args}'")

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

    # Run the command in the background to not block the API
    for device in devices.values():
        bgTasks.add_task(run_in_threadpool, func, device, *cmd.args)

    return CommandResponse(
        status="sent",
        message=f"User {user} sent command '{cmd.command}' with args: {cmd.args} to {', '.join(device.name for device in devices.values())} devices.",
    )

@app.get("/v1/cameras", summary="Get the list of connected cameras", dependencies=[Depends(authUser)])
async def getCameras() -> CameraList:
    cameras = cameraTools.cameraRegistry

    cameraDataList = []

    for cam in cameras.values():
        cameraData = CameraInfo(ip=cam.address, stream_path=f"/{cam.address}")
        cameraDataList.append(cameraData)

    return CameraList(cameras=cameraDataList)

@app.post("/v1/cameras/reconnect", summary="Reconnect all cameras", dependencies=[Depends(authUser)])
async def reconnectCameras(user: Annotated[str, Depends(authUser)]) -> CameraList:
    ml.slog(f"User {user} sent camera reconnect")
    await cameraTools.connectAllCameras()
    return await getCameras()


@app.post("/v1/camera", summary="Control a camera's movement",
          dependencies=[Depends(authUser)])
async def controlCamera(
    ip: str,
    x_movement: float,
    y_movement: float,
    bgTasks: BackgroundTasks,
    user: Annotated[str, Depends(authUser)],
) -> CommandResponse:

    ml.slog(f"User {user} sent camera move command to {ip}: <{x_movement}, {y_movement}>")

    bgTasks.add_task(
        cameraTools.moveCamera,
        ip,
        x_movement,
        y_movement,
    )

    return CommandResponse(
        status="sent",
        message=f"User {user} sent camera move command to {ip}: <{x_movement}, {y_movement}>",
    )


class ConfigsResponse(BaseModel):
    count: int
    configs: dict[str, dict[str, Any]]

@app.get("/config", summary="Get the sensor and control config",
         response_model=ConfigsResponse,
         dependencies=[Depends(authUser)])
async def getServerConfig() -> ConfigsResponse:
    configs: dict[str, dict] = {}
    for dev in deviceTools.deviceRegistry.values():
        cfg = dev.jsonConfig
        if isinstance(cfg, str):
            cfg = json.loads(cfg)   # avoid double-encoding
        configs[getattr(dev, "name", getattr(dev, "id", "unknown"))] = cfg
    return ConfigsResponse(count=len(configs), configs=configs)

class StatusResponse(BaseModel):
    status: dict[str, str]

@app.get("/status", summary="Gets the current state of each valve. Status is reported to redis log channel.")
async def getStatus() -> None:
    for device in deviceTools.deviceRegistry.values():
        deviceTools.getStatus(device)

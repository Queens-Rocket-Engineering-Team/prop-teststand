import logging
import time
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import quote

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from libqretprop.runtime.services import RuntimeServices


logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from collections.abc import Callable


app = FastAPI()
security = HTTPBasic()


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Middleware to log requests instead of writing access logs to stdout."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_s = time.perf_counter() - start_time
        duration_ms = duration_s * 1000

        runtime = getattr(request.app.state, "runtime", None)
        metrics = getattr(runtime, "metrics", None)
        if metrics is not None:
            metrics.observe_http_request(request.method, request.url.path, response.status_code, duration_s)

        client = request.client
        if client is None:
            client_host = "unknown"
            client_port = "unknown"
        else:
            client_host = client.host
            client_port = str(client.port)

        logger.info(
            f'{client_host}:{client_port} - "{request.method} {request.url.path} HTTP/1.1" {response.status_code} ({duration_ms:.0f}ms)',
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


async def start_api(runtime: RuntimeServices) -> None:
    """Start the FastAPI server."""
    app.state.runtime = runtime
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


def auth_user(creds: HTTPBasicCredentials = Depends(security)) -> str:
    valid_creds = creds.username in ALLOWED_USERS and creds.password == ALLOWED_USERS[creds.username]

    if not valid_creds:
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
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    interval_seconds: float = Field(alias="intervalSeconds")


class CameraInfo(BaseModel):
    ip: str
    hostname: str
    stream_path: str
    recording: bool


class CameraList(BaseModel):
    cameras: list[CameraInfo]


class CameraRecordingFileInfo(BaseModel):
    filename: str
    camera_ip: str | None
    camera_hostname: str | None
    size_bytes: int
    modified_unix_ms: int
    download_path: str


class CameraRecordingList(BaseModel):
    recordings: list[CameraRecordingFileInfo]


class KasaDeviceInfo(BaseModel):
    alias: str
    host: str
    model: str
    active: bool


# API Endpoints
# ------------------------


@app.get("/")  # Define a GET endpoint at the root “/”
async def read_root() -> dict:
    return {"message": "Welcome to the Prop Control API! Authenticate through the /auth endpoint."}


@app.get("/auth")  # Define a GET endpoint at “/auth”
async def read_auth(user: Annotated[str, Depends(auth_user)]) -> dict:
    if user == "noah":
        return {"message": "Welcome back Mr Stark!"}

    return {"message": f"Authenticated as, {user}!"}


@app.get("/health")
async def get_health() -> dict:
    return {"message": "The server is alive!"}


@app.get("/v1/state", summary="Get a structured snapshot of server state")
async def get_state(request: Request) -> dict[str, Any]:
    return request.app.state.runtime.system_state.to_dict()


@app.get("/v1/metrics", summary="Get live server metrics diagnostics")
async def get_metrics(request: Request) -> dict[str, object]:
    return request.app.state.runtime.metrics.to_dict()


@app.websocket("/ws/state")
async def websocket_state(websocket: WebSocket) -> None:
    await websocket.app.state.runtime.state_stream.handle_client(websocket)


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    await websocket.app.state.runtime.log_stream.handle_client(websocket)


@app.websocket("/ws/telemetry/raw")
async def websocket_raw_telemetry(websocket: WebSocket) -> None:
    await websocket.app.state.runtime.telemetry_stream.handle_client(websocket)


@app.websocket("/ws/telemetry/display")
async def websocket_display_telemetry(websocket: WebSocket) -> None:
    await websocket.app.state.runtime.telemetry_display_stream.handle_client(websocket)


@app.post(
    "/v1/command",
    summary="Send a command to the devices on the network",
)  # Define a POST endpoint for device commands at “/command”
async def send_device_command(
    request: Request,
    cmd: CommandRequest,
    bg_tasks: BackgroundTasks,
) -> CommandResponse:
    rt = request.app.state.runtime
    logger.info(f"Command sent: '{cmd.command} {cmd.args}'")

    # Map the relevant command name to their functions
    command_map: dict[str, Callable] = {
        "GETS": rt.esp_runtime.get_single,
        "STREAM": rt.esp_runtime.start_streaming,
        "STOP": rt.esp_runtime.stop_streaming,
        "CONTROL": rt.esp_runtime.set_control,
    }

    devices = rt.esp_runtime.get_registered_devices()

    func = command_map.get(cmd.command)
    if func is None:
        raise HTTPException(400, f"Unknown command {cmd.command!r}")

    # Track if any commands were sent to at least one device, to avoid returning success if all targets were invalid
    any_commands_sent = False

    # Run the command in the background to not block the API
    for device in devices.values():
        if cmd.command == "STREAM":
            # Convert frequency argument to int
            if not cmd.args:
                raise HTTPException(400, "STREAM requires a frequency argument")
            try:
                freq = int(cmd.args[0])
                any_commands_sent = True
                bg_tasks.add_task(func, device, freq)
            except ValueError:
                raise HTTPException(400, f"STREAM frequency must be an integer, got '{cmd.args[0]}'")
        elif cmd.command == "CONTROL":
            # CONTROL needs control_name and control_state
            if len(cmd.args) < 2:
                raise HTTPException(400, "CONTROL requires control name and state")

            control_name = cmd.args[0].upper()
            control_state = cmd.args[1].upper()

            if control_name not in device.controls:
                continue

            any_commands_sent = True
            bg_tasks.add_task(func, device, control_name, control_state)
        else:
            any_commands_sent = True
            bg_tasks.add_task(func, device, *cmd.args)

    if not any_commands_sent:
        raise HTTPException(400, "No valid target devices for the command")

    return CommandResponse(
        status="sent",
        message=f"Command '{cmd.command}' with args: {cmd.args} sent to {', '.join(device.name for device in devices.values())} devices.",
    )


@app.get("/v1/cameras", summary="Get the list of connected cameras")
async def get_cameras(request: Request) -> CameraList:
    cameras = request.app.state.runtime.camera_runtime.cameras()

    camera_data_list = []

    for cam in cameras:
        camera_data = CameraInfo(ip=cam.address, hostname=cam.hostname, stream_path=cam.stream_path, recording=cam.recording)
        camera_data_list.append(camera_data)

    return CameraList(cameras=camera_data_list)


@app.post("/v1/cameras/reconnect", summary="Reconnect all cameras")
async def reconnect_cameras(request: Request) -> CameraList:
    logger.info("User sent camera reconnect")
    await request.app.state.runtime.camera_runtime.connect_all_cameras()
    return await get_cameras(request)


@app.post("/v1/camera", summary="Control a camera's movement")
async def control_camera(
    request: Request,
    ip: str,
    x_movement: float,
    y_movement: float,
    bg_tasks: BackgroundTasks,
) -> CommandResponse:

    logger.info(f"User sent camera move command to {ip}: <{x_movement}, {y_movement}>")

    bg_tasks.add_task(
        request.app.state.runtime.camera_runtime.move_camera,
        ip,
        x_movement,
        y_movement,
    )

    return CommandResponse(
        status="sent",
        message=f"User sent camera move command to {ip}: <{x_movement}, {y_movement}>",
    )


@app.post("/v1/camera/recordings/start", summary="Start recording a camera's stream")
async def start_camera_recording(
    request: Request,
    ip: str,
) -> CommandResponse:

    logger.info(f"User sent camera recording start command to {ip}")

    try:
        await request.app.state.runtime.camera_runtime.start_camera_recording(ip)
    except Exception as e:
        raise HTTPException(500, f"Failed to start recording for camera at {ip}") from e

    return CommandResponse(
        status="sent",
        message=f"User sent camera recording start command to {ip}",
    )


@app.post("/v1/camera/recordings/stop", summary="Stop recording a camera's stream")
async def stop_camera_recording(
    request: Request,
    ip: str,
) -> CommandResponse:

    logger.info(f"User sent camera recording stop command to {ip}")

    try:
        await request.app.state.runtime.camera_runtime.stop_camera_recording(ip)
    except Exception as e:
        raise HTTPException(500, f"Failed to stop recording for camera at {ip}") from e

    return CommandResponse(
        status="sent",
        message=f"User sent camera recording stop command to {ip}",
    )


@app.get("/v1/camera/recordings", summary="List camera recordings available for download")
async def list_camera_recordings(request: Request, ip: str | None = None) -> CameraRecordingList:
    try:
        camera_recording_files = request.app.state.runtime.camera_runtime.list_recording_files(ip)
    except Exception as e:
        logger.error(f"Failed to list camera recordings: {e}")
        raise HTTPException(500, "Failed to list camera recordings") from e

    camera_recordings = [
        CameraRecordingFileInfo(
            filename=camera_recording["filename"],
            camera_ip=camera_recording["camera_ip"],
            camera_hostname=camera_recording["camera_hostname"],
            size_bytes=camera_recording["size_bytes"],
            modified_unix_ms=camera_recording["modified_unix_ms"],
            download_path=f"/v1/camera/recordings/download/{quote(camera_recording['filename'])}",
        )
        for camera_recording in camera_recording_files
    ]

    return CameraRecordingList(recordings=camera_recordings)


@app.get("/v1/camera/recordings/download/{filename}", summary="Download a camera recording file")
async def download_camera_recording(request: Request, filename: str) -> FileResponse:
    try:
        file_path = request.app.state.runtime.camera_runtime.get_recording_file_path(filename)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:
        logger.error(f"Failed to load recording file '{filename}': {e}")
        raise HTTPException(500, "Failed to open recording file") from e

    return FileResponse(path=file_path, media_type="video/mp4", filename=file_path.name)


@app.get("/v1/kasa", summary="Get the list of discovered Kasa devices")
async def get_kasa_devices(request: Request) -> list[KasaDeviceInfo]:
    device_data_list = []

    try:
        for dev in await request.app.state.runtime.kasa_runtime.get_devices():
            alias = dev.alias if dev.alias is not None else ""
            device_data_list.append(KasaDeviceInfo(alias=alias, host=dev.host, model=dev.model, active=dev.is_on))

        return device_data_list
    except Exception as e:
        logger.error(f"Failed to get Kasa device info: {e}")
        raise HTTPException(500, "Failed to get Kasa device info")


@app.get("/v1/kasa/discover", summary="Discover Kasa devices on the network")
async def discover_kasa_devices(request: Request) -> list[KasaDeviceInfo]:
    logger.info("User sent Kasa discover command")
    await request.app.state.runtime.kasa_runtime.discover_kasa_devices()

    return await get_kasa_devices(request)


@app.post("/v1/kasa", summary="Control a Kasa device's power state")
async def control_kasa_device(
    request: Request,
    host: str,
    active: bool,
) -> KasaDeviceInfo:

    logger.info(f"User sent Kasa control command to {host}: active={active}")

    kasa_runtime = request.app.state.runtime.kasa_runtime

    if kasa_runtime.get_device(host) is None:
        raise HTTPException(404, f"No Kasa device found at {host}")

    try:
        dev = await kasa_runtime.set_kasa_device_state(host, active)
        alias = dev.alias if dev.alias is not None else ""
        return KasaDeviceInfo(alias=alias, host=dev.host, model=dev.model, active=dev.is_on)
    except Exception as e:
        logger.error(f"Error while controlling Kasa device at {host} (active={active}): {repr(e)}")
        raise HTTPException(500, f"Failed to control Kasa device at {host}")


@app.get("/v1/autodiscovery", summary="Get autodiscovery settings")
async def get_autodiscovery_settings(request: Request) -> AutoDiscoveryConfig:
    ds = request.app.state.runtime.discovery_service
    return AutoDiscoveryConfig(
        enabled=ds.periodic_enabled,
        intervalSeconds=ds.periodic_interval_s,
    )


@app.post("/v1/autodiscovery", summary="Update autodiscovery settings")
async def update_autodiscovery_settings(
    request: Request,
    enabled: bool | None = None,
    interval_seconds: Annotated[float | None, Query(alias="intervalSeconds")] = None,
) -> AutoDiscoveryConfig:
    if interval_seconds is not None and interval_seconds <= 0:
        raise HTTPException(400, "intervalSeconds must be greater than 0")

    ds = request.app.state.runtime.discovery_service

    if enabled is not None:
        ds.periodic_enabled = enabled

    if interval_seconds is not None:
        ds.periodic_interval_s = interval_seconds

    logger.info(f"User updated autodiscovery: enabled={ds.periodic_enabled}, intervalSeconds={ds.periodic_interval_s}s")

    return AutoDiscoveryConfig(
        enabled=ds.periodic_enabled,
        intervalSeconds=ds.periodic_interval_s,
    )


@app.post("/v1/discover", summary="Send a SSP discover request for new ESP Devices")
async def discover_devices(request: Request) -> CommandResponse:
    logger.info("User sent device discover command")
    request.app.state.runtime.discovery_service.discover()
    return CommandResponse(
        status="sent",
        message="Discovery broadcast sent. Devices will auto-connect.",
    )


@app.post("/v1/estop", summary="Emergency stop - stops all streaming and control commands immediately")
async def emergency_stop(request: Request) -> None:
    rt = request.app.state.runtime
    devices = rt.esp_runtime.get_registered_devices()
    for device in devices.values():
        await rt.esp_runtime.emergency_stop(device)


class ConfigsResponse(BaseModel):
    count: int
    configs: dict[str, dict[str, Any]]


@app.get("/config", summary="Get the sensor and control config", response_model=ConfigsResponse)
async def get_device_configs(request: Request) -> ConfigsResponse:
    rt = request.app.state.runtime
    configs: dict[str, dict] = {}
    for dev in rt.esp_runtime.get_registered_devices().values():
        configs[getattr(dev, "name", getattr(dev, "id", "unknown"))] = dev.qlcp_config.raw_config
    return ConfigsResponse(count=len(configs), configs=configs)


@app.get("/status", summary="Gets the current state of each valve. Status is reported to the log stream.")
async def get_status(request: Request) -> None:
    rt = request.app.state.runtime
    devices = rt.esp_runtime.get_registered_devices()

    # Trigger a status request to all devices to refresh their latest states.
    for device in devices.values():
        await rt.esp_runtime.get_status(device)


@app.post("/v1/audio/start")
def start(request: Request) -> dict[str, str]:
    return request.app.state.runtime.audio_runtime.start()

@app.post("/v1/audio/stop")
def stop(request: Request) -> dict[str, str | None]:
    return request.app.state.runtime.audio_runtime.stop()


@app.get("/v1/audio/files")
def list_recordings(request: Request) -> dict[str, list[dict[str, str]]]:
    return {"files": request.app.state.runtime.audio_runtime.list_recordings()}

@app.get("/v1/audio/files/{filename}")
def download_recording(request: Request, filename: str) -> FileResponse:
    try:
        path = request.app.state.runtime.audio_runtime.get_recording_path(filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return FileResponse(path, media_type="audio/opus", filename=filename)

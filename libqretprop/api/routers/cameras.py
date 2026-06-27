import logging
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from libqretprop.api.models import CommandResponse


logger = logging.getLogger(__name__)
router = APIRouter(tags=["cameras"])


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


@router.get("/v1/cameras", summary="Get the list of connected cameras")
async def get_cameras(request: Request) -> CameraList:
    cameras = request.app.state.runtime.camera_runtime.cameras()

    camera_data_list = []

    for cam in cameras:
        camera_data = CameraInfo(ip=cam.address, hostname=cam.hostname, stream_path=cam.stream_path, recording=cam.recording)
        camera_data_list.append(camera_data)

    return CameraList(cameras=camera_data_list)


@router.post("/v1/cameras/reconnect", summary="Reconnect all cameras")
async def reconnect_cameras(request: Request) -> CameraList:
    logger.info("User sent camera reconnect")
    await request.app.state.runtime.camera_runtime.connect_all_cameras()
    return await get_cameras(request)


@router.post("/v1/camera", summary="Control a camera's movement")
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


@router.post("/v1/camera/recordings/start", summary="Start recording a camera's stream")
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


@router.post("/v1/camera/recordings/stop", summary="Stop recording a camera's stream")
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


@router.get("/v1/camera/recordings", summary="List camera recordings available for download")
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


@router.get("/v1/camera/recordings/download/{filename}", summary="Download a camera recording file")
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


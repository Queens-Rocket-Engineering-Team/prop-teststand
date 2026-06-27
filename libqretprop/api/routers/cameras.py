import logging
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from libqretprop.api.deps import get_runtime
from libqretprop.api.models import CommandResponse
from libqretprop.runtime.services import RuntimeServices


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
async def get_cameras(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> CameraList:
    return CameraList(cameras=[
        CameraInfo(ip=cam.address, hostname=cam.hostname, stream_path=cam.stream_path, recording=cam.recording)
        for cam in rt.camera_runtime.cameras()
    ])


@router.post("/v1/cameras/reconnect", summary="Reconnect all cameras")
async def reconnect_cameras(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> CameraList:
    logger.info("User sent camera reconnect")
    await rt.camera_runtime.connect_all_cameras()
    return CameraList(cameras=[
        CameraInfo(ip=cam.address, hostname=cam.hostname, stream_path=cam.stream_path, recording=cam.recording)
        for cam in rt.camera_runtime.cameras()
    ])


@router.post("/v1/camera", summary="Control a camera's movement")
async def control_camera(
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
    ip: str,
    x_movement: float,
    y_movement: float,
    bg_tasks: BackgroundTasks,
) -> CommandResponse:
    logger.info(f"User sent camera move command to {ip}: <{x_movement}, {y_movement}>")
    bg_tasks.add_task(rt.camera_runtime.move_camera, ip, x_movement, y_movement)
    return CommandResponse(
        status="sent",
        message=f"User sent camera move command to {ip}: <{x_movement}, {y_movement}>",
    )


@router.post("/v1/camera/recordings/start", summary="Start recording a camera's stream")
async def start_camera_recording(
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
    ip: str,
) -> CommandResponse:
    logger.info(f"User sent camera recording start command to {ip}")
    try:
        await rt.camera_runtime.start_camera_recording(ip)
    except Exception as e:
        raise HTTPException(500, f"Failed to start recording for camera at {ip}") from e
    return CommandResponse(
        status="sent",
        message=f"User sent camera recording start command to {ip}",
    )


@router.post("/v1/camera/recordings/stop", summary="Stop recording a camera's stream")
async def stop_camera_recording(
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
    ip: str,
) -> CommandResponse:
    logger.info(f"User sent camera recording stop command to {ip}")
    try:
        await rt.camera_runtime.stop_camera_recording(ip)
    except Exception as e:
        raise HTTPException(500, f"Failed to stop recording for camera at {ip}") from e
    return CommandResponse(
        status="sent",
        message=f"User sent camera recording stop command to {ip}",
    )


@router.get("/v1/camera/recordings", summary="List camera recordings available for download")
def list_camera_recordings(
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
    ip: str | None = None,
) -> CameraRecordingList:
    try:
        camera_recording_files = rt.camera_runtime.list_recording_files(ip)
    except Exception as e:
        logger.error(f"Failed to list camera recordings: {e}")
        raise HTTPException(500, "Failed to list camera recordings") from e

    return CameraRecordingList(recordings=[
        CameraRecordingFileInfo(
            filename=rec["filename"],
            camera_ip=rec["camera_ip"],
            camera_hostname=rec["camera_hostname"],
            size_bytes=rec["size_bytes"],
            modified_unix_ms=rec["modified_unix_ms"],
            download_path=f"/v1/camera/recordings/download/{quote(rec['filename'])}",
        )
        for rec in camera_recording_files
    ])


@router.get("/v1/camera/recordings/download/{filename}", summary="Download a camera recording file")
async def download_camera_recording(
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
    filename: str,
) -> FileResponse:
    try:
        file_path = rt.camera_runtime.get_recording_file_path(filename)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:
        logger.error(f"Failed to load recording file '{filename}': {e}")
        raise HTTPException(500, "Failed to open recording file") from e
    return FileResponse(path=file_path, media_type="video/mp4", filename=file_path.name)


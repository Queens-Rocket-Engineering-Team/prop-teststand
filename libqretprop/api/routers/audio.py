from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from libqretprop.api.deps import get_runtime
from libqretprop.runtime.services import RuntimeServices


router = APIRouter(tags=["audio"])


@router.post("/v1/audio/start")
def start(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> dict[str, str]:
    return rt.audio_runtime.start()


@router.post("/v1/audio/stop")
def stop(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> dict[str, str | None]:
    return rt.audio_runtime.stop()


@router.get("/v1/audio/files")
def list_recordings(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> dict[str, list[dict[str, str]]]:
    return {"files": rt.audio_runtime.list_recordings()}


@router.get("/v1/audio/files/{filename}")
def download_recording(
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
    filename: str,
) -> FileResponse:
    try:
        path = rt.audio_runtime.get_recording_path(filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return FileResponse(path, media_type="audio/opus", filename=filename)

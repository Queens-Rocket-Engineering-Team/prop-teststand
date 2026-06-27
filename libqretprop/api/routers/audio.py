from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse


router = APIRouter(tags=["audio"])


@router.post("/v1/audio/start")
def start(request: Request) -> dict[str, str]:
    return request.app.state.runtime.audio_runtime.start()


@router.post("/v1/audio/stop")
def stop(request: Request) -> dict[str, str | None]:
    return request.app.state.runtime.audio_runtime.stop()


@router.get("/v1/audio/files")
def list_recordings(request: Request) -> dict[str, list[dict[str, str]]]:
    return {"files": request.app.state.runtime.audio_runtime.list_recordings()}


@router.get("/v1/audio/files/{filename}")
def download_recording(request: Request, filename: str) -> FileResponse:
    try:
        path = request.app.state.runtime.audio_runtime.get_recording_path(filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return FileResponse(path, media_type="audio/opus", filename=filename)


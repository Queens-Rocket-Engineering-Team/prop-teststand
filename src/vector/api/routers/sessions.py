import logging
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from vector.api.deps import get_runtime
from vector.runtime.services import RuntimeServices
from vector.runtime.session_archive import iter_session_zip
from vector.runtime.session_runtime import SessionConflictError


logger = logging.getLogger(__name__)
router = APIRouter(tags=["sessions"])


class SessionStartRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class SessionSummary(BaseModel):
    id: str
    name: str
    status: str
    started_unix: float | None
    size_bytes: int
    download_path: str


class SessionList(BaseModel):
    sessions: list[SessionSummary]
    # Sessions are never pruned, so an operator needs some warning before the disk that
    # telemetry ingest writes to fills up mid-test.
    free_bytes: int


def _session_dir(rt: RuntimeServices, session_id: str) -> Path:
    try:
        return rt.session_runtime.get_session_dir(session_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"Session not found: {session_id}") from e


@router.post("/v1/sessions/start", summary="Start recording a test session")
async def start_session(
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
    request: SessionStartRequest,
) -> dict[str, Any]:
    logger.info("User started recording session %r", request.name)
    try:
        return await rt.session_runtime.start(request.name)
    except SessionConflictError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        logger.exception("Failed to start recording session")
        raise HTTPException(500, f"Failed to start recording session: {e}") from e


@router.post("/v1/sessions/stop", summary="Stop the active recording session")
async def stop_session(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> dict[str, Any]:
    logger.info("User stopped the active recording session")
    try:
        return await rt.session_runtime.stop()
    except SessionConflictError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/v1/sessions", summary="List recorded sessions")
def list_sessions(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> SessionList:
    summaries = rt.session_runtime.list_sessions()
    free_bytes = shutil.disk_usage(rt.session_runtime.recordings_root).free
    return SessionList(sessions=[SessionSummary(**summary) for summary in summaries], free_bytes=free_bytes)


@router.get("/v1/sessions/{session_id}", summary="Get a session's metadata")
def get_session(rt: Annotated[RuntimeServices, Depends(get_runtime)], session_id: str) -> dict[str, Any]:
    try:
        return rt.session_runtime.read_metadata(session_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"Session not found: {session_id}") from e


@router.get("/v1/sessions/{session_id}/download", summary="Download a session as a zip archive")
def download_session(rt: Annotated[RuntimeServices, Depends(get_runtime)], session_id: str) -> StreamingResponse:
    session_dir = _session_dir(rt, session_id)
    if rt.session_runtime.is_active(session_id):
        # Files are still being written; an archive taken now would be truncated.
        raise HTTPException(409, "Session is still recording")

    # No Content-Length: entry sizes are only known once each file has been read, so the
    # response is chunked and browsers show an indeterminate progress bar.
    return StreamingResponse(
        iter_session_zip(session_dir, session_id),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{session_id}.zip"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/v1/sessions/{session_id}/files/{file_path:path}", summary="Download one file from a session")
def download_session_file(
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
    session_id: str,
    file_path: str,
) -> FileResponse:
    try:
        resolved = rt.session_runtime.resolve_session_file(session_id, file_path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"File not found: {file_path}") from e
    return FileResponse(resolved, filename=resolved.name)

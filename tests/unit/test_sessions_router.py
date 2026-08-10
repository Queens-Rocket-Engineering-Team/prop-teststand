from __future__ import annotations
import io
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from prop_teststand.api.fast_api import app
from prop_teststand.runtime.recording_paths import RecordingPaths
from prop_teststand.runtime.services import RuntimeServices
from prop_teststand.runtime.session_runtime import SessionConflictError, SessionRuntime


SESSION_ID = "2026-08-10_143005_hotfire"


class _StubSessionRuntime:
    """A SessionRuntime whose lifecycle is stubbed but whose path guards are real."""

    def __init__(self, root: Path) -> None:
        self._paths = RecordingPaths.from_config({"root": str(root), "mediamtx_container_root": "/recordings"})
        self.active_id: str | None = None
        self.start_conflict = False
        self.stop_conflict = False
        self.started_names: list[str] = []

    # Reuse the real guards and reads rather than reimplementing them here.
    recordings_root = property(lambda self: self._paths.root)
    get_session_dir = SessionRuntime.get_session_dir
    resolve_session_file = SessionRuntime.resolve_session_file
    list_sessions = SessionRuntime.list_sessions
    read_metadata = SessionRuntime.read_metadata
    _read_metadata_file = staticmethod(SessionRuntime._read_metadata_file)
    _summarize = SessionRuntime._summarize

    def is_active(self, session_id: str) -> bool:
        return self.active_id == session_id

    def status(self) -> dict[str, Any] | None:
        return None

    async def start(self, name: str) -> dict[str, Any]:
        if self.start_conflict:
            raise SessionConflictError("A recording session is already active")
        self.started_names.append(name)
        return {"id": SESSION_ID, "name": name, "status": "active"}

    async def stop(self) -> dict[str, Any]:
        if self.stop_conflict:
            raise SessionConflictError("No recording session is active")
        return {"id": SESSION_ID, "status": "completed"}


@pytest.fixture
def client(tmp_path: Path) -> Any:
    session_dir = tmp_path / SESSION_ID
    (session_dir / "video").mkdir(parents=True)
    (session_dir / "telemetry.csv").write_text("device_timestamp,source\n1.0000,MockDevice\n")
    (session_dir / "session.json").write_text('{"name": "Hot Fire", "status": "completed", "clock": {"started_unix": 1770745805.0}}')
    (session_dir / "video" / "Cam1.mp4").write_bytes(b"\x00" * 128)

    stub = _StubSessionRuntime(tmp_path)
    runtime = MagicMock(spec=RuntimeServices)
    runtime.session_runtime = stub
    app.state.runtime = runtime
    with TestClient(app) as test_client:
        test_client.stub = stub  # type: ignore[attr-defined]
        yield test_client


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_start_passes_the_name_through(client: Any) -> None:
    response = client.post("/v1/sessions/start", json={"name": "Hot Fire 3"})

    assert response.status_code == 200
    assert response.json()["name"] == "Hot Fire 3"
    assert client.stub.started_names == ["Hot Fire 3"]


def test_start_rejects_an_empty_name(client: Any) -> None:
    assert client.post("/v1/sessions/start", json={"name": ""}).status_code == 422


def test_start_conflicts_when_a_session_is_running(client: Any) -> None:
    client.stub.start_conflict = True

    response = client.post("/v1/sessions/start", json={"name": "second"})

    assert response.status_code == 409
    assert "already active" in response.json()["detail"]


def test_stop_conflicts_when_idle(client: Any) -> None:
    client.stub.stop_conflict = True

    assert client.post("/v1/sessions/stop").status_code == 409


# ---------------------------------------------------------------------------
# Listing and metadata
# ---------------------------------------------------------------------------


def test_list_reports_sessions_and_free_space(client: Any) -> None:
    body = client.get("/v1/sessions").json()

    assert [session["id"] for session in body["sessions"]] == [SESSION_ID]
    assert body["sessions"][0]["name"] == "Hot Fire"
    assert body["sessions"][0]["download_path"] == f"/v1/sessions/{SESSION_ID}/download"
    assert body["free_bytes"] > 0


def test_get_returns_the_stored_metadata(client: Any) -> None:
    assert client.get(f"/v1/sessions/{SESSION_ID}").json()["name"] == "Hot Fire"


def test_get_unknown_session_is_404(client: Any) -> None:
    assert client.get("/v1/sessions/2020-01-01_000000_nope").status_code == 404


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def test_download_streams_a_valid_archive(client: Any) -> None:
    response = client.get(f"/v1/sessions/{SESSION_ID}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == f'attachment; filename="{SESSION_ID}.zip"'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == [f"{SESSION_ID}/session.json", f"{SESSION_ID}/telemetry.csv", f"{SESSION_ID}/video/Cam1.mp4"]


def test_download_conflicts_while_the_session_is_still_recording(client: Any) -> None:
    client.stub.active_id = SESSION_ID

    response = client.get(f"/v1/sessions/{SESSION_ID}/download")

    assert response.status_code == 409
    assert "still recording" in response.json()["detail"]


@pytest.mark.parametrize("session_id", ["%2e%2e", "not-a-session", "2026-08-10_143005_HOTFIRE"])
def test_download_rejects_ids_that_are_not_session_ids(client: Any, session_id: str) -> None:
    assert client.get(f"/v1/sessions/{session_id}/download").status_code == 400


def test_download_of_a_dot_dot_id_never_resolves(client: Any) -> None:
    # The client collapses ".." before the request is sent, so this never reaches the
    # guard; assert it cannot succeed rather than asserting a particular rejection.
    assert client.get("/v1/sessions/../download").status_code == 404


def test_download_unknown_session_is_404(client: Any) -> None:
    assert client.get("/v1/sessions/2020-01-01_000000_nope/download").status_code == 404


# ---------------------------------------------------------------------------
# Single-file download
# ---------------------------------------------------------------------------


def test_single_file_download_returns_the_artifact(client: Any) -> None:
    response = client.get(f"/v1/sessions/{SESSION_ID}/files/telemetry.csv")

    assert response.status_code == 200
    assert response.text == "device_timestamp,source\n1.0000,MockDevice\n"


def test_single_file_download_reaches_into_subdirectories(client: Any) -> None:
    assert client.get(f"/v1/sessions/{SESSION_ID}/files/video/Cam1.mp4").status_code == 200


@pytest.mark.parametrize("file_path", ["../../etc/passwd", "..%2f..%2fsecret.txt"])
def test_single_file_download_rejects_traversal(client: Any, file_path: str) -> None:
    response = client.get(f"/v1/sessions/{SESSION_ID}/files/{file_path}")

    assert response.status_code in {400, 404}
    assert "passwd" not in response.text or response.status_code != 200


def test_single_file_download_missing_file_is_404(client: Any) -> None:
    assert client.get(f"/v1/sessions/{SESSION_ID}/files/nope.csv").status_code == 404

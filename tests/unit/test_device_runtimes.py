from __future__ import annotations
import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from libqretprop.config import MediaMTXConfig, MumbleConfig
from libqretprop.runtime.audio_runtime import AudioRuntime
from libqretprop.runtime.camera_runtime import CameraRuntime
from libqretprop.runtime.kasa_runtime import KasaRuntime


# ---------------------------------------------------------------------------
# AudioRuntime
# ---------------------------------------------------------------------------


def _make_audio_config(tmp_path: Path) -> MumbleConfig:
    return cast(MumbleConfig, {
        "ip": "127.0.0.1",
        "port": 64738,
        "temp_recording_dir": str(tmp_path / "tmp"),
        "recording_dir": str(tmp_path / "recordings"),
    })


def test_audio_start_raises_when_already_recording(tmp_path: Path) -> None:
    config = _make_audio_config(tmp_path)
    runtime = AudioRuntime(config)

    # Force the "already recording" branch by setting _mumble to a truthy value.
    runtime._mumble = MagicMock()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="already recording"):
        runtime.start()


def test_audio_stop_raises_when_not_recording(tmp_path: Path) -> None:
    config = _make_audio_config(tmp_path)
    runtime = AudioRuntime(config)

    with pytest.raises(RuntimeError, match="not recording"):
        runtime.stop()


def test_audio_get_recording_path_raises_value_error_on_bad_filename(tmp_path: Path) -> None:
    config = _make_audio_config(tmp_path)
    runtime = AudioRuntime(config)

    with pytest.raises(ValueError, match="Invalid filename"):
        runtime.get_recording_path("../escape.opus")


def test_audio_get_recording_path_raises_file_not_found(tmp_path: Path) -> None:
    config = _make_audio_config(tmp_path)
    runtime = AudioRuntime(config)

    with pytest.raises(FileNotFoundError, match="File not found"):
        runtime.get_recording_path("nonexistent.opus")


# ---------------------------------------------------------------------------
# CameraRuntime
# ---------------------------------------------------------------------------


def _make_camera_runtime() -> CameraRuntime:
    mediamtx = MagicMock()
    return CameraRuntime(
        mediamtx,
        cameras=[],
        camera_account={"username": "user", "password": "pass"},
        mediamtx_config=cast(MediaMTXConfig, {"recordings_dir": "/tmp/recordings"}),
    )


def test_camera_require_camera_raises_key_error() -> None:
    runtime = _make_camera_runtime()

    with pytest.raises(KeyError, match="10.0.0.99"):
        runtime._require_camera("10.0.0.99")  # type: ignore[attr-defined]


def test_camera_start_recording_raises_key_error_for_unknown_ip() -> None:

    async def run() -> None:
        runtime = _make_camera_runtime()
        with pytest.raises(KeyError):
            await runtime.start_camera_recording("10.0.0.1")

    asyncio.run(run())


def test_camera_stop_recording_raises_key_error_for_unknown_ip() -> None:

    async def run() -> None:
        runtime = _make_camera_runtime()
        with pytest.raises(KeyError):
            await runtime.stop_camera_recording("10.0.0.1")

    asyncio.run(run())


def test_camera_start_recording_raises_runtime_error_on_media_server_failure() -> None:

    async def run() -> None:
        runtime = _make_camera_runtime()

        # Register a fake camera.
        fake_camera = MagicMock()
        fake_camera.address = "10.0.0.1"
        runtime._registry["10.0.0.1"] = fake_camera  # type: ignore[assignment]

        # Make the media server return a non-200 status.
        bad_response = MagicMock()
        bad_response.status = 500
        runtime._mediamtx.set_recording = AsyncMock(return_value=bad_response)  # type: ignore[method-assign]

        try:
            with pytest.raises(RuntimeError, match="Media server API returned status 500"):
                await runtime.start_camera_recording("10.0.0.1")
        finally:
            # CameraRuntime._get_http_session() creates a real aiohttp.ClientSession;
            # close it so the test does not leak an unclosed session.
            await runtime.close()

    asyncio.run(run())


def test_camera_get_recording_file_path_raises_value_error_on_bad_filename() -> None:
    runtime = _make_camera_runtime()
    with pytest.raises(ValueError, match="Invalid"):
        runtime.get_recording_file_path("../escape.mp4")


def test_camera_get_recording_file_path_raises_file_not_found() -> None:
    runtime = _make_camera_runtime()
    with pytest.raises(FileNotFoundError):
        runtime.get_recording_file_path("nosuchfile.mp4")


# ---------------------------------------------------------------------------
# KasaRuntime
# ---------------------------------------------------------------------------


def test_kasa_get_device_returns_none_for_unknown_host() -> None:
    runtime = KasaRuntime()
    assert runtime.get_device("192.168.1.99") is None


def test_kasa_require_device_raises_key_error() -> None:
    runtime = KasaRuntime()
    with pytest.raises(KeyError, match="192.168.1.99"):
        runtime._require_device("192.168.1.99")  # type: ignore[attr-defined]


def test_kasa_set_device_state_raises_key_error_for_unknown_host() -> None:

    async def run() -> None:
        runtime = KasaRuntime()
        with pytest.raises(KeyError):
            await runtime.set_kasa_device_state("192.168.1.99", True)

    asyncio.run(run())

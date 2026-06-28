from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from libqretprop.api.fast_api import app
from libqretprop.runtime.services import RuntimeServices


# ---------------------------------------------------------------------------
# Fake runtime helpers
# ---------------------------------------------------------------------------


def _make_fake_session(*, name: str = "TEST-DEVICE", controls: dict | None = None) -> MagicMock:
    session = MagicMock()
    session.name = name
    session.controls = controls if controls is not None else {"AV101": MagicMock()}
    return session


def _make_fake_esp_runtime(sessions: list[MagicMock] | None = None) -> MagicMock:
    rt = MagicMock()
    device_map = {f"10.0.0.{i}": s for i, s in enumerate(sessions or [])}
    rt.get_registered_devices.return_value = device_map
    rt.get_single = AsyncMock()
    rt.stop_streaming = AsyncMock()
    rt.start_streaming = AsyncMock()
    rt.set_control = AsyncMock()
    rt.emergency_stop = AsyncMock()
    return rt


def _install_runtime(esp_runtime: MagicMock) -> RuntimeServices:
    """Wire a fake esp_runtime into a minimal RuntimeServices and install it on app.state."""
    rt = MagicMock(spec=RuntimeServices)
    rt.esp_runtime = esp_runtime
    app.state.runtime = rt
    return rt


# ---------------------------------------------------------------------------
# /v1/command — GET_SINGLE (GETS)
# ---------------------------------------------------------------------------


def test_gets_command_awaits_get_single() -> None:
    session = _make_fake_session()
    esp_rt = _make_fake_esp_runtime([session])
    _install_runtime(esp_rt)

    with TestClient(app) as client:
        resp = client.post("/v1/command", json={"command": "GETS"})

    assert resp.status_code == 200
    esp_rt.get_single.assert_awaited_once_with(session)


# ---------------------------------------------------------------------------
# /v1/command — STOP
# ---------------------------------------------------------------------------


def test_stop_command_awaits_stop_streaming() -> None:
    session = _make_fake_session()
    esp_rt = _make_fake_esp_runtime([session])
    _install_runtime(esp_rt)

    with TestClient(app) as client:
        resp = client.post("/v1/command", json={"command": "STOP"})

    assert resp.status_code == 200
    esp_rt.stop_streaming.assert_awaited_once_with(session)


# ---------------------------------------------------------------------------
# /v1/command — STREAM
# ---------------------------------------------------------------------------


def test_stream_command_awaits_start_streaming() -> None:
    session = _make_fake_session()
    esp_rt = _make_fake_esp_runtime([session])
    _install_runtime(esp_rt)

    with TestClient(app) as client:
        resp = client.post("/v1/command", json={"command": "STREAM", "frequency_hz": 10})

    assert resp.status_code == 200
    esp_rt.start_streaming.assert_awaited_once_with(session, 10)


# ---------------------------------------------------------------------------
# /v1/command — CONTROL
# ---------------------------------------------------------------------------


def test_control_command_awaits_set_control() -> None:
    session = _make_fake_session(controls={"AV101": MagicMock()})
    esp_rt = _make_fake_esp_runtime([session])
    _install_runtime(esp_rt)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/command",
            json={"command": "CONTROL", "control_name": "AV101", "control_state": "OPEN"},
        )

    assert resp.status_code == 200
    esp_rt.set_control.assert_awaited_once_with(session, "AV101", "OPEN")


def test_control_command_skips_device_without_matching_control() -> None:
    """When no device has the named control, the router returns 400."""
    session = _make_fake_session(controls={})  # no controls
    esp_rt = _make_fake_esp_runtime([session])
    _install_runtime(esp_rt)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/command",
            json={"command": "CONTROL", "control_name": "NONEXISTENT", "control_state": "OPEN"},
        )

    assert resp.status_code == 400
    esp_rt.set_control.assert_not_awaited()


# ---------------------------------------------------------------------------
# /v1/command — no devices registered
# ---------------------------------------------------------------------------


def test_command_returns_400_when_no_devices_registered() -> None:
    esp_rt = _make_fake_esp_runtime([])  # empty device map
    _install_runtime(esp_rt)

    with TestClient(app) as client:
        resp = client.post("/v1/command", json={"command": "GETS"})

    assert resp.status_code == 400
    assert "No valid target devices" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /v1/command — multiple devices
# ---------------------------------------------------------------------------


def test_command_is_sent_to_all_registered_devices() -> None:
    sessions = [_make_fake_session(name=f"DEVICE-{i}") for i in range(3)]
    esp_rt = _make_fake_esp_runtime(sessions)
    _install_runtime(esp_rt)

    with TestClient(app) as client:
        resp = client.post("/v1/command", json={"command": "GETS"})

    assert resp.status_code == 200
    assert esp_rt.get_single.await_count == 3


# ---------------------------------------------------------------------------
# /v1/estop
# ---------------------------------------------------------------------------


def test_estop_awaits_emergency_stop_on_each_device() -> None:
    sessions = [_make_fake_session(name=f"DEVICE-{i}") for i in range(2)]
    esp_rt = _make_fake_esp_runtime(sessions)
    _install_runtime(esp_rt)

    with TestClient(app) as client:
        resp = client.post("/v1/estop")

    assert resp.status_code == 200
    assert esp_rt.emergency_stop.await_count == 2


def test_estop_with_no_devices_is_a_noop() -> None:
    esp_rt = _make_fake_esp_runtime([])
    _install_runtime(esp_rt)

    with TestClient(app) as client:
        resp = client.post("/v1/estop")

    assert resp.status_code == 200
    esp_rt.emergency_stop.assert_not_awaited()

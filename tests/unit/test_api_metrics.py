from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from libqretprop.api import fastAPI
from libqretprop.runtime.metrics import Metrics


def test_metrics_endpoint_returns_json_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics = Metrics(time_fn=lambda: 100.0)
    metrics.record_telemetry_datagram(128, device="PANDA")
    fastAPI.app.state.runtime = SimpleNamespace(metrics=metrics)
    monkeypatch.setattr(fastAPI.ml, "slog", lambda *_args, **_kwargs: None)

    with TestClient(fastAPI.app) as client:
        response = client.get("/v1/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {
        "generated_unix_ms",
        "server",
        "telemetry",
        "commands",
        "websockets",
        "http",
        "device_lifecycle",
        "recent_events",
    }
    assert body["telemetry"]["ingest"]["by_device"]["PANDA"]["udp_bytes_total"] == 128
    assert "udp_bytes_per_s" not in body["telemetry"]["ingest"]["by_device"]["PANDA"]


def test_metrics_endpoint_is_not_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    fastAPI.app.state.runtime = SimpleNamespace(metrics=Metrics(time_fn=lambda: 100.0))
    monkeypatch.setattr(fastAPI.ml, "slog", lambda *_args, **_kwargs: None)

    with TestClient(fastAPI.app) as client:
        response = client.get("/metrics")

    assert response.status_code == 404

from __future__ import annotations
import asyncio
import contextlib
import time
from typing import Any, cast

import numpy as np
import pytest
from fastapi import WebSocket
from tsdownsample import M4Downsampler

from libqretprop.runtime.telemetry_display_stream import (
    DISPLAY_POINTS_PER_BUCKET,
    DISPLAY_TARGET_HZ,
    TelemetryDisplayStream,
    _DeviceBucket,
    _SensorBuffer,
)
from libqretprop.runtime.telemetry_ingest import TelemetryBatch, TelemetryReading


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _IdentityDownsampler:
    """Returns the first n_out indices unchanged — predictable test output."""

    def downsample(self, x: np.ndarray, y: np.ndarray, *, n_out: int) -> np.ndarray:
        return np.arange(min(len(x), n_out), dtype=np.uint64)


class FakeWebSocket:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.accepted = False
        self.closed = False
        self.sent: list[dict[str, Any]] = []
        self._fail_after = fail_after
        self._send_count = 0

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, Any]) -> None:
        if self._fail_after is not None and self._send_count >= self._fail_after:
            raise RuntimeError("websocket send failed")
        self._send_count += 1
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True


def _as_ws(ws: FakeWebSocket) -> WebSocket:
    return cast(WebSocket, ws)


def _make_reading(
    sensor_id: int = 0,
    sensor_name: str = "PT101",
    value: float = 100.0,
    unit_name: str = "PSI",
    sensor_type: str = "pressure_transducer",
) -> TelemetryReading:
    return TelemetryReading(
        sensor_id=sensor_id,
        sensor_name=sensor_name,
        value=value,
        unit_name=unit_name,
        sensor_type=sensor_type,
    )


def _make_batch(
    timestamp_s: float = 1.0,
    readings: tuple[TelemetryReading, ...] | None = None,
    device_name: str = "MockDevice",
) -> TelemetryBatch:
    return TelemetryBatch(
        device_name=device_name,
        device_address="10.0.0.1",
        connection_key="esp-1",
        timestamp_s=timestamp_s,
        readings=readings if readings is not None else (_make_reading(),),
    )


async def _connect(stream: TelemetryDisplayStream) -> FakeWebSocket:
    ws = FakeWebSocket()
    await stream.connect_client(_as_ws(ws))
    return ws


# ---------------------------------------------------------------------------
# _SensorBuffer
# ---------------------------------------------------------------------------


def test_sensor_buffer_empty_returns_no_points() -> None:
    buf = _SensorBuffer(0, "PT101", "PSI", "pressure_transducer")
    assert buf.to_points(_IdentityDownsampler(), 8) == []


def test_sensor_buffer_collects_samples() -> None:
    buf = _SensorBuffer(0, "PT101", "PSI", "pressure_transducer")
    buf.add(1.0, 10.0)
    buf.add(2.0, 20.0)
    assert buf.timestamps == [1.0, 2.0]
    assert buf.values == [10.0, 20.0]


def test_sensor_buffer_to_points_uses_downsampler() -> None:
    buf = _SensorBuffer(0, "PT101", "PSI", "pressure_transducer")
    for i in range(5):
        buf.add(float(i), float(i * 10))

    points = buf.to_points(_IdentityDownsampler(), 3)

    assert points == [{"t": 0.0, "v": 0.0}, {"t": 1.0, "v": 10.0}, {"t": 2.0, "v": 20.0}]


def test_sensor_buffer_to_points_with_m4() -> None:
    # Sanity-check the real M4 integration: enough samples, correct output shape.
    buf = _SensorBuffer(0, "PT101", "PSI", "pressure_transducer")
    for i in range(20):
        buf.add(float(i), float(i % 5))  # 20 samples with some variation

    points = buf.to_points(M4Downsampler(), n_out=8)

    assert len(points) == 8
    ts = [p["t"] for p in points]
    assert ts == sorted(ts), "M4 indices must be in ascending time order"
    assert all("t" in p and "v" in p for p in points)


# ---------------------------------------------------------------------------
# publish_batch — accumulation and bucket boundaries
# ---------------------------------------------------------------------------


def test_publish_batch_no_clients_is_noop() -> None:
    stream = TelemetryDisplayStream(downsampler=_IdentityDownsampler())
    stream.publish_batch(_make_batch(timestamp_s=1.0))
    assert stream._buckets == {}


def test_publish_batch_creates_bucket_for_device() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(downsampler=_IdentityDownsampler())
        await _connect(stream)
        stream.publish_batch(_make_batch(timestamp_s=1.0))
        assert "MockDevice" in stream._buckets

    asyncio.run(run())


def test_publish_batch_accumulates_within_bucket() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler())
        ws = await _connect(stream)
        interval = 1.0 / 30.0

        stream.publish_batch(_make_batch(timestamp_s=1.0, readings=(_make_reading(value=10.0),)))
        stream.publish_batch(_make_batch(timestamp_s=1.0 + interval * 0.5, readings=(_make_reading(value=20.0),)))

        buf = stream._buckets["MockDevice"].sensors[0]
        assert buf.values == [10.0, 20.0]
        assert stream._clients[_as_ws(ws)].qsize() == 0

    asyncio.run(run())


def test_publish_batch_emits_on_boundary_crossing() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler())
        ws = await _connect(stream)
        interval = 1.0 / 30.0

        stream.publish_batch(_make_batch(timestamp_s=1.0, readings=(_make_reading(value=42.0),)))
        stream.publish_batch(_make_batch(timestamp_s=1.0 + interval * 1.5))

        queue = stream._clients[_as_ws(ws)]
        assert queue.qsize() == 1
        msg = queue.get_nowait()
        assert msg["type"] == "telemetry.display_batch"
        assert msg["device_name"] == "MockDevice"
        assert msg["readings"][0]["points"] == [{"t": 1.0, "v": 42.0}]

    asyncio.run(run())


def test_publish_batch_broadcasts_to_all_clients() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler())
        ws1 = await _connect(stream)
        ws2 = await _connect(stream)
        interval = 1.0 / 30.0

        stream.publish_batch(_make_batch(timestamp_s=1.0))
        stream.publish_batch(_make_batch(timestamp_s=1.0 + interval * 1.5))

        assert stream._clients[_as_ws(ws1)].qsize() == 1
        assert stream._clients[_as_ws(ws2)].qsize() == 1

    asyncio.run(run())


def test_full_queue_increments_dropped_batches() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler(), max_queue=1)
        await _connect(stream)
        interval = 1.0 / 30.0

        stream.publish_batch(_make_batch(timestamp_s=0.0))
        stream.publish_batch(_make_batch(timestamp_s=interval * 1.5))  # emits bucket 0
        stream.publish_batch(_make_batch(timestamp_s=interval * 2.5))  # emits bucket 1, queue full

        assert stream.dropped_batches == 1

    asyncio.run(run())


def test_buckets_are_independent_per_device() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler())
        ws = await _connect(stream)
        interval = 1.0 / 30.0

        stream.publish_batch(_make_batch(timestamp_s=1.0, device_name="DevA"))
        stream.publish_batch(_make_batch(timestamp_s=1.0, device_name="DevB"))
        stream.publish_batch(_make_batch(timestamp_s=1.0 + interval * 1.5, device_name="DevA"))

        queue = stream._clients[_as_ws(ws)]
        assert queue.qsize() == 1
        assert queue.get_nowait()["device_name"] == "DevA"
        assert "DevB" in stream._buckets

    asyncio.run(run())


# ---------------------------------------------------------------------------
# serialize_bucket wire format
# ---------------------------------------------------------------------------


def test_serialize_bucket_wire_format() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler())
        ws = await _connect(stream)
        interval = 1.0 / 30.0

        stream.publish_batch(_make_batch(timestamp_s=1.0, readings=(_make_reading(value=42.0),)))
        stream.publish_batch(_make_batch(timestamp_s=1.0 + interval * 1.5))

        msg = stream._clients[_as_ws(ws)].get_nowait()

        assert msg["type"] == "telemetry.display_batch"
        assert msg["device_name"] == "MockDevice"
        assert msg["bucket_end_s"] > msg["bucket_start_s"]

        r = msg["readings"][0]
        assert r["sensor_id"] == 0
        assert r["sensor_name"] == "PT101"
        assert r["unit"] == "PSI"
        assert r["sensor_type"] == "pressure_transducer"
        assert "points" in r
        assert "m4" not in r

    asyncio.run(run())


# ---------------------------------------------------------------------------
# run() flush loop
# ---------------------------------------------------------------------------


def test_run_flushes_trailing_bucket() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler())
        ws = await _connect(stream)

        stream.publish_batch(_make_batch(timestamp_s=1.0))
        stream._buckets["MockDevice"].last_updated_monotonic = time.monotonic() - (1.0 / 30.0 + 0.01)

        task = asyncio.create_task(stream.run())
        await asyncio.sleep(1.0 / 30.0 + 0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert stream._clients[_as_ws(ws)].qsize() == 1
        assert stream._clients[_as_ws(ws)].get_nowait()["type"] == "telemetry.display_batch"

    asyncio.run(run())


def test_run_skips_flush_when_no_clients() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler())
        ws = await _connect(stream)

        stream.publish_batch(_make_batch(timestamp_s=1.0))
        await stream.disconnect_client(_as_ws(ws))

        # disconnect_client with no remaining clients clears buckets
        assert stream._buckets == {}

        # run() skips when no clients — add a bucket manually to confirm nothing happens
        task = asyncio.create_task(stream.run())
        await asyncio.sleep(1.0 / 30.0 + 0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert stream._buckets == {}

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------


def test_connect_client_accepts_websocket() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream()
        ws = FakeWebSocket()
        await stream.connect_client(_as_ws(ws))
        assert ws.accepted is True
        assert stream.client_count == 1

    asyncio.run(run())


def test_disconnect_last_client_clears_buckets() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler())
        ws = await _connect(stream)

        stream.publish_batch(_make_batch(timestamp_s=1.0))
        assert "MockDevice" in stream._buckets

        await stream.disconnect_client(_as_ws(ws))

        assert stream.client_count == 0
        assert stream._buckets == {}

    asyncio.run(run())


def test_disconnect_non_last_client_preserves_buckets() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler())
        ws1 = await _connect(stream)
        ws2 = await _connect(stream)

        stream.publish_batch(_make_batch(timestamp_s=1.0))
        await stream.disconnect_client(_as_ws(ws1))

        assert stream.client_count == 1
        assert "MockDevice" in stream._buckets

    asyncio.run(run())


def test_handle_client_delivers_emitted_message() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler())
        ws = FakeWebSocket()
        task = asyncio.create_task(stream.handle_client(_as_ws(ws)))
        await asyncio.sleep(0)

        interval = 1.0 / 30.0
        stream.publish_batch(_make_batch(timestamp_s=1.0))
        stream.publish_batch(_make_batch(timestamp_s=1.0 + interval * 1.5))
        await asyncio.sleep(0)

        assert ws.sent[0]["type"] == "telemetry.display_batch"

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_handle_client_cleans_up_on_send_failure() -> None:
    async def run() -> None:
        stream = TelemetryDisplayStream(target_hz=30.0, downsampler=_IdentityDownsampler())
        ws = FakeWebSocket(fail_after=0)
        task = asyncio.create_task(stream.handle_client(_as_ws(ws)))
        await asyncio.sleep(0)

        interval = 1.0 / 30.0
        stream.publish_batch(_make_batch(timestamp_s=1.0))
        stream.publish_batch(_make_batch(timestamp_s=1.0 + interval * 1.5))
        await task

        assert stream.client_count == 0
        assert ws.closed is True

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_defaults() -> None:
    assert DISPLAY_TARGET_HZ == 30.0
    assert DISPLAY_POINTS_PER_BUCKET == 8
    stream = TelemetryDisplayStream()
    assert stream._bucket_interval_s == pytest.approx(1.0 / 30.0)
    assert isinstance(stream._downsampler, M4Downsampler)

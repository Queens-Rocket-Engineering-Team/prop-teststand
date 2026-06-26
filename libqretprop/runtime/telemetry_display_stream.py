from __future__ import annotations
import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from tsdownsample import M4Downsampler

from libqretprop.runtime.metrics import NULL_METRICS, Metrics


if TYPE_CHECKING:
    from libqretprop.runtime.telemetry_ingest import TelemetryBatch

DISPLAY_TARGET_HZ = 30.0
DISPLAY_POINTS_PER_BUCKET = 8  # M4 requires >= 8 (at least 2 windows x 4 points)
STREAM_METRIC_LABEL = "telemetry_display"


class Downsampler(Protocol):
    def downsample(self, x: np.ndarray, y: np.ndarray, *, n_out: int) -> np.ndarray: ...


@dataclass(slots=True)
class _SensorBuffer:
    sensor_id: int
    sensor_name: str
    unit_name: str
    sensor_type: str
    timestamps: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)

    def add(self, t: float, v: float) -> None:
        self.timestamps.append(t)
        self.values.append(v)

    def to_points(self, downsampler: Downsampler, n_out: int) -> list[dict[str, float]]:
        if not self.timestamps:
            return []
        ts = np.asarray(self.timestamps, dtype=np.float64)
        vs = np.asarray(self.values, dtype=np.float64)
        indices = downsampler.downsample(ts, vs, n_out=n_out)
        return [{"t": float(ts[i]), "v": float(vs[i])} for i in indices]


@dataclass(slots=True)
class _DeviceBucket:
    bucket_index: int
    bucket_start_s: float
    bucket_end_s: float
    last_updated_monotonic: float
    sensors: dict[int, _SensorBuffer] = field(default_factory=dict)


class TelemetryDisplayStream:
    """Downsampled telemetry stream for the operator GUI at /ws/telemetry/display.

    Collects raw batches into fixed-width time buckets and emits downsampled
    point sets when each bucket closes. The downsampler is injected at construction
    so different algorithms can be swapped without changing the wire format.

    run() must be started as a daemon task; it flushes the trailing partial bucket
    when no boundary-crossing batch arrives within one bucket interval.
    """

    def __init__(
        self,
        *,
        target_hz: float = DISPLAY_TARGET_HZ,
        points_per_bucket: int = DISPLAY_POINTS_PER_BUCKET,
        downsampler: Downsampler | None = None,
        max_queue: int = 128,
        metrics: Metrics | None = None,
    ) -> None:
        self.metrics = metrics or NULL_METRICS
        self._bucket_interval_s = 1.0 / target_hz
        self._points_per_bucket = points_per_bucket
        self._downsampler: Downsampler = M4Downsampler() if downsampler is None else downsampler
        self._max_queue = max_queue
        self._clients: dict[WebSocket, asyncio.Queue[dict[str, Any]]] = {}
        self._buckets: dict[str, _DeviceBucket] = {}
        self._dropped_batches = 0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def dropped_batches(self) -> int:
        return self._dropped_batches

    def publish_batch(self, batch: TelemetryBatch) -> None:
        if not self._clients:
            return

        bucket_index = int(batch.timestamp_s / self._bucket_interval_s)
        now = time.monotonic()

        device_bucket = self._buckets.get(batch.device_name)
        if device_bucket is not None and bucket_index > device_bucket.bucket_index:
            self._emit_bucket(batch.device_name, device_bucket)
            device_bucket = None

        if device_bucket is None:
            device_bucket = _DeviceBucket(
                bucket_index=bucket_index,
                bucket_start_s=bucket_index * self._bucket_interval_s,
                bucket_end_s=(bucket_index + 1) * self._bucket_interval_s,
                last_updated_monotonic=now,
            )
            self._buckets[batch.device_name] = device_bucket

        device_bucket.last_updated_monotonic = now
        for reading in batch.readings:
            buf = device_bucket.sensors.get(reading.sensor_id)
            if buf is None:
                buf = _SensorBuffer(
                    sensor_id=reading.sensor_id,
                    sensor_name=reading.sensor_name,
                    unit_name=reading.unit_name,
                    sensor_type=reading.sensor_type,
                )
                device_bucket.sensors[reading.sensor_id] = buf
            buf.add(batch.timestamp_s, reading.value)

    def serialize_bucket(self, device_name: str, bucket: _DeviceBucket) -> dict[str, Any]:
        return {
            "type": "telemetry.display_batch",
            "device_name": device_name,
            "bucket_start_s": bucket.bucket_start_s,
            "bucket_end_s": bucket.bucket_end_s,
            "readings": [
                {
                    "sensor_id": s.sensor_id,
                    "sensor_name": s.sensor_name,
                    "unit": s.unit_name,
                    "sensor_type": s.sensor_type,
                    "points": s.to_points(self._downsampler, self._points_per_bucket),
                }
                for s in bucket.sensors.values()
            ],
        }

    def _emit_bucket(self, device_name: str, bucket: _DeviceBucket) -> None:
        if not bucket.sensors:
            return
        message = self.serialize_bucket(device_name, bucket)
        for queue in self._clients.values():
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self._dropped_batches += 1
                self.metrics.record_telemetry_dropped_batch(STREAM_METRIC_LABEL)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._bucket_interval_s)
            if not self._clients:
                continue
            now = time.monotonic()
            stale = [
                name for name, bucket in self._buckets.items()
                if now - bucket.last_updated_monotonic >= self._bucket_interval_s
            ]
            for name in stale:
                self._emit_bucket(name, self._buckets.pop(name))

    async def connect_client(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients[websocket] = asyncio.Queue(maxsize=self._max_queue)
        self.metrics.set_ws_clients(STREAM_METRIC_LABEL, self.client_count)

    async def disconnect_client(self, websocket: WebSocket) -> None:
        self._clients.pop(websocket, None)
        self.metrics.set_ws_clients(STREAM_METRIC_LABEL, self.client_count)
        if not self._clients:
            self._buckets.clear()
        with contextlib.suppress(Exception):
            await websocket.close()

    async def handle_client(self, websocket: WebSocket) -> None:
        await self.connect_client(websocket)
        queue = self._clients[websocket]
        try:
            while True:
                message = await queue.get()
                await websocket.send_json(message)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            await self.disconnect_client(websocket)

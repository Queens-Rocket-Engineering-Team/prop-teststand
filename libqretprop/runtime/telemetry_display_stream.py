from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
from tsdownsample import M4Downsampler

from libqretprop.runtime.metrics import NULL_METRICS, Metrics
from libqretprop.runtime.ws_fanout import BoundedWebSocketFanout


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


def _record_telemetry_drop(metrics: Metrics, stream: str) -> None:
    metrics.record_telemetry_dropped_batch(stream)


class TelemetryDisplayStream(BoundedWebSocketFanout):
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
        super().__init__(
            stream_metric_label=STREAM_METRIC_LABEL,
            max_queue=max_queue,
            metrics=metrics or NULL_METRICS,
            drop_recorder=_record_telemetry_drop,
        )
        self._bucket_interval_s = 1.0 / target_hz
        self._points_per_bucket = points_per_bucket
        self._downsampler: Downsampler = M4Downsampler() if downsampler is None else downsampler
        self._buckets: dict[str, _DeviceBucket] = {}

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
        self.publish_message(message)

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

    def _after_disconnect(self) -> None:
        if not self._clients:
            self._buckets.clear()

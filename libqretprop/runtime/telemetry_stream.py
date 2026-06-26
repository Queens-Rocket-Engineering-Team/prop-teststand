from __future__ import annotations
import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect

from libqretprop.runtime.metrics import NULL_METRICS, Metrics


if TYPE_CHECKING:
    from libqretprop.runtime.telemetry_ingest import TelemetryBatch


STREAM_METRIC_LABEL = "telemetry_raw"


class TelemetryStreamRuntime:
    """Forwards full-rate telemetry batches to ``/ws/telemetry/raw`` clients.

    Each client gets a bounded queue and an independent send loop. ``publish_batch``
    is synchronous and non-blocking so it is safe to call directly from the UDP ingest
    loop: a slow or stalled client never blocks ingest, its batches are dropped instead.
    """

    def __init__(self, *, max_queue: int = 256, metrics: Metrics | None = None) -> None:
        self.metrics = metrics or NULL_METRICS
        self._max_queue = max_queue
        self._clients: dict[WebSocket, asyncio.Queue[dict[str, Any]]] = {}
        self._dropped_batches = 0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def dropped_batches(self) -> int:
        return self._dropped_batches

    def serialize_batch(self, batch: TelemetryBatch) -> dict[str, Any]:
        return {
            "type": "telemetry.raw_batch",
            "device_name": batch.device_name,
            "device_address": batch.device_address,
            "connection_key": batch.connection_key,
            "timestamp_s": batch.timestamp_s,
            "readings": [
                {
                    "sensor_id": reading.sensor_id,
                    "sensor_name": reading.sensor_name,
                    "value": reading.value,
                    "unit": reading.unit_name,
                    "sensor_type": reading.sensor_type,
                }
                for reading in batch.readings
            ],
        }

    def publish_batch(self, batch: TelemetryBatch) -> None:
        """Queue a serialized batch to every connected client without blocking ingest."""
        if not self._clients:
            return

        message = self.serialize_batch(batch)
        for queue in self._clients.values():
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self._dropped_batches += 1
                self.metrics.record_telemetry_dropped_batch(STREAM_METRIC_LABEL)

    async def connect_client(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients[websocket] = asyncio.Queue(maxsize=self._max_queue)
        self.metrics.set_ws_clients(STREAM_METRIC_LABEL, self.client_count)

    async def disconnect_client(self, websocket: WebSocket) -> None:
        self._clients.pop(websocket, None)
        self.metrics.set_ws_clients(STREAM_METRIC_LABEL, self.client_count)
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

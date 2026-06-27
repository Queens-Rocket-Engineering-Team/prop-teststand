from __future__ import annotations
import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from libqretprop.runtime.metrics import NULL_METRICS, Metrics


JsonMessage = dict[str, Any]
DropRecorder = Callable[[Metrics, str], None]


class BoundedWebSocketFanout:
    """Bounded per-client JSON fan-out for WebSocket streams.

    Publishers stay synchronous and non-blocking. Each connected client owns a
    bounded queue; slow clients lose newest messages instead of blocking the
    runtime loop that produced the data.
    """

    def __init__(
        self,
        *,
        stream_metric_label: str,
        max_queue: int,
        metrics: Metrics | None = None,
        drop_recorder: DropRecorder | None = None,
    ) -> None:
        self.metrics = metrics or NULL_METRICS
        self._stream_metric_label = stream_metric_label
        self._max_queue = max_queue
        self._clients: dict[WebSocket, asyncio.Queue[JsonMessage]] = {}
        self._dropped_batches = 0
        self._drop_recorder = drop_recorder

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def dropped_batches(self) -> int:
        return self._dropped_batches

    def publish_message(self, message: JsonMessage) -> None:
        """Queue *message* to every connected client without blocking."""
        for queue in self._clients.values():
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self._dropped_batches += 1
                if self._drop_recorder is not None:
                    self._drop_recorder(self.metrics, self._stream_metric_label)

    async def connect_client(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients[websocket] = asyncio.Queue(maxsize=self._max_queue)
        self.metrics.set_ws_clients(self._stream_metric_label, self.client_count)

    async def disconnect_client(self, websocket: WebSocket) -> None:
        self._clients.pop(websocket, None)
        self.metrics.set_ws_clients(self._stream_metric_label, self.client_count)
        self._after_disconnect()
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

    def _after_disconnect(self) -> None:
        """Hook for subclasses that need lifecycle cleanup."""

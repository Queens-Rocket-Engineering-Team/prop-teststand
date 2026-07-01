from __future__ import annotations
import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

import orjson

from libqretprop.runtime.metrics import Metrics


if TYPE_CHECKING:
    from fastapi import WebSocket


logger = logging.getLogger(__name__)


JsonMessage = dict[str, Any]


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
    ) -> None:
        self.metrics = metrics or Metrics()
        self._stream_metric_label = stream_metric_label
        self._max_queue = max_queue
        self._clients: dict[WebSocket, asyncio.Queue[JsonMessage]] = {}

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def publish_message(self, message: JsonMessage) -> None:
        """Queue *message* to every connected client without blocking."""
        for queue in self._clients.values():
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self.metrics.record_telemetry_dropped_batch(self._stream_metric_label)

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
                await websocket.send_text(orjson.dumps(message).decode())
        except Exception as e:
            logger.debug(f"WebSocket send error on {self._stream_metric_label}: {e!r}")
        finally:
            await self.disconnect_client(websocket)

    def _after_disconnect(self) -> None:
        """Hook for subclasses that need lifecycle cleanup."""

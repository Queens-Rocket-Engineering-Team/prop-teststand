from __future__ import annotations
import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

import orjson
from fastapi import WebSocket

from libqretprop.runtime.metrics import Metrics


if TYPE_CHECKING:
    from libqretprop.state import SystemState


logger = logging.getLogger(__name__)

STREAM_METRIC_LABEL = "state"
STATE_STREAM_MAX_QUEUE = 64

# Sentinel placed in a client's queue on overflow; wakes a blocked drain loop to exit.
_DRAIN_SHUTDOWN: object = object()


class StateStream:
    """Broadcasts state snapshots and typed state events to /ws/state WebSocket clients.

    Each client gets a full snapshot as its first message, then ordered delta events via
    a per-client queue.  Slow clients are disconnected on queue overflow (state events are
    deltas; silently dropping one leaves the client permanently stale).  A concurrent
    receive loop runs alongside the drain loop so client disconnects are detected promptly
    on this low-rate stream rather than waiting for the next publish.
    """

    def __init__(self, state: SystemState, *, metrics: Metrics | None = None) -> None:
        self.metrics = metrics or Metrics()
        self._state = state
        self._clients: dict[WebSocket, asyncio.Queue[Any]] = {}

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def snapshot_message(self) -> dict[str, Any]:
        return {
            "type": "state.snapshot",
            "state_version": self._state.state_version,
            "state": self._state.snapshot(),
        }

    async def connect_client(self, websocket: WebSocket) -> None:
        """Accept the WebSocket and enqueue the initial snapshot at queue position 0.

        The snapshot is enqueued before the client is registered in ``_clients`` so no
        concurrent ``publish()`` call can insert an event ahead of it.
        """
        await websocket.accept()
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=STATE_STREAM_MAX_QUEUE)
        queue.put_nowait(self.snapshot_message())  # always fits; queue is empty
        self._clients[websocket] = queue
        self.metrics.set_ws_clients(STREAM_METRIC_LABEL, self.client_count)

    async def disconnect_client(self, websocket: WebSocket) -> None:
        self._clients.pop(websocket, None)
        self.metrics.set_ws_clients(STREAM_METRIC_LABEL, self.client_count)
        with contextlib.suppress(Exception):
            await websocket.close()

    async def handle_client(self, websocket: WebSocket) -> None:
        """Manage the full lifetime of one /ws/state connection."""
        await self.connect_client(websocket)
        queue = self._clients.get(websocket)
        if queue is None:
            # connect_client failed before registering (accept raised)
            return

        async def _drain() -> None:
            try:
                while True:
                    message = await queue.get()
                    if message is _DRAIN_SHUTDOWN:
                        break
                    await websocket.send_text(orjson.dumps(message).decode())
            except Exception as exc:
                logger.debug(f"State WebSocket send error, removing client: {exc!r}")

        async def _receive() -> None:
            # Actively receive to detect client-initiated disconnects promptly.
            try:
                while True:
                    await websocket.receive_text()
            except Exception:
                pass

        drain_task = asyncio.create_task(_drain())
        receive_task = asyncio.create_task(_receive())
        try:
            await asyncio.wait([drain_task, receive_task], return_when=asyncio.FIRST_COMPLETED)
        finally:
            drain_task.cancel()
            receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drain_task
            with contextlib.suppress(asyncio.CancelledError):
                await receive_task
            await self.disconnect_client(websocket)

    def publish(self, event: dict[str, object] | None) -> None:
        """Queue *event* to every connected client without blocking."""
        if event is None:
            return
        overflow: list[WebSocket] = []
        for ws, queue in list(self._clients.items()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                overflow.append(ws)
        for ws in overflow:
            self._disconnect_overflow_client(ws)

    def _disconnect_overflow_client(self, websocket: WebSocket) -> None:
        """Remove a slow client, drain its queue, and put the shutdown sentinel.

        The sentinel wakes the drain loop if it is blocked on ``queue.get()``.
        """
        queue = self._clients.pop(websocket, None)
        if queue is None:
            return
        self.metrics.set_ws_clients(STREAM_METRIC_LABEL, self.client_count)
        # Clear pending items to make room for the sentinel.
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(_DRAIN_SHUTDOWN)
        # Schedule socket close; handle_client's finally also calls disconnect_client (idempotent).
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_close_websocket(websocket))
        except RuntimeError:
            pass


async def _close_websocket(websocket: WebSocket) -> None:
    with contextlib.suppress(Exception):
        await websocket.close()

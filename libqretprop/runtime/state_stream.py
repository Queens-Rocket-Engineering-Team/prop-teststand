from __future__ import annotations
import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect


if TYPE_CHECKING:
    from collections.abc import Iterable

    from libqretprop.state import SystemState


class StateStream:
    """Broadcasts state snapshots and typed state events to WebSocket clients."""

    def __init__(self, state: SystemState) -> None:
        self._state = state
        self._clients: set[WebSocket] = set()
        self._broadcast_tasks: set[asyncio.Task[None]] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def snapshot_message(self) -> dict[str, Any]:
        return {
            "type": "state.snapshot",
            "state_version": self._state.state_version,
            "state": self._state.to_dict(),
        }

    async def connect_client(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        try:
            await websocket.send_json(self.snapshot_message())
        except Exception:
            await self.disconnect_client(websocket)
            raise

    async def disconnect_client(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)
        with contextlib.suppress(Exception):
            await websocket.close()

    async def handle_client(self, websocket: WebSocket) -> None:
        await self.connect_client(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await self.disconnect_client(websocket)

    def publish(self, event: dict[str, object] | None) -> None:
        if event is None:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        task = loop.create_task(self.broadcast(event))
        self._broadcast_tasks.add(task)
        task.add_done_callback(self._broadcast_tasks.discard)

    async def broadcast(self, event: dict[str, object]) -> None:
        stale_clients: list[WebSocket] = []

        for websocket in self._clients_snapshot():
            try:
                await websocket.send_json(event)
            except Exception:
                stale_clients.append(websocket)

        for websocket in stale_clients:
            await self.disconnect_client(websocket)

    def _clients_snapshot(self) -> Iterable[WebSocket]:
        return tuple(self._clients)

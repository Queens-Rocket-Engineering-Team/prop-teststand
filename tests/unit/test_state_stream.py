from __future__ import annotations
import asyncio
from typing import Any, cast

from fastapi import WebSocket

from libqretprop.runtime.state_stream import StateStream
from libqretprop.state.system_state import SystemState
from tests.unit.test_system_state import _make_device


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


def _as_websocket(websocket: FakeWebSocket) -> WebSocket:
    return cast(WebSocket, websocket)


def test_connect_client_sends_initial_snapshot() -> None:
    async def run() -> None:
        state = SystemState()
        state.register_device(_make_device())
        stream = StateStream(state)
        websocket = FakeWebSocket()

        await stream.connect_client(_as_websocket(websocket))

        assert websocket.accepted is True
        assert websocket.sent[0]["type"] == "state.snapshot"
        assert websocket.sent[0]["state_version"] == 1
        assert websocket.sent[0]["state"]["state_version"] == 1
        assert websocket.sent[0]["state"]["devices"][0]["name"] == "TEST-DEVICE"

    asyncio.run(run())


def test_broadcast_sends_typed_state_event() -> None:
    async def run() -> None:
        state = SystemState()
        device = _make_device()
        state.register_device(device)
        stream = StateStream(state)
        websocket = FakeWebSocket()
        await stream.connect_client(_as_websocket(websocket))

        event = state.update_control_state(device, 0, "OPEN", now=42.0)
        assert event is not None
        await stream.broadcast(event)

        assert websocket.sent[-1]["type"] == "control.updated"
        assert websocket.sent[-1]["state_version"] == 2
        assert websocket.sent[-1]["control_name"] == "VALVE1"
        assert websocket.sent[-1]["reported_state"] == "OPEN"

    asyncio.run(run())


def test_broadcast_removes_broken_clients() -> None:
    async def run() -> None:
        state = SystemState()
        stream = StateStream(state)
        good = FakeWebSocket()
        broken = FakeWebSocket(fail_after=1)
        await stream.connect_client(_as_websocket(good))
        await stream.connect_client(_as_websocket(broken))

        await stream.broadcast({"type": "device.disconnected", "state_version": 1})

        assert stream.client_count == 1
        assert good.sent[-1]["type"] == "device.disconnected"
        assert broken.closed is True

    asyncio.run(run())


def test_connect_client_removes_client_when_initial_snapshot_send_fails() -> None:
    async def run() -> None:
        state = SystemState()
        stream = StateStream(state)
        websocket = FakeWebSocket(fail_after=0)

        try:
            await stream.connect_client(_as_websocket(websocket))
        except RuntimeError:
            pass

        assert stream.client_count == 0
        assert websocket.closed is True

    asyncio.run(run())

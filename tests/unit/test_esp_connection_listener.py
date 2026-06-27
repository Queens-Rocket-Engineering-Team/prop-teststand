from __future__ import annotations
import asyncio
import contextlib
import socket
from typing import cast

from libqretprop.runtime.esp_connection_runtime import ESPConnectionListener, ESPConnectionRuntime


class FakeRuntime:
    def __init__(self) -> None:
        self.accepted: list[str] = []

    async def accept_connection(self, client_socket: socket.socket, address: str) -> None:
        self.accepted.append(address)
        client_socket.close()


def _free_tcp_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


def test_listener_delegates_accepted_connections_to_runtime() -> None:
    async def run() -> None:
        port = _free_tcp_port()
        runtime = FakeRuntime()
        listener = ESPConnectionListener(cast(ESPConnectionRuntime, runtime), port=port)
        task = asyncio.create_task(listener.run())

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            for _ in range(200):  # up to ~4s; tolerates the socket not yet being bound
                with contextlib.suppress(OSError):
                    client.connect(("127.0.0.1", port))
                    break
                await asyncio.sleep(0.02)

            for _ in range(200):
                if runtime.accepted:
                    break
                await asyncio.sleep(0.02)

            assert runtime.accepted == ["127.0.0.1"]
        finally:
            client.close()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(run())

from __future__ import annotations
import asyncio
import contextlib
import socket
from typing import cast

from prop_teststand.runtime.esp_connection_runtime import ESPConnectionRuntime


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


class StallingRuntime:
    """First connection's handshake stalls until released; later ones complete."""

    def __init__(self) -> None:
        self.accepted: list[str] = []
        self.release = asyncio.Event()
        self._first = True

    async def accept_connection(self, client_socket: socket.socket, address: str) -> None:
        self.accepted.append(address)
        stall = self._first
        self._first = False
        if stall:
            await self.release.wait()
        client_socket.close()


async def _connect(port: int) -> socket.socket:
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    for _ in range(200):  # up to ~4s; tolerates the socket not yet being bound
        with contextlib.suppress(OSError):
            client.connect(("127.0.0.1", port))
            return client
        await asyncio.sleep(0.02)
    client.close()
    raise AssertionError("could not connect to listener")


def test_stalled_handshake_does_not_block_other_connections() -> None:
    async def run() -> None:
        port = _free_tcp_port()
        runtime = StallingRuntime()
        task = asyncio.create_task(ESPConnectionRuntime.run_tcp_listener(cast(ESPConnectionRuntime, runtime), port=port))

        clients: list[socket.socket] = []
        try:
            clients.append(await _connect(port))  # handshake stalls, never completes
            clients.append(await _connect(port))

            for _ in range(200):
                if len(runtime.accepted) >= 2:
                    break
                await asyncio.sleep(0.02)

            # The second handshake started while the first was still stalled.
            assert runtime.accepted == ["127.0.0.1", "127.0.0.1"]
        finally:
            runtime.release.set()
            for client in clients:
                client.close()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(run())


def test_listener_delegates_accepted_connections_to_runtime() -> None:
    async def run() -> None:
        port = _free_tcp_port()
        runtime = FakeRuntime()
        task = asyncio.create_task(ESPConnectionRuntime.run_tcp_listener(cast(ESPConnectionRuntime, runtime), port=port))

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

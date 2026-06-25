from __future__ import annotations
import asyncio
import contextlib
import socket
from typing import cast

import pytest

from libqretprop.runtime.telemetry_ingest import (
    TelemetryBatch,
    TelemetryIngest,
    TelemetryUDPListener,
)


@pytest.fixture(autouse=True)
def _silence_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    # ml.slog/elog raise unless the Redis-backed logger is initialized, which it is not
    # under pytest. The listener logs on startup and on errors, so stub them out.
    monkeypatch.setattr("libqretprop.runtime.telemetry_ingest.ml.slog", lambda *a, **k: None)
    monkeypatch.setattr("libqretprop.runtime.telemetry_ingest.ml.elog", lambda *a, **k: None)


class FakeIngest:
    def __init__(self, batch: TelemetryBatch | None) -> None:
        self._batch = batch
        self.seen: list[tuple[bytes, str]] = []

    def handle_datagram(self, data: bytes, address: str) -> TelemetryBatch | None:
        self.seen.append((data, address))
        return self._batch


class FakePublisher:
    def __init__(self) -> None:
        self.batches: list[TelemetryBatch] = []

    def publish_batch(self, batch: TelemetryBatch) -> None:
        self.batches.append(batch)


def _free_udp_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


def _make_batch() -> TelemetryBatch:
    return TelemetryBatch(
        device_name="MockDevice",
        device_address="127.0.0.1",
        connection_key="esp-1",
        timestamp_s=1.0,
        readings=(),
    )


async def _drive(listener: TelemetryUDPListener, port: int, stop) -> None:
    """Run the listener while a sender pushes datagrams until ``stop()`` is true (or timeout)."""
    task = asyncio.create_task(listener.run())
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _ in range(200):  # up to ~4s; tolerates the socket not yet being bound
            sender.sendto(b"datagram", ("127.0.0.1", port))
            await asyncio.sleep(0.02)
            if stop():
                break
    finally:
        sender.close()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_listener_publishes_decoded_batches() -> None:
    async def run() -> None:
        port = _free_udp_port()
        batch = _make_batch()
        ingest = FakeIngest(batch)
        publisher = FakePublisher()
        listener = TelemetryUDPListener(cast(TelemetryIngest, ingest), publisher, port=port)

        await _drive(listener, port, stop=lambda: bool(publisher.batches))

        assert publisher.batches, "listener never published a decoded batch"
        assert publisher.batches[0] is batch
        assert ingest.seen[0][1] == "127.0.0.1"

    asyncio.run(run())


def test_listener_skips_publish_when_ingest_returns_none() -> None:
    async def run() -> None:
        port = _free_udp_port()
        ingest = FakeIngest(None)
        publisher = FakePublisher()
        listener = TelemetryUDPListener(cast(TelemetryIngest, ingest), publisher, port=port)

        # Drive until at least one datagram is received, then confirm nothing was published.
        await _drive(listener, port, stop=lambda: bool(ingest.seen))

        assert ingest.seen, "listener never received a datagram"
        assert publisher.batches == []

    asyncio.run(run())

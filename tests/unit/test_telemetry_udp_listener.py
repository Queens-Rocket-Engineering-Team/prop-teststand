from __future__ import annotations
import asyncio
import contextlib
import socket

from libqretprop.runtime.telemetry_ingest import (
    TelemetryBatch,
    TelemetryRuntime,
)


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
        timestamp_source="device_synced",
        timestamp_synced=True,
    )


async def _drive(listener: TelemetryRuntime, port: int, stop) -> None:
    """Run the listener while a sender pushes datagrams until ``stop()`` is true (or timeout)."""
    task = asyncio.create_task(listener.run_udp_listener(port=port))
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
        listener = TelemetryRuntime(lambda _address: None, publisher)
        listener.handle_datagram = ingest.handle_datagram  # type: ignore[method-assign]

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
        listener = TelemetryRuntime(lambda _address: None, publisher)
        listener.handle_datagram = ingest.handle_datagram  # type: ignore[method-assign]

        # Drive until at least one datagram is received, then confirm nothing was published.
        await _drive(listener, port, stop=lambda: bool(ingest.seen))

        assert ingest.seen, "listener never received a datagram"
        assert publisher.batches == []

    asyncio.run(run())

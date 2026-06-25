from __future__ import annotations
import asyncio

import pytest

from libqretprop.runtime.discovery import DiscoveryService


@pytest.fixture(autouse=True)
def _silence_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    # ml.dlog/slog raise unless the Redis-backed logger is initialized, which it is not
    # under pytest. discover()/_create_socket log on send and on socket creation.
    monkeypatch.setattr("libqretprop.runtime.discovery.ml.dlog", lambda *a, **k: None)
    monkeypatch.setattr("libqretprop.runtime.discovery.ml.slog", lambda *a, **k: None)


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data: bytes, address: tuple[str, int]) -> None:
        self.sent.append((data, address))

    def close(self) -> None:
        self.closed = True


class _StopLoop(Exception):
    pass


def test_default_config_is_periodic_discovery() -> None:
    service = DiscoveryService()

    assert service.periodic_enabled is True
    assert service.periodic_interval_s == 30.0

    service.periodic_enabled = False
    service.periodic_interval_s = 5.0
    assert service.periodic_enabled is False
    assert service.periodic_interval_s == 5.0


def test_discover_lazily_creates_socket_once_and_sends_request(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DiscoveryService(multicast_address="239.255.255.250", multicast_port=1900)
    fake = FakeSocket()
    created = 0

    def _create_socket() -> FakeSocket:
        nonlocal created
        created += 1
        return fake

    monkeypatch.setattr(service, "_create_socket", _create_socket)

    service.discover()
    service.discover()

    assert created == 1  # socket created once and reused
    assert len(fake.sent) == 2
    payload, address = fake.sent[0]
    assert address == ("239.255.255.250", 1900)
    assert b"M-SEARCH" in payload
    assert b"ST: urn:qretprop:espdevice:1" in payload


def test_run_issues_discovery_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        service = DiscoveryService(periodic_enabled=True, periodic_interval_s=7.0)
        discover_calls = 0
        sleeps: list[float] = []

        def _discover() -> None:
            nonlocal discover_calls
            discover_calls += 1

        async def _fake_sleep(delay: float) -> None:
            sleeps.append(delay)
            raise _StopLoop  # break out after the first loop iteration

        monkeypatch.setattr(service, "discover", _discover)
        monkeypatch.setattr("libqretprop.runtime.discovery.asyncio.sleep", _fake_sleep)

        with pytest.raises(_StopLoop):
            await service.run()

        assert discover_calls == 1
        assert sleeps == [7.0]

    asyncio.run(run())


def test_run_skips_discovery_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        service = DiscoveryService(periodic_enabled=False)
        discover_calls = 0
        sleeps: list[float] = []

        def _discover() -> None:
            nonlocal discover_calls
            discover_calls += 1

        async def _fake_sleep(delay: float) -> None:
            sleeps.append(delay)
            raise _StopLoop

        monkeypatch.setattr(service, "discover", _discover)
        monkeypatch.setattr("libqretprop.runtime.discovery.asyncio.sleep", _fake_sleep)

        with pytest.raises(_StopLoop):
            await service.run()

        assert discover_calls == 0
        assert sleeps == [0.5]  # short poll interval while disabled

    asyncio.run(run())

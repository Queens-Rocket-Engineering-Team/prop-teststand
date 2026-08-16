from __future__ import annotations
import asyncio
from typing import Any

import pytest

from prop_teststand.runtime.gui_watchdog import GUI_WATCHDOG_TIMEOUT_S, GUIWatchdog
from prop_teststand.runtime.metrics import Metrics


class Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeStateStream:
    def __init__(self, client_count: int = 0) -> None:
        self.client_count = client_count


class FakeSession:
    def __init__(self, name: str, connection_key: str) -> None:
        self.name = name
        self.connection_key = connection_key


class FakeESPRuntime:
    """Stands in for ESPConnectionRuntime, recording every ESTOP fan-out."""

    def __init__(self, *sessions: FakeSession, fail: set[str] | None = None) -> None:
        self.sessions = list(sessions)
        self.fail = fail or set()
        self.estopped: list[str] = []

    def get_registered_devices(self) -> dict[str, Any]:
        return {f"10.0.0.{index}": session for index, session in enumerate(self.sessions)}

    async def emergency_stop(self, session: Any) -> bool:
        self.estopped.append(session.name)
        return session.name not in self.fail


class _StopLoop(Exception):
    pass


def _watchdog(
    stream: FakeStateStream,
    esp: FakeESPRuntime,
    clock: Clock,
    *,
    metrics: Metrics | None = None,
) -> GUIWatchdog:
    return GUIWatchdog(state_stream=stream, esp_runtime=esp, metrics=metrics, time_fn=clock)


def test_defaults_are_ten_minutes_and_armed_at_construction() -> None:
    clock = Clock()
    watchdog = _watchdog(FakeStateStream(), FakeESPRuntime(), clock)

    assert GUI_WATCHDOG_TIMEOUT_S == 600.0
    assert watchdog.timeout_s == 600.0
    assert watchdog.tripped is False


def test_connected_gui_never_trips() -> None:
    clock = Clock()
    esp = FakeESPRuntime(FakeSession("valve-node", "esp-1"))
    watchdog = _watchdog(FakeStateStream(client_count=1), esp, clock)

    async def run() -> None:
        for _ in range(5):
            clock.advance(GUI_WATCHDOG_TIMEOUT_S)
            await watchdog.check()

    asyncio.run(run())

    assert esp.estopped == []
    assert watchdog.tripped is False


def test_trips_and_estops_every_registered_node_after_timeout() -> None:
    clock = Clock()
    esp = FakeESPRuntime(FakeSession("valve-node", "esp-1"), FakeSession("igniter-node", "esp-2"))
    watchdog = _watchdog(FakeStateStream(), esp, clock)

    async def run() -> None:
        clock.advance(GUI_WATCHDOG_TIMEOUT_S - 1.0)
        await watchdog.check()
        assert esp.estopped == []

        clock.advance(2.0)
        await watchdog.check()

    asyncio.run(run())

    assert esp.estopped == ["valve-node", "igniter-node"]
    assert watchdog.tripped is True


def test_armed_at_boot_trips_without_a_gui_ever_connecting() -> None:
    clock = Clock()
    esp = FakeESPRuntime(FakeSession("valve-node", "esp-1"))
    watchdog = _watchdog(FakeStateStream(), esp, clock)

    async def run() -> None:
        clock.advance(GUI_WATCHDOG_TIMEOUT_S + 1.0)
        await watchdog.check()

    asyncio.run(run())

    assert esp.estopped == ["valve-node"]


def test_latches_so_each_node_is_estopped_once_per_trip() -> None:
    clock = Clock()
    esp = FakeESPRuntime(FakeSession("valve-node", "esp-1"))
    watchdog = _watchdog(FakeStateStream(), esp, clock)

    async def run() -> None:
        clock.advance(GUI_WATCHDOG_TIMEOUT_S + 1.0)
        await watchdog.check()
        for _ in range(5):
            clock.advance(GUI_WATCHDOG_TIMEOUT_S)
            await watchdog.check()

    asyncio.run(run())

    assert esp.estopped == ["valve-node"]


def test_reconnecting_gui_rearms_the_watchdog() -> None:
    clock = Clock()
    esp = FakeESPRuntime(FakeSession("valve-node", "esp-1"))
    stream = FakeStateStream()
    watchdog = _watchdog(stream, esp, clock)

    async def run() -> None:
        clock.advance(GUI_WATCHDOG_TIMEOUT_S + 1.0)
        await watchdog.check()
        assert esp.estopped == ["valve-node"]

        # GUI comes back: latch clears and the timer restarts.
        stream.client_count = 1
        await watchdog.check()
        assert watchdog.tripped is False

        stream.client_count = 0
        clock.advance(GUI_WATCHDOG_TIMEOUT_S - 1.0)
        await watchdog.check()
        assert esp.estopped == ["valve-node"]

        clock.advance(2.0)
        await watchdog.check()

    asyncio.run(run())

    assert esp.estopped == ["valve-node", "valve-node"]
    assert watchdog.tripped is True


def test_trips_with_no_registered_nodes_and_safes_one_that_connects_later() -> None:
    clock = Clock()
    esp = FakeESPRuntime()
    watchdog = _watchdog(FakeStateStream(), esp, clock)

    async def run() -> None:
        clock.advance(GUI_WATCHDOG_TIMEOUT_S + 1.0)
        await watchdog.check()
        assert watchdog.tripped is True
        assert esp.estopped == []

        # A node registers while the GUI is still absent.
        esp.sessions.append(FakeSession("valve-node", "esp-1"))
        await watchdog.check()
        assert esp.estopped == ["valve-node"]

        # ...and is not re-sent on later ticks.
        await watchdog.check()

    asyncio.run(run())

    assert esp.estopped == ["valve-node"]


def test_node_reconnecting_while_latched_gets_its_own_estop() -> None:
    clock = Clock()
    session = FakeSession("valve-node", "esp-1")
    esp = FakeESPRuntime(session)
    watchdog = _watchdog(FakeStateStream(), esp, clock)

    async def run() -> None:
        clock.advance(GUI_WATCHDOG_TIMEOUT_S + 1.0)
        await watchdog.check()
        assert esp.estopped == ["valve-node"]

        # Same device back on a fresh TCP session: new connection_key, unknown control states.
        esp.sessions = [FakeSession("valve-node", "esp-2")]
        await watchdog.check()
        assert esp.estopped == ["valve-node", "valve-node"]

        await watchdog.check()

    asyncio.run(run())

    assert esp.estopped == ["valve-node", "valve-node"]


def test_failed_send_does_not_block_other_nodes_or_retry_tightly() -> None:
    clock = Clock()
    esp = FakeESPRuntime(
        FakeSession("valve-node", "esp-1"),
        FakeSession("igniter-node", "esp-2"),
        fail={"valve-node"},
    )
    metrics = Metrics()
    watchdog = _watchdog(FakeStateStream(), esp, clock, metrics=metrics)

    async def run() -> None:
        clock.advance(GUI_WATCHDOG_TIMEOUT_S + 1.0)
        await watchdog.check()
        # The failed node keeps its key recorded, so it is not retried on the next tick.
        await watchdog.check()

    asyncio.run(run())

    assert esp.estopped == ["valve-node", "igniter-node"]
    snapshot = metrics.to_dict()
    assert snapshot["gui_watchdog"] == {"trips_total": 1}


def test_trip_is_recorded_in_metrics() -> None:
    clock = Clock()
    esp = FakeESPRuntime(FakeSession("valve-node", "esp-1"))
    metrics = Metrics()
    watchdog = _watchdog(FakeStateStream(), esp, clock, metrics=metrics)

    async def run() -> None:
        clock.advance(GUI_WATCHDOG_TIMEOUT_S + 1.0)
        await watchdog.check()

    asyncio.run(run())

    snapshot = metrics.to_dict()
    assert snapshot["gui_watchdog"] == {"trips_total": 1}
    events = [event for event in snapshot["recent_events"] if event["kind"] == "gui.watchdog_tripped"]  # type: ignore[union-attr]
    assert len(events) == 1
    assert events[0]["targeted"] == 1
    assert events[0]["sent"] == 1


def test_run_loop_sleeps_the_poll_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    watchdog = GUIWatchdog(
        state_stream=FakeStateStream(client_count=1),
        esp_runtime=FakeESPRuntime(),
        time_fn=clock,
        poll_interval_s=7.0,
    )
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        raise _StopLoop

    monkeypatch.setattr("prop_teststand.runtime.gui_watchdog.asyncio.sleep", _fake_sleep)

    async def run() -> None:
        with pytest.raises(_StopLoop):
            await watchdog.run()

    asyncio.run(run())

    assert sleeps == [7.0]


def test_run_loop_survives_a_failing_check(monkeypatch: pytest.MonkeyPatch) -> None:
    watchdog = GUIWatchdog(state_stream=FakeStateStream(), esp_runtime=FakeESPRuntime(), poll_interval_s=1.0)
    sleeps: list[float] = []

    async def _boom() -> None:
        raise RuntimeError("registry exploded")

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 2:
            raise _StopLoop

    monkeypatch.setattr(watchdog, "check", _boom)
    monkeypatch.setattr("prop_teststand.runtime.gui_watchdog.asyncio.sleep", _fake_sleep)

    async def run() -> None:
        with pytest.raises(_StopLoop):
            await watchdog.run()

    asyncio.run(run())

    assert sleeps == [1.0, 1.0]

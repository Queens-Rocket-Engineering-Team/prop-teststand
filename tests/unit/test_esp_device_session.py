from __future__ import annotations
import asyncio
import socket
import time
from typing import Any

from libqretprop.qlcp.enums import PacketType
from libqretprop.runtime.command_tracker import CommandTracker
from libqretprop.runtime.esp_connection_runtime import ESPConnectionRuntime
from libqretprop.runtime.esp_device_session import ESPDeviceSession
from libqretprop.state.system_state import SystemState


class FakeStateStream:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def publish(self, event: dict[str, object] | None) -> None:
        if event is not None:
            self.events.append(event)


def _make_config() -> dict[str, Any]:
    return {
        "device_name": "TEST-DEVICE",
        "device_type": "Sensor Monitor",
        "sensor_info": {},
        "controls": {},
    }


def _make_session() -> tuple[ESPDeviceSession, socket.socket]:
    session_sock, peer_sock = socket.socketpair()
    session_sock.setblocking(False)
    peer_sock.setblocking(False)
    session = ESPDeviceSession(
        session_sock,
        "10.0.0.2",
        _make_config(),
        connection_key="conn-a",
    )
    return session, peer_sock


def _make_runtime() -> tuple[ESPConnectionRuntime, CommandTracker, FakeStateStream]:
    tracker = CommandTracker()
    state_stream = FakeStateStream()
    runtime = ESPConnectionRuntime(
        command_tracker=tracker,
        system_state=SystemState(command_tracker=tracker),
        state_stream=state_stream,
    )
    return runtime, tracker, state_stream


def test_resync_state_transitions() -> None:
    session, peer_sock = _make_session()
    try:
        assert session.needs_resync() is False

        session.last_sync_time = time.monotonic() - session.RESYNC_INTERVAL_S - 1.0
        assert session.needs_resync() is True

        session.mark_resync_sent()
        assert session.needs_resync() is False

        session.last_sync_time = time.monotonic()
        session.mark_synced()
        assert session.needs_resync() is False
    finally:
        session.close()
        peer_sock.close()


def test_heartbeat_ack_resets_missed_heartbeat_state() -> None:
    session, peer_sock = _make_session()
    _runtime, tracker, _stream = _make_runtime()
    try:
        command = tracker.mark_sent(
            connection_key=session.connection_key,
            device_name=session.name,
            device_address=session.address,
            packet_type=PacketType.HEARTBEAT,
            packet_sequence=1,
            now=10.0,
        )

        assert session.register_missed_heartbeat() is False
        session.mark_unresponsive()
        session.record_heartbeat_ack(command)

        assert session.missed_heartbeat_count == 0
        assert session.is_responsive is True
    finally:
        session.close()
        peer_sock.close()


def test_runtime_marks_session_unresponsive_at_heartbeat_miss_limit() -> None:
    session, peer_sock = _make_session()
    runtime, tracker, _stream = _make_runtime()
    runtime.devices[session.address] = session
    runtime.system_state.register_device(session)
    try:
        command = tracker.mark_sent(
            connection_key=session.connection_key,
            device_name=session.name,
            device_address=session.address,
            packet_type=PacketType.HEARTBEAT,
            packet_sequence=1,
            now=10.0,
        )

        for _ in range(session.HEARTBEAT_ACK_MISS_LIMIT - 1):
            assert runtime._handle_missed_heartbeat(session, command) is False
            assert session.is_responsive is True

        assert runtime._handle_missed_heartbeat(session, command) is True
        assert session.is_responsive is False
        assert session.address not in runtime.devices
    finally:
        session.close()
        peer_sock.close()


def test_heartbeat_loop_exits_when_send_heartbeat_fails() -> None:
    async def run() -> None:
        session, peer_sock = _make_session()

        class RuntimeStub:
            expire_calls = 0
            send_calls = 0

            def expire_command_timeouts(self, session: ESPDeviceSession) -> bool:
                self.expire_calls += 1
                return False

            async def send_heartbeat(self, session: ESPDeviceSession) -> bool:
                self.send_calls += 1
                return False

        runtime = RuntimeStub()
        try:
            await session.heartbeat(runtime)  # type: ignore[arg-type]

            assert runtime.expire_calls == 1
            assert runtime.send_calls == 1
        finally:
            session.close()
            peer_sock.close()

    asyncio.run(run())

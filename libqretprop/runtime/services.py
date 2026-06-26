"""Composition root and lifecycle container for the server's runtime graph."""

from __future__ import annotations
import asyncio
from dataclasses import dataclass, field

import libqretprop.redis_logging as ml
from libqretprop.legacy_gui_logging import LegacyESPLogSink
from libqretprop.runtime.command_tracker import CommandTracker
from libqretprop.runtime.discovery import DiscoveryService
from libqretprop.runtime.esp_connection_runtime import ESPConnectionListener, ESPConnectionRuntime
from libqretprop.runtime.state_stream import StateStream
from libqretprop.runtime.telemetry_display_stream import TelemetryDisplayStream
from libqretprop.runtime.telemetry_ingest import TelemetryIngest, TelemetryUDPListener
from libqretprop.runtime.telemetry_stream import TelemetryStreamRuntime
from libqretprop.state import SystemState


@dataclass
class RuntimeServices:
    """Wired runtime object graph for the server process.

    Owns the ESP/telemetry/state daemon lifecycle.  Camera, Kasa, and audio
    tasks are owned by ``server.main()`` and are not part of this container.
    """

    command_tracker: CommandTracker
    system_state: SystemState
    state_stream: StateStream
    discovery_service: DiscoveryService
    telemetry_stream: TelemetryStreamRuntime
    telemetry_display_stream: TelemetryDisplayStream
    esp_runtime: ESPConnectionRuntime
    esp_connection_listener: ESPConnectionListener
    telemetry_ingest: TelemetryIngest
    telemetry_udp_listener: TelemetryUDPListener
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False, repr=False)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Launch ESP/telemetry/state daemon tasks onto *loop*.

        Must be called after the event loop is running (i.e. from inside an
        async context or after ``asyncio.get_event_loop()`` is valid).  Only
        has an effect when the server is not started in ``noDiscovery`` mode;
        that gate lives in ``server.main()``.
        """
        self._tasks["tcpListener"] = loop.create_task(self.esp_connection_listener.run())
        self._tasks["udpListener"] = loop.create_task(self.telemetry_udp_listener.run())
        self._tasks["telemetryDisplayFlush"] = loop.create_task(self.telemetry_display_stream.run())
        self._tasks["autoDiscovery"] = loop.create_task(self.discovery_service.run())

    async def stop(self) -> None:
        """Cancel and await all runtime daemon tasks, then close device connections."""
        for name, task in self._tasks.items():
            if not task.done():
                task.cancel()
                ml.slog(f"Cancelled {name} daemon task.")
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self.esp_runtime.close_all()


def build_runtime() -> RuntimeServices:
    """Construct and wire the full runtime object graph.

    This is the single composition root.  Call once at server startup and pass
    the returned :class:`RuntimeServices` container to all consumers — do not
    import it as a module-level global.
    """
    command_tracker = CommandTracker()
    system_state = SystemState(command_tracker=command_tracker)
    state_stream = StateStream(system_state)
    discovery_service = DiscoveryService()
    telemetry_stream = TelemetryStreamRuntime()
    telemetry_display_stream = TelemetryDisplayStream()
    esp_runtime = ESPConnectionRuntime(
        command_tracker=command_tracker,
        system_state=system_state,
        state_stream=state_stream,
        legacy_log_sink=LegacyESPLogSink(),
    )
    esp_connection_listener = ESPConnectionListener(esp_runtime)
    telemetry_ingest = TelemetryIngest(esp_runtime)
    telemetry_udp_listener = TelemetryUDPListener(
        telemetry_ingest,
        telemetry_stream,
        telemetry_display_stream,
    )
    return RuntimeServices(
        command_tracker=command_tracker,
        system_state=system_state,
        state_stream=state_stream,
        discovery_service=discovery_service,
        telemetry_stream=telemetry_stream,
        telemetry_display_stream=telemetry_display_stream,
        esp_runtime=esp_runtime,
        esp_connection_listener=esp_connection_listener,
        telemetry_ingest=telemetry_ingest,
        telemetry_udp_listener=telemetry_udp_listener,
    )

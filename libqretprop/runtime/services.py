"""Composition root and lifecycle container for the server's runtime graph."""

from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field

from libqretprop.integrations.mediamtx import MediaMTXClient
from libqretprop.runtime.audio_runtime import AudioRuntime
from libqretprop.runtime.camera_runtime import CameraRuntime
from libqretprop.runtime.command_tracker import CommandTracker
from libqretprop.runtime.discovery import DiscoveryService
from libqretprop.runtime.esp_connection_runtime import ESPConnectionListener, ESPConnectionRuntime
from libqretprop.runtime.kasa_runtime import KasaRuntime
from libqretprop.runtime.log_stream import LogStream
from libqretprop.runtime.metrics import Metrics
from libqretprop.runtime.state_stream import StateStream
from libqretprop.runtime.telemetry_display_stream import TelemetryDisplayStream
from libqretprop.runtime.telemetry_ingest import TelemetryIngest, TelemetryUDPListener
from libqretprop.runtime.telemetry_stream import TelemetryStreamRuntime
from libqretprop.state import SystemState


logger = logging.getLogger(__name__)


@dataclass
class RuntimeServices:
    """Wired runtime object graph for the server process.

    Owns runtime daemon lifecycle and startup actions.
    """

    command_tracker: CommandTracker
    metrics: Metrics
    system_state: SystemState
    state_stream: StateStream
    log_stream: LogStream
    discovery_service: DiscoveryService
    telemetry_stream: TelemetryStreamRuntime
    telemetry_display_stream: TelemetryDisplayStream
    esp_runtime: ESPConnectionRuntime
    esp_connection_listener: ESPConnectionListener
    telemetry_ingest: TelemetryIngest
    telemetry_udp_listener: TelemetryUDPListener
    audio_runtime: AudioRuntime
    camera_runtime: CameraRuntime
    kasa_runtime: KasaRuntime
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False, repr=False)

    def start(self, loop: asyncio.AbstractEventLoop, *, include_device_daemons: bool = True) -> None:
        """Launch runtime daemon tasks onto *loop*.

        Must be called after the event loop is running (i.e. from inside an
        async context or after ``asyncio.get_event_loop()`` is valid).
        """
        if include_device_daemons:
            self._tasks["tcp_listener"] = loop.create_task(self.esp_connection_listener.run())
            self._tasks["udp_listener"] = loop.create_task(self.telemetry_udp_listener.run())
            self._tasks["telemetry_display_flush"] = loop.create_task(self.telemetry_display_stream.run())
            self._tasks["auto_discovery"] = loop.create_task(self.discovery_service.run())
        # Camera/Kasa startup is independent of ESP discovery.
        self._tasks["camera_connector"] = loop.create_task(self.camera_runtime.connect_all_cameras())
        self._tasks["kasa_discoverer"] = loop.create_task(self.kasa_runtime.discover_kasa_devices())
        self._tasks["log_stream"] = loop.create_task(self.log_stream.run())
        logger.info("Started camera_connector daemon task.")
        logger.info("Started kasa_discoverer daemon task.")

    async def stop(self) -> None:
        """Cancel and await all runtime daemon tasks, then close device connections."""
        log_task = self._tasks.get("log_stream")
        runtime_tasks = {
            name: task
            for name, task in self._tasks.items()
            if name != "log_stream"
        }

        for name, task in runtime_tasks.items():
            if not task.done():
                task.cancel()
                logger.info(f"Cancelled {name} daemon task.")
        await asyncio.gather(*runtime_tasks.values(), return_exceptions=True)
        self.esp_runtime.close_all()
        self.audio_runtime.close()

        await asyncio.sleep(0)
        if log_task is not None and not log_task.done():
            log_task.cancel()
        if log_task is not None:
            await asyncio.gather(log_task, return_exceptions=True)


def build_runtime() -> RuntimeServices:
    """Construct and wire the full runtime object graph.

    This is the single composition root.  Call once at server startup and pass
    the returned :class:`RuntimeServices` container to all consumers — do not
    import it as a module-level global.
    """
    metrics = Metrics()
    command_tracker = CommandTracker(metrics=metrics)
    system_state = SystemState(command_tracker=command_tracker)
    state_stream = StateStream(system_state, metrics=metrics)
    log_stream = LogStream(metrics=metrics)
    discovery_service = DiscoveryService()
    telemetry_stream = TelemetryStreamRuntime(metrics=metrics)
    telemetry_display_stream = TelemetryDisplayStream(metrics=metrics)
    esp_runtime = ESPConnectionRuntime(
        command_tracker=command_tracker,
        system_state=system_state,
        state_stream=state_stream,
        metrics=metrics,
    )
    esp_connection_listener = ESPConnectionListener(esp_runtime)
    telemetry_ingest = TelemetryIngest(esp_runtime, metrics=metrics)
    telemetry_udp_listener = TelemetryUDPListener(
        telemetry_ingest,
        telemetry_stream,
        telemetry_display_stream,
    )
    audio_runtime = AudioRuntime()
    mediamtx = MediaMTXClient()
    camera_runtime = CameraRuntime(mediamtx=mediamtx)
    kasa_runtime = KasaRuntime()
    return RuntimeServices(
        command_tracker=command_tracker,
        metrics=metrics,
        system_state=system_state,
        state_stream=state_stream,
        log_stream=log_stream,
        discovery_service=discovery_service,
        telemetry_stream=telemetry_stream,
        telemetry_display_stream=telemetry_display_stream,
        esp_runtime=esp_runtime,
        esp_connection_listener=esp_connection_listener,
        telemetry_ingest=telemetry_ingest,
        telemetry_udp_listener=telemetry_udp_listener,
        audio_runtime=audio_runtime,
        camera_runtime=camera_runtime,
        kasa_runtime=kasa_runtime,
    )

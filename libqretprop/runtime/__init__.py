"""Runtime helpers for active server behavior."""

from libqretprop.runtime.command_tracker import (
    CommandKey,
    CommandLifecycle,
    CommandRecord,
    CommandTracker,
    command_tracker,
)
from libqretprop.runtime.discovery import DiscoveryService, discovery_service
from libqretprop.runtime.state_stream import StateStream, state_stream

# NOTE: do not re-export the ``telemetry_ingest`` singleton here — its name collides
# with the ``telemetry_ingest`` submodule and would shadow the module on the package
# namespace (breaking dotted-path monkeypatching). Import it from the module directly.
from libqretprop.runtime.telemetry_ingest import (
    TelemetryIngest,
    TelemetryUDPListener,
    telemetry_udp_listener,
)
from libqretprop.runtime.telemetry_stream import TelemetryStreamRuntime, telemetry_stream


__all__ = [
    "CommandKey",
    "CommandLifecycle",
    "CommandRecord",
    "CommandTracker",
    "DiscoveryService",
    "StateStream",
    "TelemetryIngest",
    "TelemetryStreamRuntime",
    "TelemetryUDPListener",
    "command_tracker",
    "discovery_service",
    "state_stream",
    "telemetry_stream",
    "telemetry_udp_listener",
]

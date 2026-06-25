import libqretprop.redis_logging as ml
from libqretprop.runtime.discovery import discovery_service
from libqretprop.runtime.esp_connection_runtime import esp_connection_listener, esp_runtime
from libqretprop.runtime.esp_device_session import ESPDeviceSession
from libqretprop.runtime.telemetry_ingest import telemetry_udp_listener


class _LegacyESPLogSink:
    """Temporary GUI compatibility sink for legacy text-log device events."""

    def device_connected(self, session: ESPDeviceSession) -> None:
        ml.log(f"{session.name} CONNECTED")

    def device_disconnected(self, session: ESPDeviceSession) -> None:
        ml.log(f"{session.name} DISCONNECTED")

    def control_status(self, session: ESPDeviceSession, control_name: str, state: str) -> None:
        ml.log(f"{session.name} STATUS {control_name} {state}")


esp_runtime.legacy_log_sink = _LegacyESPLogSink()


# ---------------------- #
# Active Searching Tools #
# ---------------------- #


async def autoDiscoveryLoop() -> None:
    """Compatibility entry point for periodic device discovery.

    The discovery mechanism and config now live in ``runtime/discovery.py``
    (``DiscoveryService``); this shim keeps ``server.py``'s daemon wiring unchanged.
    New runtime logic must go in the runtime module, not here.
    """
    await discovery_service.run()


# ---------------------- #
# TCP and UDP Listeners  #
# ---------------------- #


async def tcpListener() -> None:
    """Compatibility entry point for the ESP TCP listener.

    The accept loop and config handshake now live in ``runtime/esp_connection_runtime.py``
    (``ESPConnectionListener`` / ``ESPConnectionRuntime.accept_connection``); this shim keeps
    ``server.py``'s daemon wiring unchanged. New runtime logic must go in the runtime module,
    not here.
    """
    await esp_connection_listener.run()


async def udpListener() -> None:
    """Compatibility entry point for the UDP telemetry listener.

    The socket loop now lives in ``runtime/telemetry_ingest.py`` (``TelemetryUDPListener``);
    this shim keeps ``server.py``'s daemon wiring unchanged until the listener daemons are
    restructured. New runtime logic must go in the runtime module, not here.
    """
    await telemetry_udp_listener.run()


def getRegisteredDevices() -> dict[str, ESPDeviceSession]:
    return esp_runtime.get_registered_devices()


# ---------------------- #
# Socket Management      #
# ---------------------- #


def closeDeviceConnections() -> None:
    esp_runtime.close_all()


async def getSingle(device: ESPDeviceSession) -> None:
    await esp_runtime.get_single(device)


async def startStreaming(device: ESPDeviceSession, Hz: int) -> None:
    await esp_runtime.start_streaming(device, Hz)


async def stopStreaming(device: ESPDeviceSession) -> None:
    await esp_runtime.stop_streaming(device)


async def setControl(device: ESPDeviceSession, controlName: str, controlState: str) -> None:
    await esp_runtime.set_control(device, controlName, controlState)


async def getStatus(device: ESPDeviceSession) -> None:
    await esp_runtime.get_status(device)


async def emergencyStop(device: ESPDeviceSession) -> None:
    await esp_runtime.emergency_stop(device)


def cleanup_device(device: ESPDeviceSession) -> None:
    esp_runtime.cleanup_device(device)


def remove_device(device: ESPDeviceSession) -> None:
    esp_runtime.remove_device(device)

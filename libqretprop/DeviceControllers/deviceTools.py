import libqretprop.mylogging as ml
from libqretprop.qlcp.enums import ControlState, PacketType
from libqretprop.qlcp.packets import (
    ControlPacket,
    SimplePacket,
    StreamStartPacket,
)
from libqretprop.runtime.discovery import discovery_service
from libqretprop.runtime.esp_connection_runtime import (
    TrackedCommandPacket,
    esp_connection_listener,
    esp_runtime,
)
from libqretprop.runtime.esp_device_session import ESPDeviceSession
from libqretprop.runtime.telemetry_ingest import telemetry_udp_listener


deviceRegistry = esp_runtime.devices


class _LegacyESPLogSink:
    """Temporary GUI compatibility sink for legacy text-log device events."""

    def device_connected(self, session: ESPDeviceSession) -> None:
        ml.log(f"{session.name} CONNECTED")

    def device_disconnected(self, session: ESPDeviceSession) -> None:
        ml.log(f"{session.name} DISCONNECTED")

    def control_status(self, session: ESPDeviceSession, control_name: str, state: str) -> None:
        ml.log(f"{session.name} STATUS {control_name} {state}")


esp_runtime.legacy_log_sink = _LegacyESPLogSink()


async def _send_tracked_command(session: ESPDeviceSession, packet: TrackedCommandPacket):
    return await esp_runtime.send_tracked_command(session, packet)


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
# TCP and UDP Listeners
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
# Socket Management
# ---------------------- #


def closeDeviceConnections() -> None:
    esp_runtime.close_all()


async def getSingle(device: ESPDeviceSession) -> None:
    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.GET_SINGLE)
            await _send_tracked_command(device, packet)
            ml.slog(f"Sent GET_SINGLE command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending GET_SINGLE command to {device.name}: {e}")
            if device.address in deviceRegistry:
                remove_device(device)
    else:
        ml.elog(f"No socket available for {device.name} to send GET_SINGLE command.")
        remove_device(device)


async def startStreaming(device: ESPDeviceSession, Hz: int) -> None:
    if not Hz or Hz < 1 or Hz > 65535:
        ml.elog(f"Invalid frequency: {Hz}. Must be between 1-65535 Hz.")
        return

    if device.socket:
        try:
            packet = StreamStartPacket.create(frequency_hz=Hz)
            await _send_tracked_command(device, packet)
            ml.slog(f"Sent STREAM_START ({Hz} Hz) to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STREAM_START command to {device.name}: {e}")
            if device.address in deviceRegistry:
                remove_device(device)
    else:
        ml.elog(f"No socket available for {device.name} to send STREAM_START command.")
        remove_device(device)


async def stopStreaming(device: ESPDeviceSession) -> None:
    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.STREAM_STOP)
            await _send_tracked_command(device, packet)
            ml.slog(f"Sent STREAM_STOP command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STREAM_STOP command to {device.name}: {e}")
            if device.address in deviceRegistry:
                remove_device(device)
    else:
        ml.elog(f"No socket available for {device.name} to send STREAM_STOP command.")
        remove_device(device)


async def setControl(device: ESPDeviceSession, controlName: str, controlState: str) -> None:
    controlName = controlName.upper()
    controlState = controlState.upper()

    if controlName not in device.controls:
        ml.elog(f"Invalid control name '{controlName}'. Valid: {list(device.controls.keys())}")
        return

    if controlState not in ["OPEN", "CLOSE"]:
        ml.elog(f"Invalid state '{controlState}'. Valid: OPEN, CLOSE")
        return

    command_id = device.controls[controlName].id
    state = ControlState.OPEN if controlState == "OPEN" else ControlState.CLOSED

    if device.socket:
        try:
            packet = ControlPacket.create(command_id=command_id, command_state=state)
            await _send_tracked_command(device, packet)
            ml.slog(f"Sent CONTROL command (id={command_id}, {controlName} {controlState}) to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending CONTROL command to {device.name}: {e}")
            if device.address in deviceRegistry:
                remove_device(device)
    else:
        ml.elog(f"No socket available for {device.name} to send CONTROL command.")
        remove_device(device)


async def getStatus(device: ESPDeviceSession) -> None:
    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.STATUS_REQUEST)
            await _send_tracked_command(device, packet)
            ml.slog(f"Sent STATUS_REQUEST command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STATUS_REQUEST command to {device.name}: {e}")
            if device.address in deviceRegistry:
                remove_device(device)
    else:
        ml.elog(f"No socket available for {device.name} to send STATUS_REQUEST command.")
        remove_device(device)


async def emergencyStop(device: ESPDeviceSession) -> None:
    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.ESTOP)
            await _send_tracked_command(device, packet)
            ml.slog(f"Sent EMERGENCY STOP command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending EMERGENCY STOP command to {device.name}: {e}")
            if device.address in deviceRegistry:
                remove_device(device)
    else:
        ml.elog(f"No socket available for {device.name} to send EMERGENCY STOP command.")
        remove_device(device)


def cleanup_device(device: ESPDeviceSession) -> None:
    esp_runtime.cleanup_device(device)


def remove_device(device: ESPDeviceSession) -> None:
    esp_runtime.remove_device(device)

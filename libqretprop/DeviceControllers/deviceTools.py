import asyncio
import contextlib
import json
import socket
import time

import libqretprop.mylogging as ml
from libqretprop.Devices.ESPDevice import ESPDevice
from libqretprop.drivers.esp import ESPDriver, ESPDriverConnectionClosedError
from libqretprop.qlcp.decoding import decode_packet_server
from libqretprop.qlcp.enums import ControlState, PacketType
from libqretprop.qlcp.packets import (
    ConfigPacket,
    ControlPacket,
    DataPacket,
    SimplePacket,
    StreamStartPacket,
)
from libqretprop.runtime.esp_connection_runtime import TrackedCommandPacket, esp_runtime


MULTICAST_ADDRESS = "239.255.255.250"
MULTICAST_PORT = 1900

TCP_PORT = 50000
UDP_PORT = 50001  # These wouldn't overlap but a different port number is useful for debugging

AUTODISCOVER_ENABLED = True
AUTODISCOVER_INTERVAL_S = 30.0  # seconds between SSDP discovery broadcasts

# Searching Globals #
ssdpSearchSocket: socket.socket | None = None

# Listening Globals #
tcpListenerSocket: socket.socket | None = None
deviceRegistry = esp_runtime.devices


class _LegacyESPLogSink:
    """Temporary GUI compatibility sink for legacy text-log device events."""

    def device_connected(self, device: ESPDevice) -> None:
        ml.log(f"{device.name} CONNECTED")

    def device_disconnected(self, device: ESPDevice) -> None:
        ml.log(f"{device.name} DISCONNECTED")

    def control_status(self, device: ESPDevice, control_name: str, state: str) -> None:
        ml.log(f"{device.name} STATUS {control_name} {state}")


esp_runtime.legacy_log_sink = _LegacyESPLogSink()


def _isCurrentConnection(device: ESPDevice) -> bool:
    return esp_runtime.is_current_connection(device)


def _disconnectRegisteredDevice(device: ESPDevice) -> None:
    esp_runtime._disconnect_registered_device(device)


def _disconnectRegisteredDevicesWithName(device_name: str) -> None:
    esp_runtime.disconnect_registered_devices_with_name(device_name)


def _trackSentCommand(device: ESPDevice, packet: TrackedCommandPacket):
    return esp_runtime._track_sent_command(device, packet)


async def _sendTrackedCommand(device: ESPDevice, packet: TrackedCommandPacket):
    return await esp_runtime.send_tracked_command(device, packet)


# ---------------------- #
# Active Searching Tools #
# ---------------------- #


def sendDiscoveryBroadcast() -> None:
    global ssdpSearchSocket

    if ssdpSearchSocket is None:
        ssdpSearchSocket = _createSSDPSocket()

    ml.dlog("Sending SSDP multicast discovery request.")

    ssdpRequest = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {MULTICAST_ADDRESS}:{MULTICAST_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: urn:qretprop:espdevice:1\r\n"
        "USER-AGENT: QRET/1.0\r\n"
        "\r\n"
    )

    ssdpSearchSocket.sendto(ssdpRequest.encode(), (MULTICAST_ADDRESS, MULTICAST_PORT))


async def autoDiscoveryLoop() -> None:
    """Periodically send SSDP discovery broadcasts every AUTODISCOVER_INTERVAL seconds."""
    while True:
        if AUTODISCOVER_ENABLED:
            sendDiscoveryBroadcast()
            await asyncio.sleep(AUTODISCOVER_INTERVAL_S)
        else:
            await asyncio.sleep(0.5)


def _createSSDPSocket() -> socket.socket:
    """Create a send-only SSDP socket for broadcasting discovery."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    with contextlib.suppress(OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    # Choose outbound interface
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)

    sock.setblocking(False)
    ml.slog(f"SSDP broadcast socket initialized for {MULTICAST_ADDRESS}:{MULTICAST_PORT}")
    return sock


# ---------------------- #
# TCP and UDP Listeners
# ---------------------- #


async def tcpListener() -> None:
    """Listen for incoming TCP connections from devices on port 50000."""
    global deviceRegistry

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("0.0.0.0", TCP_PORT))
    server_socket.listen(5)
    server_socket.setblocking(False)

    ml.slog(f"TCP listener started on port {TCP_PORT}")

    loop = asyncio.get_event_loop()

    while True:
        try:
            client_socket, addr = await loop.sock_accept(server_socket)
            client_socket.setblocking(False)
            deviceIP = addr[0]

            ml.slog(f"Accepted TCP connection from {deviceIP}")

            driver = ESPDriver(client_socket, deviceIP)
            try:
                packet = await driver.read_packet()
            except ESPDriverConnectionClosedError:
                ml.elog(f"Device {deviceIP} disconnected during config.")
                client_socket.close()
                continue

            if isinstance(packet, ConfigPacket):
                config_dict = json.loads(packet.config_json)

                await esp_runtime.register_configured_device(
                    client_socket,
                    deviceIP,
                    config_dict,
                    packet.sequence,
                    loop=loop,
                )

        except asyncio.CancelledError:
            ml.slog("TCP listener cancelled")
            server_socket.close()
            raise
        except Exception as e:
            ml.elog(f"Error in TCP listener: {e}")
            await asyncio.sleep(0.1)

async def udpListener() -> None:
    """Listen for incoming UDP packets from devices"""
    loop = asyncio.get_event_loop()
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    udp_socket.bind(("0.0.0.0", UDP_PORT))
    udp_socket.setblocking(False)

    ml.slog(f"UDP listener started on port {UDP_PORT}")

    UDP_BATCH_SIZE = 128  # Max packets per event-loop tick before yielding to TCP/other tasks

    while True:
        try:
            data, addr = await loop.sock_recvfrom(udp_socket, 4096)

            # Process first packet plus any already-buffered ones, up to UDP_BATCH_SIZE
            # This prevents the UDP listener from blocking the event loop for too long if commands need to be processed
            for _ in range(UDP_BATCH_SIZE):
                deviceIP = addr[0]
                if deviceIP in deviceRegistry:
                    device = deviceRegistry[deviceIP]
                    packet = decode_packet_server(data)

                    if isinstance(packet, DataPacket):
                        timestamp_ms = packet.timestamp
                        readings = packet.readings
                        t = timestamp_ms / 1000.0 if device.last_sync_time is not None else time.monotonic()
                        sensor_names = device.sensor_names

                        for reading in readings:
                            sid = reading.sensor_id
                            value = reading.value

                            if sid < len(sensor_names):
                                sensor_name = sensor_names[sid]
                                ml.log(f"{device.name} {t:.3f} {sensor_name}:{value:.2f}")
                    else:
                        ml.elog(f"Received non-DATA packet over UDP from {device.name}. Ignoring.")
                else:
                    ml.elog(f"Received UDP packet from unknown device {deviceIP}")

                try:
                    data, addr = udp_socket.recvfrom(4096)
                except BlockingIOError:
                    break

            await asyncio.sleep(0)  # Yield to let other tasks run

        except asyncio.CancelledError:
            ml.slog("UDP listener cancelled")
            udp_socket.close()
            raise
        except Exception as e:
            ml.elog(f"Error in UDP listener: {e}")
            await asyncio.sleep(0.1)


def getRegisteredDevices() -> dict[str, ESPDevice]:
    return esp_runtime.get_registered_devices()


# ---------------------- #
# Socket Management
# ---------------------- #


def closeDeviceConnections() -> None:
    esp_runtime.close_all()


# ---------------------- #
# Device Monitoring
# ---------------------- #


async def _monitorSingleDevice(device: ESPDevice) -> None:
    await esp_runtime.monitor_device(device)

# ---------------------- #
# Device Control Tools
# ---------------------- #


async def getSingle(device: ESPDevice) -> None:
    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.GET_SINGLE)
            await _sendTrackedCommand(device, packet)
            ml.slog(f"Sent GET_SINGLE command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending GET_SINGLE command to {device.name}: {e}")
            if device.address in deviceRegistry:
                removeDevice(device)
    else:
        ml.elog(f"No socket available for {device.name} to send GET_SINGLE command.")
        removeDevice(device)


async def startStreaming(device: ESPDevice, Hz: int) -> None:
    if not Hz or Hz < 1 or Hz > 65535:
        ml.elog(f"Invalid frequency: {Hz}. Must be between 1-65535 Hz.")
        return

    if device.socket:
        try:
            packet = StreamStartPacket.create(frequency_hz=Hz)
            await _sendTrackedCommand(device, packet)
            ml.slog(f"Sent STREAM_START ({Hz} Hz) to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STREAM_START command to {device.name}: {e}")
            if device.address in deviceRegistry:
                removeDevice(device)
    else:
        ml.elog(f"No socket available for {device.name} to send STREAM_START command.")
        removeDevice(device)


async def stopStreaming(device: ESPDevice) -> None:
    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.STREAM_STOP)
            await _sendTrackedCommand(device, packet)
            ml.slog(f"Sent STREAM_STOP command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STREAM_STOP command to {device.name}: {e}")
            if device.address in deviceRegistry:
                removeDevice(device)
    else:
        ml.elog(f"No socket available for {device.name} to send STREAM_STOP command.")
        removeDevice(device)


async def setControl(device: ESPDevice, controlName: str, controlState: str) -> None:
    controlName = controlName.upper()
    controlState = controlState.upper()

    if controlName not in device.controls:
        ml.elog(f"Invalid control name '{controlName}'. Valid: {list(device.controls.keys())}")
        return

    if controlState not in ["OPEN", "CLOSE"]:
        ml.elog(f"Invalid state '{controlState}'. Valid: OPEN, CLOSE")
        return

    control_names = list(device.controls.keys())
    command_id = control_names.index(controlName)

    state = ControlState.OPEN if controlState == "OPEN" else ControlState.CLOSED

    if device.socket:
        try:
            packet = ControlPacket.create(command_id=command_id, command_state=state)
            await _sendTrackedCommand(device, packet)
            ml.slog(f"Sent CONTROL command (id={command_id}, {controlName} {controlState}) to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending CONTROL command to {device.name}: {e}")
            if device.address in deviceRegistry:
                removeDevice(device)
    else:
        ml.elog(f"No socket available for {device.name} to send CONTROL command.")
        removeDevice(device)


async def getStatus(device: ESPDevice) -> None:
    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.STATUS_REQUEST)
            await _sendTrackedCommand(device, packet)
            ml.slog(f"Sent STATUS_REQUEST command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STATUS_REQUEST command to {device.name}: {e}")
            if device.address in deviceRegistry:
                removeDevice(device)
    else:
        ml.elog(f"No socket available for {device.name} to send STATUS_REQUEST command.")
        removeDevice(device)


async def emergencyStop(device: ESPDevice) -> None:
    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.ESTOP)
            await _sendTrackedCommand(device, packet)
            ml.slog(f"Sent EMERGENCY STOP command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending EMERGENCY STOP command to {device.name}: {e}")
            if device.address in deviceRegistry:
                removeDevice(device)
    else:
        ml.elog(f"No socket available for {device.name} to send EMERGENCY STOP command.")
        removeDevice(device)


def cleanupDevice(device: ESPDevice) -> None:
    esp_runtime.cleanup_device(device)


def removeDevice(device: ESPDevice) -> None:
    esp_runtime.remove_device(device)

import asyncio
import contextlib
import json
import socket
import time

import libqretprop.mylogging as ml
from libqretprop.Devices.ESPDevice import ESPDevice
from libqretprop.drivers.esp import ESPDriver, ESPDriverConnectionClosedError
from libqretprop.qlcp.constants import HEADER_SIZE
from libqretprop.qlcp.decoding import decode_packet_server
from libqretprop.qlcp.enums import ControlState, PacketType
from libqretprop.qlcp.framing import get_packet_len
from libqretprop.qlcp.packets import (
    AckPacket,
    ConfigPacket,
    ControlPacket,
    DataPacket,
    NackPacket,
    SimplePacket,
    StatusPacket,
    StreamStartPacket,
)


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
deviceRegistry: dict[str, ESPDevice] = {}


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

                # If a device with the same IP is already registered, close the old connection before registering the new one
                # Prevents issues with devices rebooting and reconnecting before the disconnect is detected
                if deviceIP in deviceRegistry:
                    ml.elog(f"Device {deviceIP} attempted to connect and is already registered. Closing old connection.")
                    cleanupDevice(deviceRegistry[deviceIP])
                    del deviceRegistry[deviceIP]

                newDevice = ESPDevice(client_socket, deviceIP, config_dict)

                deviceRegistry[deviceIP] = newDevice

                listenerTask = loop.create_task(_monitorSingleDevice(newDevice))
                deviceRegistry[deviceIP].listenerTask = listenerTask

                ml.slog(f"Device {newDevice.name} registered from {deviceIP}")
                ml.log(f"{newDevice.name} CONNECTED")  # Used by GUI to trigger device addition

                # ACK the CONFIG, then send initial TIMESYNC

                ack = AckPacket.create(PacketType.CONFIG, packet.sequence)
                await newDevice.driver.send_packet(ack)

                timesync = SimplePacket.create(PacketType.TIMESYNC)
                await newDevice.driver.send_packet(timesync)
                ml.plog(f"Sent initial TIMESYNC to {newDevice.name}")

                status_request = SimplePacket.create(PacketType.STATUS_REQUEST)
                await newDevice.driver.send_packet(status_request)
                ml.plog(f"Sent initial STATUS_REQUEST to {newDevice.name}")

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
    return deviceRegistry.copy()


# ---------------------- #
# Socket Management
# ---------------------- #


def closeDeviceConnections() -> None:
    global deviceRegistry

    for device in deviceRegistry.values():
        if device.socket:
            try:
                device.listenerTask.cancel()
                device.socket.close()
                ml.slog(f"Closed socket for device {device.name}")
            except OSError as e:
                ml.elog(f"Error closing socket for device {device.name}: {e}")

    deviceRegistry.clear()
    ml.slog("Closed all device sockets and cleared registry.")


# ---------------------- #
# Device Monitoring
# ---------------------- #


async def _monitorSingleDevice(device: ESPDevice) -> None:
    """Monitor a single device using LENGTH-based framing from v2 header."""
    loop = asyncio.get_event_loop()
    buffer = b""

    try:
        while True:
            data = await loop.sock_recv(device.socket, 4096)
            if not data:
                ml.elog(f"Device {device.name} disconnected.")
                removeDevice(device)
                break

            buffer += data

            # Use LENGTH field for framing
            while len(buffer) >= HEADER_SIZE:
                try:
                    packet_len = get_packet_len(buffer)
                    if len(buffer) < packet_len:
                        break  # Need more data

                    packet_data = buffer[:packet_len]
                    packet = decode_packet_server(packet_data)

                    ml.plog(f"Decoded {type(packet).__name__} from {device.name}")

                    match packet:
                        case DataPacket():
                            ml.elog(f"Unexpected DATA packet received over TCP from {device.name}. This should be sent over UDP. Ignoring.")

                        case StatusPacket(control_states=control_states) if control_states:
                            for control_state in control_states:
                                control_names = list(device.controls.keys())
                                if control_state.id < len(control_names):
                                    control_name = control_names[control_state.id]
                                    state_str = (
                                        "OPEN"
                                        if control_state.state == ControlState.OPEN
                                        else "CLOSED"
                                        if control_state.state == ControlState.CLOSED
                                        else "UNKNOWN"
                                    )
                                    device.setControlState(control_name, state_str)
                                    ml.log(f"{device.name} STATUS {control_name} {state_str}")

                        case AckPacket():
                            if packet.ack_packet_type == PacketType.TIMESYNC:
                                device.last_sync_time = time.monotonic()
                                device._resync_pending = False
                                ml.plog(f"{device.name} TIMESYNC completed")
                            elif packet.ack_packet_type == PacketType.HEARTBEAT:
                                device.handleHeartbeatAck(packet.ack_sequence)
                                ml.plog(f"{device.name} HEARTBEAT ACK seq={packet.ack_sequence}")
                            elif packet.ack_packet_type == PacketType.CONTROL:
                                # Check for pending control command
                                if packet.ack_sequence in device._pending_controls:
                                    control_name, state = device._pending_controls.pop(packet.ack_sequence)

                                    # Send status log for control ACK
                                    state_str = "OPEN" if state == "OPEN" else "CLOSED" if state == "CLOSE" else "UNKNOWN"
                                    if control_name in device.controls:
                                        device.setControlState(control_name, state_str)
                                        ml.log(f"{device.name} STATUS {control_name} {state_str}")
                                else:
                                    ml.plog(f"{device.name} ACK for CONTROL seq={packet.ack_sequence}")
                            else:
                                ml.plog(f"{device.name} ACK for {packet.ack_packet_type.name} seq={packet.ack_sequence}")

                        case NackPacket():
                            ml.plog(f"{device.name} NACK for {packet.nack_packet_type.name} error={packet.error_code.name}")

                        case _:
                            ml.elog(f"Received unexpected packet type {type(packet).__name__} from {device.name} over TCP")

                    buffer = buffer[packet_len:]

                    # Periodic resync check
                    if (
                        not device._resync_pending
                        and device.last_sync_time is not None
                        and time.monotonic() - device.last_sync_time > ESPDevice.RESYNC_INTERVAL_S
                    ):
                        device._resync_pending = True
                        timesync = SimplePacket.create(PacketType.TIMESYNC)
                        await device.driver.send_packet(timesync)
                        ml.plog(f"{device.name} resync sent (stale >{ESPDevice.RESYNC_INTERVAL_S / 60:.0f} min)")

                except ValueError:
                    break
                except Exception as e:
                    ml.elog(f"Error decoding packet from {device.name}: {e}")
                    buffer = buffer[1:]

    except asyncio.CancelledError:
        ml.slog(f"Stopped monitoring {device.name}")
        raise
    except Exception as e:
        ml.elog(f"Error receiving response from {device.name}: {e}")
        if device.address in deviceRegistry:
            removeDevice(device)

# ---------------------- #
# Device Control Tools
# ---------------------- #


async def getSingle(device: ESPDevice) -> None:
    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.GET_SINGLE)
            await device.driver.send_packet(packet)
            ml.slog(f"Sent GET_SINGLE command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending GET_SINGLE command to {device.name}: {e}")
            if device.address in deviceRegistry:
                _removed = deviceRegistry.pop(device.address)
                ml.slog(f"{_removed.name} removed from registry")
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
            await device.driver.send_packet(packet)
            ml.slog(f"Sent STREAM_START ({Hz} Hz) to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STREAM_START command to {device.name}: {e}")
            if device.address in deviceRegistry:
                _removed = deviceRegistry.pop(device.address)
                ml.slog(f"{device.name} removed from registry")
    else:
        ml.elog(f"No socket available for {device.name} to send STREAM_START command.")
        removeDevice(device)


async def stopStreaming(device: ESPDevice) -> None:
    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.STREAM_STOP)
            await device.driver.send_packet(packet)
            ml.slog(f"Sent STREAM_STOP command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STREAM_STOP command to {device.name}: {e}")
            if device.address in deviceRegistry:
                _removed = deviceRegistry.pop(device.address)
                ml.slog(f"{device.name} removed from registry")
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

            # Store pending control BEFORE sending (to avoid race condition)
            device._pending_controls[packet.sequence] = (controlName, controlState.upper())

            await device.driver.send_packet(packet)
            ml.slog(f"Sent CONTROL command (id={command_id}, {controlName} {controlState}) to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending CONTROL command to {device.name}: {e}")
            # Clean up pending control on send failure
            if packet.sequence in device._pending_controls:
                device._pending_controls.pop(packet.sequence)
            if device.address in deviceRegistry:
                removeDevice(device)
    else:
        ml.elog(f"No socket available for {device.name} to send CONTROL command.")


async def getStatus(device: ESPDevice) -> None:
    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.STATUS_REQUEST)
            await device.driver.send_packet(packet)
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
            await device.driver.send_packet(packet)
            ml.slog(f"Sent EMERGENCY STOP command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending EMERGENCY STOP command to {device.name}: {e}")
            if device.address in deviceRegistry:
                removeDevice(device)


def cleanupDevice(device: ESPDevice) -> None:
    if device.address in deviceRegistry:
        if device.socket:
            try:
                device.socket.close()
                ml.slog(f"Closed socket for {device.name}")
            except OSError as e:
                ml.elog(f"Error closing socket for {device.name}: {e}")
            finally:
                device.socket = None

    # Cancel any per-device listener task to avoid it running against a closed socket
    listener_task = getattr(device, "listenerTask", None)
    if listener_task is not None:
        try:
            listener_task.cancel()
            ml.slog(f"Cancelled listener task for {device.name}")
        except Exception as e:
            ml.elog(f"Error cancelling listener task for {device.name}: {e}")

    heartbeat_task = getattr(device, "heartbeat_task", None)
    if heartbeat_task is not None:
        try:
            heartbeat_task.cancel()
            ml.slog(f"Cancelled heartbeat task for {device.name}")
        except Exception as e:
            ml.elog(f"Error cancelling heartbeat task for {device.name}: {e}")


def removeDevice(device: ESPDevice) -> None:
    if device.address in deviceRegistry:
        cleanupDevice(device)
        del deviceRegistry[device.address]

        ml.slog(f"{device.name} removed from registry.")
        ml.log(f"{device.name} DISCONNECTED")  # Used by GUI to trigger device removal

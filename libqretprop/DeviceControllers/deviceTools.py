import asyncio
import contextlib
import csv
import socket
import time


import libqretprop.mylogging as ml
from libqretprop.Devices.ESPDevice import ESPDevice
from libqretprop.Devices.SensorMonitor import SensorMonitor
from libqretprop.protocol import ControlState, DataPacket, PacketHeader, PacketType, Unit, decode_packet


MULTICAST_ADDRESS = "239.255.255.250"
MULTICAST_PORT = 1900

TCP_PORT = 50000
UDP_PORT = 50001 # These wouldn't overlap but a different port number is useful for debugging

AUTODISCOVER_ENABLED = True
AUTODISCOVER_INTERVAL_S = 30.0 # seconds between SSDP discovery broadcasts

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

            # Read header first (9 bytes) to get packet length
            import json

            from libqretprop.protocol import PacketHeader, PacketType, decode_packet

            header_bytes = b""
            while len(header_bytes) < PacketHeader.SIZE:
                chunk = await loop.sock_recv(client_socket, PacketHeader.SIZE - len(header_bytes))
                if not chunk:
                    ml.elog(f"Device {deviceIP} disconnected during config.")
                    client_socket.close()
                    break
                header_bytes += chunk
            else:
                header = PacketHeader.unpack(header_bytes)
                remaining = header.length - PacketHeader.SIZE
                payload_bytes = b""
                while len(payload_bytes) < remaining:
                    chunk = await loop.sock_recv(client_socket, remaining - len(payload_bytes))
                    if not chunk:
                        ml.elog(f"Device {deviceIP} disconnected during config payload.")
                        client_socket.close()
                        break
                    payload_bytes += chunk
                else:
                    full_packet = header_bytes + payload_bytes
                    packet = decode_packet(full_packet)

                    if packet.header.packet_type == PacketType.CONFIG:
                        config_dict = json.loads(packet.config_json)

                        if config_dict.get("deviceType") in {"Sensor Monitor", "Simulated Sensor Monitor"}:
                            newDevice = SensorMonitor(client_socket, deviceIP, config_dict)
                        else:
                            newDevice = ESPDevice(client_socket, deviceIP, config_dict)

                        deviceRegistry[deviceIP] = newDevice

                        listenerTask = loop.create_task(_monitorSingleDevice(newDevice))
                        deviceRegistry[deviceIP].listenerTask = listenerTask

                        ml.slog(f"Device {newDevice.name} registered from {deviceIP}")
                        ml.log(f"{newDevice.name} CONNECTED") # Used by GUI to trigger device addition

                        # ACK the CONFIG, then send initial TIMESYNC
                        from libqretprop.protocol import AckPacket, SimplePacket

                        ack = AckPacket.create(PacketType.CONFIG, packet.header.sequence)
                        await loop.sock_sendall(client_socket, ack.pack())

                        timesync = SimplePacket.create(PacketType.TIMESYNC)
                        await loop.sock_sendall(client_socket, timesync.pack())
                        ml.plog(f"Sent initial TIMESYNC to {newDevice.name}")

                        status_request = SimplePacket.create(PacketType.STATUS_REQUEST)
                        await loop.sock_sendall(client_socket, status_request.pack())
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
                    if isinstance(device, SensorMonitor):
                        # Fast-path for DATA packets (100% of UDP traffic):
                        # Check packet type from raw bytes without full decode_packet.
                        packet_type = data[1]
                        if packet_type == PacketType.DATA:
                            _, _, _, _, timestamp_ms, readings = _unpackDataPacketFast(data)
                            t = timestamp_ms / 1000.0 if device.last_sync_time is not None else time.monotonic()
                            sensor_names = device.sensor_names
                            sensors = device.sensors
                            for sid, _, value in readings:
                                if sid < len(sensor_names):
                                    sensor_name = sensor_names[sid]
                                    sensors[sensor_name].data.append(value)
                                    ml.log(f"{device.name} {t:.3f} {sensor_name}:{value:.2f}")
                            device.times.append(t)
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
    from libqretprop.protocol import PacketHeader, PacketType, SimplePacket, decode_packet

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
            while len(buffer) >= PacketHeader.SIZE:
                try:
                    header = PacketHeader.unpack(buffer)

                    if len(buffer) < header.length:
                        break  # Need more data

                    packet_data = buffer[: header.length]
                    packet = decode_packet(packet_data)

                    ml.plog(f"Decoded {packet.header.packet_type.name} from {device.name}")

                    if packet.header.packet_type == PacketType.DATA:
                        ml.elog(f"Unexpected DATA packet received over TCP from {device.name}. This should be sent over UDP. Ignoring.")

                    elif packet.header.packet_type == PacketType.STATUS:
                        # If SensorMonitor, log control states
                        if isinstance(device, SensorMonitor) and packet.control_states:
                            # Read control states from payload (if any) and update internal state
                            for control_state in packet.control_states:
                                control_names = list(device.controls.keys())
                                if control_state.id < len(control_names):
                                    control_name = control_names[control_state.id]
                                    state_str = "OPEN" if control_state.state == ControlState.OPEN else "CLOSED" if control_state.state == ControlState.CLOSED else "UNKNOWN"
                                    device.controls[control_name].state = state_str
                                    ml.log(f"{device.name} STATUS {control_name} {state_str}")

                    elif packet.header.packet_type == PacketType.ACK:
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
                                if isinstance(device, SensorMonitor) and control_name in device.controls:
                                    device.controls[control_name].state = state
                                    ml.log(f"{device.name} STATUS {control_name} {state_str}")
                            else:
                                ml.plog(f"{device.name} ACK for CONTROL seq={packet.ack_sequence}")
                        else:
                            ml.plog(f"{device.name} ACK for {packet.ack_packet_type.name} seq={packet.ack_sequence}")

                    elif packet.header.packet_type == PacketType.NACK:
                        ml.plog(f"{device.name} NACK for {packet.nack_packet_type.name} error={packet.error_code.name}")

                    buffer = buffer[header.length :]

                    # Periodic resync check
                    if (
                        not device._resync_pending
                        and device.last_sync_time is not None
                        and time.monotonic() - device.last_sync_time > ESPDevice.RESYNC_INTERVAL_S
                    ):
                        device._resync_pending = True
                        timesync = SimplePacket.create(PacketType.TIMESYNC)
                        await loop.sock_sendall(device.socket, timesync.pack())
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
# Data Packet Processing Tools
# ---------------------- #

def _unpackDataPacketFast(data: bytes) -> tuple[int, int, int, int, int, list[tuple[int, int, float]]]:
    """Fast inline unpacking of DATA packets using pre-compiled structs.
    Returns: (version, packet_type, sequence, length, timestamp, [(sensor_id, unit, value), ...])
    Avoids SensorReading object allocation.
    """
    # Use PacketHeader's pre-compiled struct for fast header unpack
    version, packet_type, sequence, length, timestamp = PacketHeader._STRUCT.unpack_from(data, 0)

    # Parse reading count at byte 9
    count = data[9]

    # Parse readings using DataPacket's pre-compiled struct
    readings = []
    offset = 10
    for _ in range(count):
        sid, unit_val, value = DataPacket._READING_STRUCT.unpack_from(data, offset)
        readings.append((sid, unit_val, value))
        offset += DataPacket.READING_SIZE

    return version, packet_type, sequence, length, timestamp, readings

# ---------------------- #
# Device Control Tools
# ---------------------- #


async def getSingle(device: ESPDevice) -> None:
    from libqretprop.protocol import PacketType, SimplePacket

    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.GET_SINGLE)
            loop = asyncio.get_event_loop()
            await loop.sock_sendall(device.socket, packet.pack())
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
    from libqretprop.protocol import StreamStartPacket

    if not Hz or Hz < 1 or Hz > 65535:
        ml.elog(f"Invalid frequency: {Hz}. Must be between 1-65535 Hz.")
        return

    if device.socket:
        try:
            packet = StreamStartPacket.create(frequency_hz=Hz)
            loop = asyncio.get_event_loop()
            await loop.sock_sendall(device.socket, packet.pack())
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
    from libqretprop.protocol import PacketType, SimplePacket

    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.STREAM_STOP)
            loop = asyncio.get_event_loop()
            await loop.sock_sendall(device.socket, packet.pack())
            ml.slog(f"Sent STREAM_STOP command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STREAM_STOP command to {device.name}: {e}")
            if device.address in deviceRegistry:
                _removed = deviceRegistry.pop(device.address)
                ml.slog(f"{device.name} removed from registry")
    else:
        ml.elog(f"No socket available for {device.name} to send STREAM_STOP command.")
        removeDevice(device)


async def setControl(device: SensorMonitor, controlName: str, controlState: str) -> None:
    from libqretprop.protocol import ControlPacket
    from libqretprop.protocol import ControlState as CS

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

    state = CS.OPEN if controlState == "OPEN" else CS.CLOSED

    if device.socket:
        try:
            packet = ControlPacket.create(command_id=command_id, command_state=state)

            # Store pending control BEFORE sending (to avoid race condition)
            device._pending_controls[packet.header.sequence] = (controlName, controlState.upper())

            loop = asyncio.get_event_loop()
            await loop.sock_sendall(device.socket, packet.pack())
            ml.slog(f"Sent CONTROL command (id={command_id}, {controlName} {controlState}) to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending CONTROL command to {device.name}: {e}")
            # Clean up pending control on send failure
            if packet.header.sequence in device._pending_controls:
                device._pending_controls.pop(packet.header.sequence)
            if device.address in deviceRegistry:
                removeDevice(device)
    else:
        ml.elog(f"No socket available for {device.name} to send CONTROL command.")

async def getStatus(device: ESPDevice) -> None:
    from libqretprop.protocol import PacketType, SimplePacket

    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.STATUS_REQUEST)
            loop = asyncio.get_event_loop()
            await loop.sock_sendall(device.socket, packet.pack())
            ml.slog(f"Sent STATUS_REQUEST command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STATUS_REQUEST command to {device.name}: {e}")
            if device.address in deviceRegistry:
                removeDevice(device)
    else:
        ml.elog(f"No socket available for {device.name} to send STATUS_REQUEST command.")
        removeDevice(device)

def removeDevice(device: ESPDevice) -> None:
    if device.address in deviceRegistry:
        if device.socket:
            try:
                device.socket.close()
                ml.slog(f"Closed socket for {device.name}")
            except OSError as e:
                ml.elog(f"Error closing socket for {device.name}: {e}")
            finally:
                # Ensure other code can detect that the device is no longer connected
                device.socket = None

        # Cancel any per-device listener task to avoid it running against a closed socket
        listener_task = getattr(device, "listenerTask", None)
        if listener_task is not None:
            try:
                listener_task.cancel()
                ml.slog(f"Cancelled listener task for {device.name}")
            except Exception as e:
                ml.elog(f"Error cancelling listener task for {device.name}: {e}")
        deviceRegistry.pop(device.address)
        ml.slog(f"{device.name} removed from registry.")
        ml.log(f"{device.name} DISCONNECTED") # Used by GUI to trigger device removal


# ---------------------- #
# Data Export Tools
# ---------------------- #


def exportDataToCSV() -> None:
    testTime = time.strftime("%Y%m%d-%H%M%S")

    for device in deviceRegistry.values():
        if isinstance(device, SensorMonitor):
            testTime = time.strftime("%Y%m%d-%H:%M:%S")
            deviceFilename = f"test_data/{device.name}_{testTime}.csv"

            sensorNames = [sensor.name for sensor in device.sensors.values()]

            with open(deviceFilename, mode="w", newline="") as csvfile:
                writer = csv.writer(csvfile)
                header = ["Time", *sensorNames]
                writer.writerow(header)
                for i in range(len(device.times)):
                    row = [device.times[i]] + [sensor.data[i] for sensor in device.sensors.values()]
                    writer.writerow(row)

            ml.slog(f"Exported data to {deviceFilename} for device: {device.name}")

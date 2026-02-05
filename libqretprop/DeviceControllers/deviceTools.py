import asyncio
import contextlib
import csv
import socket
import struct
import time

import libqretprop.mylogging as ml
from libqretprop.Devices.ESPDevice import ESPDevice
from libqretprop.Devices.SensorMonitor import SensorMonitor


MULTICAST_ADDRESS = "239.255.255.250"
MULTICAST_PORT = 1900

TCP_PORT = 50000

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
# TCP Listener
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
            from libqretprop.protocol import PacketHeader, decode_packet, PacketType
            import json

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

                        # ACK the CONFIG, then send initial TIMESYNC
                        from libqretprop.protocol import AckPacket, TimeSyncPacket

                        ack = AckPacket.create(PacketType.CONFIG, packet.header.sequence)
                        await loop.sock_sendall(client_socket, ack.pack())

                        timesync = TimeSyncPacket.create()
                        await loop.sock_sendall(client_socket, timesync.pack())
                        ml.slog(f"Sent initial TIMESYNC to {newDevice.name}")

        except asyncio.CancelledError:
            ml.slog("TCP listener cancelled")
            server_socket.close()
            raise
        except Exception as e:
            ml.elog(f"Error in TCP listener: {e}")
            await asyncio.sleep(0.1)


def getRegisteredDevices() -> dict[str, ESPDevice]:
    return deviceRegistry.copy()


def getDeviceByName(name: str) -> ESPDevice | None:
    return deviceRegistry.get(name)


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
    from libqretprop.protocol import PacketHeader, decode_packet, PacketType, TimeSyncPacket

    buffer = b""

    try:
        while True:
            data = await loop.sock_recv(device.socket, 4096)
            if not data:
                ml.elog(f"Device {device.name} disconnected.")
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

                    ml.slog(f"Decoded {packet.header.packet_type.name} from {device.name}")

                    if packet.header.packet_type == PacketType.DATA and isinstance(device, SensorMonitor):
                        # Device timestamps are already in server monotonic ms (locked via TIMESYNC)
                        if device.last_sync_time is not None:
                            t = packet.header.timestamp / 1000.0 - device.startTime
                        else:
                            ml.log(f"WARNING: {device.name} data before TIMESYNC, using server time")
                            t = time.monotonic() - device.startTime

                        sensor_names = list(device.sensors.keys())
                        for reading in packet.readings:
                            if reading.sensor_id < len(sensor_names):
                                sensor_name = sensor_names[reading.sensor_id]
                                device.sensors[sensor_name].data.append(reading.value)
                                if not device.times or len(device.times) < len(device.sensors[sensor_name].data):
                                    device.times.append(t)
                                ml.log(f"{device.name} {t:.3f} {sensor_name}:{reading.value:.2f}")

                    elif packet.header.packet_type == PacketType.STATUS:
                        ml.log(f"{device.name} status: {packet.status.name}")

                    elif packet.header.packet_type == PacketType.ACK:
                        if packet.ack_packet_type == PacketType.TIMESYNC:
                            device.last_sync_time = time.monotonic()
                            device._resync_pending = False
                            ml.slog(f"{device.name} TIMESYNC completed")
                        elif packet.ack_packet_type == PacketType.CONTROL:
                            # Check for pending control command
                            if packet.ack_sequence in device._pending_controls:
                                control_name, state = device._pending_controls.pop(packet.ack_sequence)
                                ml.log(f"{device.name} CONTROL {control_name} {state}")
                            else:
                                ml.slog(f"{device.name} ACK for CONTROL seq={packet.ack_sequence}")
                        else:
                            ml.slog(f"{device.name} ACK for {packet.ack_packet_type.name} seq={packet.ack_sequence}")

                    elif packet.header.packet_type == PacketType.NACK:
                        ml.elog(f"{device.name} NACK for {packet.nack_packet_type.name} error={packet.error_code.name}")

                    buffer = buffer[header.length :]

                    # Periodic resync check
                    if (
                        not device._resync_pending
                        and device.last_sync_time is not None
                        and time.monotonic() - device.last_sync_time > ESPDevice.RESYNC_INTERVAL_S
                    ):
                        device._resync_pending = True
                        timesync = TimeSyncPacket.create()
                        await loop.sock_sendall(device.socket, timesync.pack())
                        ml.log(f"{device.name} resync sent (stale >{ESPDevice.RESYNC_INTERVAL_S / 60:.0f} min)")

                except ValueError:
                    break
                except Exception as e:
                    ml.elog(f"Error decoding packet from {device.name}: {e}")
                    buffer = buffer[1:]

    except Exception as e:
        ml.elog(f"Error receiving response from {device.name}: {e}")
        if device.address in deviceRegistry:
            _removed = deviceRegistry.pop(device.address)
            ml.slog(f"{device.name} removed from registry")


# ---------------------- #
# Device Control Tools
# ---------------------- #


async def getSingle(device: ESPDevice) -> None:
    from libqretprop.protocol import SimplePacket, PacketType

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


async def stopStreaming(device: ESPDevice) -> None:
    from libqretprop.protocol import SimplePacket, PacketType

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


async def setControl(device: SensorMonitor, controlName: str, controlState: str) -> None:
    from libqretprop.protocol import ControlPacket, ControlState as CS

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
                _removed = deviceRegistry.pop(device.address)
                ml.slog(f"{device.name} removed from registry.")
    else:
        ml.elog(f"No socket available for {device.name} to send CONTROL command.")


async def getStatus(device: ESPDevice) -> None:
    from libqretprop.protocol import SimplePacket, PacketType

    if device.socket:
        try:
            packet = SimplePacket.create(PacketType.STATUS_REQUEST)
            loop = asyncio.get_event_loop()
            await loop.sock_sendall(device.socket, packet.pack())
            ml.slog(f"Sent STATUS_REQUEST to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STATUS_REQUEST to {device.name}: {e}")
    else:
        ml.elog(f"No socket available for {device.name} to send STATUS_REQUEST.")


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

import asyncio
import contextlib
import csv
import socket
import struct
import time

import libqretprop.mylogging as ml
from libqretprop.Devices.ESPDevice import ESPDevice
from libqretprop.Devices.SensorMonitor import SensorMonitor
from libqretprop.Devices.sensors.LoadCell import LoadCell
from libqretprop.Devices.sensors.PressureTransducer import PressureTransducer
from libqretprop.Devices.sensors.Thermocouple import Thermocouple


MULTICAST_ADDRESS = "239.255.255.250"
MULTICAST_PORT = 1900

TCP_PORT = 50000  # Default TCP port for direct device communication

# Searching Globals #
ssdpSearchSocket : socket.socket | None = None

# Listening Globals #
tcpListenerSocket: socket.socket | None = None
deviceRegistry   : dict[str, ESPDevice] = {}  # Registry to keep track of discovered devices

# ---------------------- #
# Active Searching Tools #
# ---------------------- #

def sendMulticastDiscovery() -> None:
    global ssdpSearchSocket

    if ssdpSearchSocket is None:
        ml.elog("The device listener is not running. Start the listener before sending discovery requests.")
        return

    ml.dlog("Sending SSDP multicast discovery request.")

    ssdpRequest = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {MULTICAST_ADDRESS}:{MULTICAST_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"                         # Maximum wait time in seconds
        "ST: urn:qretprop:espdevice:1\r\n"     # Search target - custom for your devices
        "USER-AGENT: QRET/1.0\r\n"          # Identify your application
        "\r\n"
    )

    ssdpSearchSocket.sendto(ssdpRequest.encode(), (MULTICAST_ADDRESS, MULTICAST_PORT))

async def continuousMulticastDiscovery() -> None:
    """Send out a search request every 5 seconds."""
    while True:
        sendMulticastDiscovery()
        await asyncio.sleep(5)  # Wait for 5 seconds before sending the next request

async def connectToDevice(deviceIP: str) -> None:

    if deviceIP in deviceRegistry:
        ml.elog(f"Device {deviceIP} is already registered.")
        return

    loop = asyncio.get_event_loop()

    # Create TCP connection to device
    deviceSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    await loop.sock_connect(deviceSocket, (deviceIP, TCP_PORT))
    deviceSocket.setblocking(False)

    ml.slog(f"Established TCP connection to device at {deviceIP}:{TCP_PORT}")

    # After initial connection a device should send its config file so that the server can configure
    # the device and add it to the registry.
    config = await loop.sock_recv(deviceSocket, 2048)  # Config files are generally less han 2kB. Change if needed
    if config[:4].decode("utf-8") != "CONF":
        ml.elog("First message received from device was not a config file."
                f"Expected 'CONF' prefix and got {config[:4].decode('utf-8', errors='ignore')}")

    # Create new ESPDevice instance and add to registry
    newDevice = ESPDevice.fromConfigBytes(deviceSocket, deviceIP, config[4:])  # Skip the "CONF" prefix
    deviceRegistry[deviceIP] = newDevice

    # If we successfully connect to the device, start monitoring it
    listenerTask = loop.create_task(_monitorSingleDevice(deviceRegistry[deviceIP]))
    deviceRegistry[deviceIP].listenerTask = listenerTask

def _createSSDPSocket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    with contextlib.suppress(OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    # Receive on all interfaces, SSDP port
    sock.bind(("", MULTICAST_PORT))

    # Join the SSDP group on eth0 (your LAN IP)
    membershipRequest = socket.inet_aton(MULTICAST_ADDRESS) + socket.INADDR_ANY.to_bytes(4, "big")
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membershipRequest)

    # >>> CRITICAL FOR SENDING: choose outbound interface <<<
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.INADDR_ANY)

    # Optional but helpful
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)

    sock.setblocking(False)
    ml.slog(f"SSDP Listener socket initialized on {MULTICAST_ADDRESS}:{MULTICAST_PORT}")
    return sock
# ---------------------- #
# Discovery Listener Tools
# ---------------------- #

async def tcpListener() -> None:
    """Listen for incoming TCP connections from devices on port 50000."""
    global deviceRegistry

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('0.0.0.0', TCP_PORT))
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

            # Receive config packet
            config_bytes = await loop.sock_recv(client_socket, 2048)

            # Decode config packet using binary protocol
            from libqretprop.protocol import decode_packet, PacketType
            import json

            packet = decode_packet(config_bytes)

            if packet.header.packet_type == PacketType.CONFIG:
                # Parse the JSON config from the packet
                config_dict = json.loads(packet.config_json)

                # Create device from config dictionary
                newDevice = ESPDevice(client_socket, deviceIP, config_dict)

                # Special handling for SensorMonitor
                if config_dict.get("deviceType") in {"Sensor Monitor", "Simulated Sensor Monitor"}:
                    from libqretprop.Devices.SensorMonitor import SensorMonitor
                    newDevice = SensorMonitor(client_socket, deviceIP, config_dict)

                deviceRegistry[deviceIP] = newDevice

                # Start monitoring
                listenerTask = loop.create_task(_monitorSingleDevice(newDevice))
                deviceRegistry[deviceIP].listenerTask = listenerTask

                ml.slog(f"Device {newDevice.name} registered from {deviceIP}")

        except asyncio.CancelledError:
            ml.slog("TCP listener cancelled")
            server_socket.close()
            raise
        except Exception as e:
            ml.elog(f"Error in TCP listener: {e}")
            await asyncio.sleep(0.1)

async def deviceListener() -> None:
    """Listen for SSDP responses from devices."""
    global ssdpSearchSocket, deviceRegistry

    ssdpSearchSocket = _createSSDPSocket()

    ml.slog("Starting SSDP listener...")

    while True:
        try:
            # Use asyncio to handle non-blocking socket
            loop = asyncio.get_event_loop()
            data, addr = await loop.sock_recvfrom(ssdpSearchSocket, 1024)
            response = data.decode("utf-8", errors="ignore")

            # Validate SSDP response
            if all(required in response for required in [
                "HTTP/1.1 200 OK",
                "EXT:",
                "SERVER: ESP32/1.0 UPnP/1.0",
                "ST: urn:qretprop:espdevice:1",
            ]):
                deviceIP = addr[0]

                # Check if device is already registered
                if deviceIP in deviceRegistry:
                    ml.slog(f"Device {deviceIP} is already registered as {deviceRegistry[deviceIP].name}.")
                    continue

                ml.slog(f"New device discovered at {deviceIP}, attempting to connect...")

                try:
                    await connectToDevice(deviceIP)

                except Exception as e:
                    ml.elog(f"Failed to establish TCP connection to {deviceIP}: {e}")


        except asyncio.CancelledError:
            ml.slog("SSDP listener cancelled")
            raise
        except Exception as e:
            ml.elog(f"Error in SSDP listener: {e}")
            await asyncio.sleep(1)  # Prevent tight loop on errors

def getRegisteredDevices() -> dict[str, ESPDevice]:
    return deviceRegistry.copy()

def getDeviceByName(name: str) -> ESPDevice | None:
    return deviceRegistry.get(name)

# ---------------------- #
# Socket Management      #
# ---------------------- #

def closeDeviceConnections() -> None:
    """Close all device sockets."""
    global deviceRegistry

    for device in deviceRegistry.values():
        if device.socket:
            try:
                device.listenerTask.cancel()  # Cancel the listener task if it exists
                device.socket.close()
                ml.slog(f"Closed socket for device {device.name}")
            except OSError as e:
                ml.elog(f"Error closing socket for device {device.name}: {e}")

    deviceRegistry.clear()
    ml.slog("Closed all device sockets and cleared registry.")

# ---------------------- #
# Device Monitoring Tools
# ---------------------- #

async def _monitorSingleDevice(device: ESPDevice) -> None:
    """Monitor a single device for responses using binary protocol.

    This is called after a device is connected to continuously monitor the data it sends out.
    """
    loop = asyncio.get_event_loop()
    from libqretprop.protocol import decode_packet, PacketType

    buffer = b""  # Binary buffer

    try:
        while True:
            data = await loop.sock_recv(device.socket, 1024)
            if not data:
                ml.elog(f"Device {device.name} disconnected.")
                break

            buffer += data
            ml.slog(f"Received {len(data)} bytes from {device.name}, buffer={len(buffer)} bytes")

            # Try to decode binary packets
            while len(buffer) >= 10:  # Minimum packet size
                try:
                    packet = decode_packet(buffer)
                    packet_size = len(packet.pack())
                    ml.slog(f"Decoded packet type: {packet.header.packet_type.name}")

                    # Handle different packet types
                    if packet.header.packet_type == PacketType.DATA and isinstance(device, SensorMonitor):
                        # Get sensor name from ID
                        sensor_names = list(device.sensors.keys())
                        if packet.sensor_id < len(sensor_names):
                            sensor_name = sensor_names[packet.sensor_id]

                            # Store data point
                            device.sensors[sensor_name].data.append(packet.data)
                            if not device.times or len(device.times) < len(device.sensors[sensor_name].data):
                                device.times.append(time.monotonic() - device.startTime)

                            # Log it
                            ml.log(f"{device.name} {sensor_name}: {packet.data:.2f}")

                    elif packet.header.packet_type == PacketType.STATUS:
                        ml.log(f"{device.name} status: {packet.status.name}")

                    elif packet.header.packet_type == PacketType.ACK:
                        ml.slog(f"{device.name} ACK for packet type {packet.ack_packet_type.name}")

                    elif packet.header.packet_type == PacketType.NACK:
                        ml.elog(f"{device.name} NACK for packet type {packet.ack_packet_type.name}")

                    # Remove processed packet from buffer
                    buffer = buffer[packet_size:]

                except ValueError:
                    # Not enough data yet for a complete packet
                    break
                except Exception as e:
                    ml.elog(f"Error decoding packet from {device.name}: {e}")
                    buffer = buffer[1:]  # Skip one byte and try again

    except Exception as e:
        ml.elog(f"Error receiving response from {device.name}: {e}")
        # Remove the device from the device registry if there is an exception
        if device.address in deviceRegistry:
            _removed = deviceRegistry.pop(device.address)
            ml.slog(f"{device.name} removed from registry")

def publishSensorData(message: str, device: SensorMonitor) -> None:
    """Parse a stream data message from the device and publish the sensor data.

    Args:
        message (str): The message received from the device.
        device (ESPDevice): The device that sent the message.
    """
    message = message.strip()

    parts = message.split(" ")

    if len(parts) != len(device.sensors) + 1:
        ml.elog(f"Not enough data points received from {device.name}: {message}")
        ml.elog(f"Expected {len(device.sensors)} data points, got {len(parts)}. Stopping streaming.")
        stopStreaming(device)

    sensorValues: dict[str, float] = {}

    # Sensor data is expected to be in the format "time:timevalue sensor1:value1 sensor2:value2 ..."
    for val in parts:
        if ":" not in val:
            ml.elog(f"Invalid sensor data format from {device.name}: {val}")
            continue

        key, value = val.strip().split(":", 1)

        if key == "time":
            pass # Time is handled by the server! Maybe change this later #FIXME

        if key in device.sensors:
            sensorValues[key] = float(value)

    device.addDataPoints(sensorValues)
    time = device.times[-1]

    # Publish the sensor data to the redis log
    for sense, reading in sensorValues.items():
        ml.log(f"{device.name} {time:.2f} {sense}:{reading}")


# ---------------------- #
# Device Control Tools   #
# ---------------------- #

async def getSingle(device: ESPDevice) -> None:
    """Request a single data point from the device.

    Args:
        device (ESPDevice): The device to request data from.
    """
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


async def startStreaming(device: ESPDevice,
                   Hz: int) -> None:
    """Start streaming data from a device.

    Args:
        device (ESPDevice): The device to start streaming from.
        Hz (int): Frequency in Hz.

    """
    from libqretprop.protocol import StreamStartPacket

    if not Hz or Hz < 1 or Hz > 1000:
        ml.elog(f"Invalid frequency: {Hz}. Must be between 1-1000 Hz.")
        return

    ml.slog(f"startStreaming called for {device.name}, socket={device.socket}")

    if device.socket:
        try:
            packet = StreamStartPacket.create(frequency_hz=Hz)
            packed = packet.pack()
            ml.slog(f"Created STREAM_START packet: {len(packed)} bytes, data={packed.hex()[:40]}...")

            loop = asyncio.get_event_loop()
            ml.slog(f"About to send via sock_sendall...")
            await loop.sock_sendall(device.socket, packed)
            ml.slog(f"✓ ACTUALLY SENT STREAM_START ({Hz} Hz) to {device.name}")

        except Exception as e:
            ml.elog(f"Error sending STREAM_START command to {device.name}: {e}")
            import traceback
            ml.elog(traceback.format_exc())
            if device.address in deviceRegistry:
                _removed = deviceRegistry.pop(device.address)
                ml.slog(f"{device.name} removed from registry")
    else:
        ml.elog(f"No socket available for {device.name} to send STREAM_START command.")

async def stopStreaming(device: ESPDevice) -> None:
    """Stop streaming data from a device.

    Args:
        device (ESPDevice): The device to stop streaming from.
    """
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

async def setControl(device: SensorMonitor, controlName: str, controlState: str,) -> None:
    """Set the control state on a device.

    Args:
        device (SensorMonitor): The device to set the control state on.
        controlName (str): Name of the control (e.g., "AVFILL")
        controlState (str): State to set ("OPEN" or "CLOSE")
    """
    from libqretprop.protocol import ControlPacket, ControlState as CS

    controlName = controlName.upper()
    controlState = controlState.upper()

    if controlName not in device.controls:
        ml.elog(f"Invalid control name '{controlName}'. Valid: {list(device.controls.keys())}")
        return

    if controlState not in ["OPEN", "CLOSE"]:
        ml.elog(f"Invalid state '{controlState}'. Valid: OPEN, CLOSE")
        return

    # Get command ID (index in controls dict)
    control_names = list(device.controls.keys())
    command_id = control_names.index(controlName)

    # Convert to ControlState enum
    state = CS.OPEN if controlState == "OPEN" else CS.CLOSED

    if device.socket:
        try:
            packet = ControlPacket.create(command_id=command_id, command_state=state)
            loop = asyncio.get_event_loop()
            await loop.sock_sendall(device.socket, packet.pack())
            ml.slog(f"Sent CONTROL command (id={command_id}, {controlName} {controlState}) to {device.name}")

        except Exception as e:
            ml.elog(f"Error sending CONTROL command to {device.name}: {e}")
            if device.address in deviceRegistry:
                _removed = deviceRegistry.pop(device.address)
                ml.slog(f"{device.name} removed from registry.")

    else:
        ml.elog(f"No socket available for {device.name} to send CONTROL command.")

def getStatus(device: ESPDevice) -> None:
    command = "STATUS"
    device.socket.sendall(command.encode("utf-8"))
    ml.slog(f"Sent '{command}' command to {device.name}")

# ---------------------- #
# Data Export Tools      #
# ---------------------- #

def exportDataToCSV() -> None:
    """Export sensor data to a CSV file.

    Args:
        device (SensorMonitor): The device to export data from.
        filename (str): The name of the file to save the data to.
    """
    testTime = time.strftime("%Y%m%d-%H%M%S")

    # Export data for each SensorMonitor device to a separate CSV file
    for device in deviceRegistry.values():
        if isinstance(device, SensorMonitor):
            testTime = time.strftime("%Y%m%d-%H:%M:%S")
            deviceFilename = f"test_data/{device.name}_{testTime}.csv"

            sensorNames = [sensor.name for sensor in device.sensors.values()]

            with open(deviceFilename, mode='w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                header = ["Time", *sensorNames]
                writer.writerow(header)
                for i in range(len(device.times)):
                    row = [device.times[i]] + [sensor.data[i] for sensor in device.sensors.values()]
                    writer.writerow(row)

            ml.slog(f"Exported data to {deviceFilename} for device: {device.name}")


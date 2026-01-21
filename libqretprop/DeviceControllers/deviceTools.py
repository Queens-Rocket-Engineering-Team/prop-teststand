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
    """Monitor a single device for responses.

    This is called after a device is connected to continuously monitor the data it sends out.
    """
    loop = asyncio.get_event_loop()

    buffer = ""

    try:
        while True:
            data = await loop.sock_recv(device.socket, 1024)
            if not data:
                ml.elog(f"Device {device.name} disconnected.")
                break

            chunk = data.decode("utf-8", errors="ignore")
            buffer += chunk

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1) # Grab the first line and keep the rest in the buffer

                if (line.startswith(("STRM", "GETS"))) and isinstance(device, SensorMonitor):
                    publishSensorData(line[4:], device)  # Strip the "STRM" prefix

                elif line.startswith("CONTROL") and isinstance(device, SensorMonitor):
                    # Handle valve commands if applicable
                    ml.log(f"{device.name}: {line}")
                elif line.startswith("STATUS") and isinstance(device, SensorMonitor):
                    ml.log(f"{device.name}: {line}")

    except Exception as e:
        ml.elog(f"Error receiving response from {device.name}: {e}")
        # Remove the device from the device registry if there is an exception
        _removed = deviceRegistry.pop(device.name)
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

def getSingle(device: ESPDevice) -> None:
    """Request a single data point from the device.

    Args:
        device (ESPDevice): The device to request data from.
    """
    if device.socket:
        try:
            device.socket.sendall(b"GETS\n")
            ml.slog(f"Sent GETS command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending GETS command to {device.name}: {e}")
            _removed = deviceRegistry.pop(device.name)
            ml.slog(f"{_removed.name} removed from registry")
    else:
        ml.elog(f"No socket available for {device.name} to send GETS command.")


def startStreaming(device: ESPDevice,
                   Hz: int) -> None:
    """Start streaming data from a device.

    Args:
        device (ESPDevice): The device to start streaming from.
        args (list[str]): Optional arguments to include in the streaming command.

    """

    # Default command if no arguments are provided
    command = "STREAM\n"

    if Hz:
        command = f"STREAM {Hz}\n"
    else:
        ml.elog(f"Incorrect arguments provided for STREAM command: {Hz}. Expected a single numeric argument.")
        return

    if device.socket:
        try:
            device.socket.sendall(command.encode("utf-8"))
            ml.slog(f"Sent '{command}' command to {device.name}")

        except Exception as e:
            ml.elog(f"Error sending '{command}' command to {device.name}: {e}")
            _removed = deviceRegistry.pop(device.name)
            ml.slog(f"{device.name} removed from registry")
    else:
        ml.elog(f"No socket available for {device.name} to send STRM command.")

def stopStreaming(device: ESPDevice) -> None:
    """Stop streaming data from a device.

    Args:
        device (ESPDevice): The device to stop streaming from.
    """
    if device.socket:
        try:
            device.socket.sendall(b"STOP\n")
            ml.slog(f"Sent STOP command to {device.name}")
        except Exception as e:
            ml.elog(f"Error sending STOP command to {device.name}: {e}")
            _removed = deviceRegistry.pop(device.name)
            ml.slog(f"{device.name} removed from registry")
    else:
        ml.elog(f"No socket available for {device.name} to send STOP command.")

def setControl(device: SensorMonitor, controlName: str, controlState: str,) -> None:
    """Set the valve state on a device.

    Args:
        device (ESPDevice): The device to set the valve state on.
        args (list[str]): Arguments for the valve command, expected to be a single numeric value.
    """

    command = "BADCOMMAND\n" # Default fail state

    controlName = controlName.upper()
    controlState = controlState.upper()

    if controlName not in device.controls:
        ml.elog(f"Invalid control name. Valid names are {device.controls}")

    if controlState not in ["OPEN", "CLOSE"]:
        ml.elog(f"Invalid state. Valid states are {['OPEN', 'CLOSE']}")

    if device.socket:
        try:
            command = f"CONTROL {controlName} {controlState}\n"
            device.socket.sendall(command.encode("utf-8"))
            ml.slog(f"Sent '{command.strip()}' command to {device.name}")

        except Exception as e:
            ml.elog(f"Error sending {command} command to {device.name}: {e}")
            _removed = deviceRegistry.pop(device.name)
            ml.slog(f"{device.name} removed from registry.")

    else:
        ml.elog(f"No socket available for {device.name} to send {command} command.")

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

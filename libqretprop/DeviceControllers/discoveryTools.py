import asyncio
import socket
import struct

import libqretprop.mylogging as ml
from libqretprop.Devices.ESPDevice import ESPDevice


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

def _createSSDPSocket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    sock.setsockopt(socket.SOL_SOCKET,      # SOL_SOCKET is the socket level for options
                    socket.SO_REUSEADDR,    # SO_REUSEADDR allows the socket to be bound to an address that is already in use
                    1)                      # Set the option value to 1 (true)

    try:
        sock.bind(("", MULTICAST_PORT)) # Bind to all interfaces on the specified port. MAYBE SPECIFY INTERFACE
    except OSError as e:
        ml.elog(f"Error binding to port {MULTICAST_PORT}: {e}")


    membershipRequest = struct.pack(
        "4s4s",                                     # Pack the multicast address and interface address
        socket.inet_aton(MULTICAST_ADDRESS),    # inet_aton converts the IP address from string to binary format
        socket.inet_aton("0.0.0.0"))                # Bind to all for simplicity. WILL NEED TO CHANGE IF MULTIPLE INTERFACES ARE USED

    # Join the multicast group
    sock.setsockopt(socket.IPPROTO_IP,          # Specifies option is for IP protocol layer
                    socket.IP_ADD_MEMBERSHIP,   # Join the multicast group
                    membershipRequest)          # The packed membership request containing the multicast address and interface address

    # TTL Options
    sock.setsockopt(socket.IPPROTO_IP,       # IP protocol level
                    socket.IP_MULTICAST_TTL, # Set the time-to-live for multicast packets
                    2)                       # Set the TTL to 2, can jump through two routers (default is 1, which is local network only)


    # Set the socket to non-blocking mode
    sock.setblocking(False)

    ml.slog(f"SSDP Listener socket initialized on {MULTICAST_ADDRESS}:{MULTICAST_ADDRESS}")

    return sock

# ---------------------- #
# Listener Tools         #
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
                ml.slog(f"Valid ESP32 device discovered at {deviceIP}")

                # Check if device is already registered
                if deviceIP in deviceRegistry:
                    ml.slog(f"Device {deviceIP} is already registered as {deviceRegistry[deviceIP].name}.")
                    continue

                try:
                    # Create TCP connection to device
                    deviceSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    await loop.sock_connect(deviceSocket, (deviceIP, TCP_PORT))
                    deviceSocket.setblocking(False)

                    ml.slog(f"Established TCP connection to device at {deviceIP}:{TCP_PORT}")

                    # After initial connection a device should send its config file so that the server can configure
                    # the device and add it to the registry.
                    config = await loop.sock_recv(deviceSocket, 1024)  # Receive initial config data (if any)
                    if config[:4].decode("utf-8") != "CONF":
                        ml.elog("First message received from device was not a config file."
                                f"Expected 'CONF' prefix and got {config[:4].decode('utf-8', errors='ignore')}")

                    # ml.dlog(config.decode("utf-8", errors="ignore"))

                    # Create new ESPDevice instance and add to registry
                    newDevice = ESPDevice.fromConfigBytes(deviceSocket, deviceIP, config[4:])  # Skip the "CONF" prefix
                    deviceRegistry[deviceIP] = newDevice
                    ml.slog(f"Successfully connected to {deviceRegistry[deviceIP].name} at {deviceIP}")

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
        if device.tcpSocket:
            try:
                device.tcpSocket.close()
                ml.slog(f"Closed socket for device {device.name}")
            except OSError as e:
                ml.elog(f"Error closing socket for device {device.name}: {e}")

    deviceRegistry.clear()
    ml.slog("Closed all device sockets and cleared registry.")

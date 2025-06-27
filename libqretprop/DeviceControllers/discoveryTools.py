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
tcpSearchSocket  : socket.socket | None = None

# Listening Globals #
tcpListenerSocket: socket.socket | None = None
deviceRegistry   : dict[str, ESPDevice] = {}  # Registry to keep track of discovered devices

# ---------------------- #
# Active Searching Tools #
# ---------------------- #

def initSearchSockets() -> None:
    """Set the global SSDP and TCP sockets."""
    global ssdpSearchSocket, tcpSearchSocket
    ssdpSearchSocket = _createSSDPSocket()
    tcpSearchSocket  = _createTCPSocket()

def sendMulticastDiscovery() -> None:
    global ssdpSearchSocket

    if ssdpSearchSocket is None:
        raise RuntimeError("SSDP socket is not initialized. Call setSockets() first.")

    ml.dlog("Sending SSDP multicast discovery request.")

    ssdpRequest = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {MULTICAST_ADDRESS}:{MULTICAST_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"                         # Maximum wait time in seconds
        "ST: urn:qret-device:esp32\r\n"     # Search target - custom for your devices
        "USER-AGENT: QRET/1.0\r\n"          # Identify your application
        "\r\n"
    )

    ssdpSearchSocket.setsockopt(socket.IPPROTO_IP,       # IP protocol level
                        socket.IP_MULTICAST_TTL, # Set the time-to-live for multicast packets
                        2)                       # Set the TTL to 2, can jump through two routers (default is 1, which is local network only)
    ssdpSearchSocket.sendto(ssdpRequest.encode(), (MULTICAST_ADDRESS, MULTICAST_PORT))

async def continuousMulticastDiscovery() -> None:
    """Send out a search request every 5 seconds."""
    while True:
        sendMulticastDiscovery()
        await asyncio.sleep(5)  # Wait for 5 seconds before sending the next request

def directDiscovery(address: str) -> None:
    """Directly search for a device at the specified address over TCP."""
    global tcpSearchSocket
    if tcpSearchSocket is None:
        raise RuntimeError("TCP socket is not initialized. Call setSockets() first.")

    try:
        tcpSearchSocket.connect((address, TCP_PORT))
        ml.dlog(f"Successfully sent discovery to {address}:{TCP_PORT}")

        # Close and recreate a new socket for future use. This is to ensure that the socket is fresh for the next connection.
        tcpSearchSocket.close()
        tcpSearchSocket = _createTCPSocket()

    except OSError as e:
        ml.elog(f"Error connecting to device at {address}:{TCP_PORT}: {e}")

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

    ml.slog(f"SSDP Listener socket initialized on {MULTICAST_ADDRESS}:{MULTICAST_ADDRESS}")

    return sock

def _createTCPSocket() -> socket.socket:
    """Create a TCP socket for direct device communication."""
    tcpSock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcpSock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcpSock.settimeout(0.5)  # Set a short timeout for non-blocking behavior
    return tcpSock

# ---------------------- #
# Listener Tools         #
# ---------------------- #

def initListening() -> None:
    """Initialize the TCP listener socket."""
    global tcpListenerSocket

    if tcpListenerSocket is not None:
        raise RuntimeError("TCP listener socket is already initialized.")

    tcpListenerSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcpListenerSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcpListenerSocket.setblocking(False)  # Set the socket to non-blocking mode for asyncio

    try:
        tcpListenerSocket.bind(("0.0.0.0", TCP_PORT))  # Bind to all interfaces on the specified port
        tcpListenerSocket.listen(5)               # Start listening for incoming connections
        ml.slog(f"TCP Listener socket initialized on port {TCP_PORT}")
    except OSError as e:
        ml.elog(f"Error binding TCP listener socket to port {TCP_PORT}: {e}")
        tcpListenerSocket = None

async def listenForDevices() -> None:
    """Daemon that accepts incoming TCP connections from ESP32 devices and registers them to the known devices list."""
    global tcpListenerSocket

    if tcpListenerSocket is None:
        raise RuntimeError("TCP listener socket is not initialized. Call initListenerSocket() first.")

    ml.slog("Listening for incoming TCP connections from devices...")
    ml.slog("Starting TCP listener daemon.")

    loop = asyncio.get_event_loop()

    try:
        while True:
            clientSocket, clientAddress = await loop.sock_accept(tcpListenerSocket)
            ml.slog(f"Accepted connection from {clientAddress[0]}")

            try:
                # Blocking receive call since we expect the device to send its configuration immediately after connecting
                configBytes = clientSocket.recv(1024)

                device = ESPDevice.fromConfigBytes(clientSocket, clientAddress[0], configBytes)

                if device.name is not None:
                    if device.name in deviceRegistry:
                        ml.slog(f"Device {device.name} already registered. Overwriting existing entry.")
                    else:
                        ml.slog(f"Registering new device: {device.name}")

                    deviceRegistry[device.name] = device
                else:
                    ml.elog(f"Received device with no name from {clientAddress[0]}. Device not registered.")
            finally:
                clientSocket.close()

    except asyncio.CancelledError:
        ml.slog("listenForDevices cancelled, shutting down listener.")
        raise  # Re-raise to ensure proper task cancellation
    except OSError as e:
        ml.elog(f"Error listening for devices: {e}")

def getRegisteredDevices() -> dict[str, ESPDevice]:
    return deviceRegistry.copy()

def getDeviceByName(name: str) -> ESPDevice | None:
    return deviceRegistry.get(name)

# ---------------------- #
# Socket Management      #
# ---------------------- #

def closeSearchSockets() -> None:
    """Close the SSDP and TCP sockets."""
    global ssdpSearchSocket, tcpSearchSocket
    if ssdpSearchSocket:
        ssdpSearchSocket.close()
        ssdpSearchSocket = None
    if tcpSearchSocket:
        tcpSearchSocket.close()
        tcpSearchSocket = None
    ml.slog("Closed SSDP and TCP sockets.")

def closeDeviceSockets() -> None:
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

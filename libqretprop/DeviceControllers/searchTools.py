import asyncio
import socket
import struct

import libqretprop.mylogging as ml


MULTICAST_ADDRESS = "239.255.255.250"
MULTICAST_PORT = 1900

TCP_PORT = 50000  # Default TCP port for direct device communication

ssdpSocket : socket.socket | None = None
tcpSocket  : socket.socket | None = None

def initSockets() -> None:
    """Set the global SSDP and TCP sockets."""
    global ssdpSocket, tcpSocket
    ssdpSocket = _createSSDPSocket()
    tcpSocket  = _createTCPSocket()

def sendMulticastDiscovery() -> None:
    global ssdpSocket

    if ssdpSocket is None:
        raise RuntimeError("SSDP socket is not initialized. Call setSockets() first.")

    ml.slog("Sending SSDP multicast discovery request.")

    ssdpRequest = "M-SEARCH"
    ssdpSocket.setsockopt(socket.IPPROTO_IP,       # IP protocol level
                        socket.IP_MULTICAST_TTL, # Set the time-to-live for multicast packets
                        2)                       # Set the TTL to 2, can jump through two routers (default is 1, which is local network only)
    ssdpSocket.sendto(ssdpRequest.encode(), (MULTICAST_ADDRESS, MULTICAST_PORT))

async def continuousMulticastDiscovery() -> None:
    """Send out a search request every 5 seconds."""
    while True:
        sendMulticastDiscovery()
        await asyncio.sleep(5)  # Wait for 5 seconds before sending the next request


def directDiscovery(address: str) -> None:
    """Directly search for a device at the specified address over TCP."""
    global tcpSocket
    if tcpSocket is None:
        raise RuntimeError("TCP socket is not initialized. Call setSockets() first.")

    try:
        tcpSocket.connect((address, TCP_PORT))
        ml.slog(f"Found device at {address}:{TCP_PORT}")
        tcpSocket.close()  # Close the socket after connection

        # Now recreate the TCP socket for future use
        tcpSocket = _createTCPSocket()

    except OSError as e:
        ml.elog(f"Error connecting to device at {address}:{TCP_PORT}: {e}")

def closeSockets() -> None:
    """Close the SSDP and TCP sockets."""
    global ssdpSocket, tcpSocket
    if ssdpSocket:
        ssdpSocket.close()
        ssdpSocket = None
    if tcpSocket:
        tcpSocket.close()
        tcpSocket = None
    ml.slog("Closed SSDP and TCP sockets.")


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
    return tcpSock

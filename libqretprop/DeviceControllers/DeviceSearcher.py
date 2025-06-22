import asyncio
import socket
import struct

import libqretprop.mylogging as ml


MULTICAST_ADDRESS = "239.255.255.250"
MULTICAST_PORT = 1900

TCP_PORT = 50000  # Default TCP port for direct device communication

class DeviceSearcher:
    """A class to search for ESP32 devices using SSDP (Simple Service Discovery Protocol).

    This class purely exists to abstract away the creation and management of the SSDP search socket. All it does is send
    search messages. Incoming connections come in on a TCP socket and not over SSDP.

    Parameters
    ----------
    multicastAddress : str
        The multicast address to listen for SSDP responses.
    port : int
        The port to listen for SSDP responses. Default is 1900, which is the standard port for SSDP.

    Attributes
    ----------
    multicastAddress : str
        The multicast address to listen for SSDP responses.
    port : int
        The port to listen for SSDP responses.
    SSDPSock : socket.socket
        The socket used for listening to SSDP responses.

    """

    def __init__(self) -> None:
        self.multicastAddress   = MULTICAST_ADDRESS
        self.multicastPort      = MULTICAST_PORT

        self.SSDPSock   = self._createSSDPSocket()  # Create the SSDP socket
        self.tcpSock    = self._createTCPSocket()  # Create a TCP socket for direct device communication


    def sendMulticastDiscovery(self) -> None:
        ssdpRequest = "M-SEARCH"
        self.SSDPSock.setsockopt(socket.IPPROTO_IP,       # IP protocol level
                                 socket.IP_MULTICAST_TTL, # Set the time-to-live for multicast packets
                                 2)                       # Set the TTL to 2, can jump through two routers (default is 1, which is local network only)
        self.SSDPSock.sendto(ssdpRequest.encode(), (self.multicastAddress, self.multicastPort))

    async def continuousMulticastDiscovery(self) -> None:
        """Send out a search request every 5 seconds."""
        while True:
            self.sendMulticastDiscovery()
            await asyncio.sleep(5)  # Wait for 5 seconds before sending the next request


    def directDiscovery(self, address: str) -> None:
        """Directly search for a device at the specified address over TCP."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((address, TCP_PORT))
                ml.slog(f"Found device at {address}:{TCP_PORT}")
        except OSError as e:
            ml.elog(f"Error connecting to device at {address}:{TCP_PORT}: {e}")


    def closeSocket(self) -> None:
        """Stop the SSDP searcher."""
        self.SSDPSock.close()
        ml.slog("SSDP Listener stopped.")


    def _createSSDPSocket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

        sock.setsockopt(socket.SOL_SOCKET,      # SOL_SOCKET is the socket level for options
                        socket.SO_REUSEADDR,    # SO_REUSEADDR allows the socket to be bound to an address that is already in use
                        1)                      # Set the option value to 1 (true)

        try:
            sock.bind(("", self.multicastPort)) # Bind to all interfaces on the specified port. MAYBE SPECIFY INTERFACE
        except OSError as e:
            ml.elog(f"Error binding to port {self.multicastPort}: {e}")


        membershipRequest = struct.pack(
            "4s4s",                                     # Pack the multicast address and interface address
            socket.inet_aton(self.multicastAddress),    # inet_aton converts the IP address from string to binary format
            socket.inet_aton("0.0.0.0"))                # Bind to all for simplicity. WILL NEED TO CHANGE IF MULTIPLE INTERFACES ARE USED

        # Join the multicast group
        sock.setsockopt(socket.IPPROTO_IP,          # Specifies option is for IP protocol layer
                        socket.IP_ADD_MEMBERSHIP,   # Join the multicast group
                        membershipRequest)          # The packed membership request containing the multicast address and interface address

        ml.slog(f"SSDP Listener socket initialized on {self.multicastAddress}:{self.multicastPort}")

        return sock

    def _createTCPSocket(self) -> socket.socket:
        """Create a TCP socket for direct device communication."""
        tcpSock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcpSock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return tcpSock

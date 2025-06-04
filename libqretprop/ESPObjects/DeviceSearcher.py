import socket
import struct
from socket import inet_aton

from libqretprop.ESPObjects.ESPDevice.ESPDevice import ESPDevice


class DeviceSearcher:
    """A class to search for ESP32 devices using SSDP (Simple Service Discovery Protocol).

    This class listens for SSDP responses from ESP32 devices and creates ESPDevice objects from the received
    configurations. This class is designed to be used within a select type loop, where it can listen for SSDP responses
    and handle them asynchronously.

    Parameters
    ----------
    multicastAddress : str
        The multicast address to listen for SSDP responses.
    port : int
        The port to listen for SSDP responses. Default is 1900, which is the standard port for SSDP.

    Attributes
    ----------
    deviceList : set[str]
        A set of discovered device names to avoid duplicates.
    multicastAddress : str
        The multicast address to listen for SSDP responses.
    port : int
        The port to listen for SSDP responses.
    SSDPSock : socket.socket
        The socket used for listening to SSDP responses.
    localIP : str
        The local IP address of the machine running this script, used for debugging purposes.


    """
    def __init__(self, multicastAddress: str = "239.255.255.250", port: int = 1900) -> None:
        self.deviceList: set[str] = set()
        self.multicastAddress = multicastAddress
        self.port = port

        self.SSDPSock = self._createSSDPSocket()  # Create the SSDP socket

        # Get the local IP address by connecting to a public IP (does not send data)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(("8.8.8.8", 80))
                self.localIP = s.getsockname()[0]
            except Exception:
                self.localIP = "127.0.0.1"
        print(f"Local IP: {self.localIP}")


    def sendDiscovery(self) -> None:
        ssdpRequest = "M-SEARCH"
        self.SSDPSock.setsockopt(socket.IPPROTO_IP,       # IP protocol level
                                 socket.IP_MULTICAST_TTL, # Set the time-to-live for multicast packets
                                 2)                       # Set the TTL to 2, can jump through two routers (default is 1, which is local network only)
        self.SSDPSock.sendto(ssdpRequest.encode(), (self.multicastAddress, self.port))

    def handleDeviceCallback(self) -> ESPDevice | None:
        """Generate an ESPDevice object from the SSDP response.

        This method should only be called while awaiting for a response from the SSDP socket. Calling it directly will
        block until a response is received.

        Returns
        -------
        ESPDevice | None
            An ESPDevice object if a valid configuration is received, otherwise None.

        """
        data, addr = self.SSDPSock.recvfrom(1024)
        messageType = data[0:4].decode("utf-8", errors="ignore") # First 4 bytes are the message type

        if messageType == "CONF":
            print(f"Received configuration from {addr[0]}")
            device = ESPDevice.fromConfigBytes(data[4:], addr[0]) # Ignore the first 4 bytes which are the message type

            if device.name not in self.deviceList:
                self.deviceList.add(device.name)
                print(f"Discovered new device: {device.name} at {addr[0]}")
                return device
            else:
                print(f"Device {device.name} already discovered.")
                return None

        print(f"Received non-CONF message: {data.decode('utf-8', errors='ignore')} from {addr[0]}")
        return None  # If the message type is not CONF, return None

    def stopListening(self) -> None:
        """Stop the SSDP listener."""
        self.SSDPSock.close()
        print("SSDP Listener stopped.")


    def _createSSDPSocket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

        sock.setsockopt(socket.SOL_SOCKET,      # SOL_SOCKET is the socket level for options
                        socket.SO_REUSEADDR,    # SO_REUSEADDR allows the socket to be bound to an address that is already in use
                        1)                      # Set the option value to 1 (true)

        try:
            sock.bind(("", self.port)) # Bind to all interfaces on the specified port. MAYBE SPECIFY INTERFACE
        except OSError as e:
            print(f"Error binding to port {self.port}: {e}")


        membershipRequest = struct.pack(
            "4s4s",                                     # Pack the multicast address and interface address
            socket.inet_aton(self.multicastAddress),    # inet_aton converts the IP address from string to binary format
            socket.inet_aton("0.0.0.0"))                # Bind to all for simplicity. WILL NEED TO CHANGE IF MULTIPLE INTERFACES ARE USED

        # Join the multicast group
        sock.setsockopt(socket.IPPROTO_IP,          # Specifies option is for IP protocol layer
                        socket.IP_ADD_MEMBERSHIP,   # Join the multicast group
                        membershipRequest)          # The packed membership request containing the multicast address and interface address

        print(f"SSDP Listener socket initialized on {self.multicastAddress}:{self.port}")

        return sock

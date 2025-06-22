import socket

class CommunicationManager:
    """Manages communication with devices.

    This class is responsible for handling the communication protocols
    and managing the connections to various devices.
    """

    def __init__(self) -> None:
        self.connections : dict[str, socket.socket]= {} # Maps a device identifier to its socket connection.

        self.networkInterface : str = "0.0.0.0" # Default to binding to all interfaces
        self.tcpListeningPort : int = 50000          # Default TCP port for communication

    def listenForTCPDevices(self) -> None:
        """Listen for incoming TCP connections from devices.

        Parameters
        ----------
        host : str
            The host address to listen on.
        port : int
            The port to listen on.

        """

        # TCP listening socket setup. Devices will come through this socket to make themselves known.
        server_socket = socket.socket(socket.AF_INET,       # Address family: IPv4
                                      socket.SOCK_STREAM)   # Socket type: TCP
        server_socket.bind((self.networkInterface, self.tcpListeningPort))
        server_socket.listen()



import errno # need to import errno for OSError handling
import socket  # noqa: INP001 -- Implicit namespace doesn't matter for ESP32 filesystem


class TCPHandler:
    """A class to listen for incoming TCP messages on a specified port.

    Attributes
    ----------
        port (int): The port to listen on.
        tcpSocket (socket.socket): The socket object that is currently open for a new connection

    Methods
    -------
        handleMessage(clientSocket: socket.socket) -> None:
            Handles incoming TCP messages. This method should be overridden by the user to handle
            incoming messages.

        getSocket() -> socket.socket:
            Returns the socket object for the listener.

    Usage
    -----
    The TCPListener class handles all TCP requests, connection or command. There is only ever one
    socket available to connect to (the listener socket). When a connection is made, a new socket is
    created and assigned to be the new listener socket. This allows for multiple connections to be
    made to the ESP32 over TCP. The TCPHandler can process any incoming message to a socket

    """
    def __init__(self, port: int) -> None:
        self.port = port
        self.tcpSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # Allow rebinding to the same port
        self.tcpSocket.bind(("", self.port))
        self.tcpSocket.listen(1)  # Allow only 1 connection backlog
        print(f"TCP Listener initialized on port {self.port}")

    def handleMessage(self, clientSocket: socket.socket, clientAddress: str) -> bool:
        """Handle incoming TCP messages. Return False if there is an error handling data."""

        try:
            data = clientSocket.recv(1024).decode("utf-8")
            if not data:
                print("Connection closed by client")
                return False

            # Add TCP message handling logic here

            print(f"Received TCP message: {data}")

        except OSError as e: # Handle any OSErrors. Micropython is different than Cpython and error codes need to be imported
            if e.args[0] == errno.ECONNRESET:  # Handle connection reset error
                print(f"Client {clientAddress} closed the connection: ECONNRESET")
            else:
                print(f"Unexpected OSError from {clientAddress}: {e}")
            return False

        except Exception as e:
            print(f"Error handling TCP client: {e}")
            return False

        return True


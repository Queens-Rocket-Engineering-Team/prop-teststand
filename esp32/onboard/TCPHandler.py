import socket  # noqa: INP001 -- Implicit namespace doesn't matter here


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

    def handleMessage(self, clientSocket: socket.socket) -> None:
        try:
            data = clientSocket.recv(1024).decode("utf-8")
            if not data:
                clientSocket.close()
                return
            # Add TCP message handling logic here
            print(f"Received TCP message: {data}")
        except Exception as e:
            print(f"Error handling TCP client: {e}")
            clientSocket.close()

    def getSocket(self) -> socket.socket:
        return self.tcpSocket

import select  # noqa: INP001 -- Implicit namespace doesn't matter here
import socket  # noqa: TCH003 -- Typing not a library within micropython, cant put into a typed block

from TCPHandler import TCPHandler
from UDPListener import UDPListener


class AsyncManager:
    def __init__(self, udpListener: UDPListener, tcpListener: TCPHandler) -> None:
        self.udpListener = udpListener
        self.tcpListener = tcpListener
        self.running = False


        # Generating list of sockets to pass to select
        self.inputs: list[socket.socket] = [self.udpListener.getSocket(), self.tcpListener.getSocket()]

    def tcpPortGenerator(self): # type:ignore # noqa: ANN201 # Generator not default type and not included in micropython
        # Generates a new port number for the TCP server in sequence starting at the first listener port.
        port = self.tcpListener.port
        while True:
            port += 1
            yield port

    def createNewTCPServer(self) -> None:
        newPort = next(self.tcpPortGenerator()) # type:ignore # Generator typing not in micropython
        newServer = TCPHandler(port=newPort)
        self.inputs.append(newServer.getSocket()) # Add the new server to the list of inputs
        self.tcpListener = newServer

    def run(self) -> None:
        print("Server is running...")
        self.running = True
        try:
            while self.running:
                # Monitor sockets for if they become readable
                readable, _, _ = select.select(self.inputs, [], [])
                for sock in readable:
                    if sock == self.udpListener.getSocket():
                        data, address = sock.recvfrom(1024)
                        print(f"Received UDP message: {data.decode('utf-8')} from {address}")
                        # Handle all UDP messages in the UDPListener. CreateNewServer is for if a
                        # SEARCH message is received
                        self.udpListener.handleMessage(data, address, self.tcpListener.port, self.createNewTCPServer) # Pass data to the listener


                    elif sock == self.tcpListener.getSocket():
                        clientSocket, clientAddress = sock.accept()
                        print(f"New TCP connection from {clientAddress}")
                        self.inputs.append(clientSocket)
                    else:
                        self.tcpListener.handleMessage(sock)
                        self.inputs.remove(sock)
        except KeyboardInterrupt:
            if self.running:
                print("\nStopping Server...")
                self.stop()
            else: # If the server is already stopped
                print("Server already stopped.")

    def stop(self) -> None:
        print("Cleaning up sockets...")
        for sock in self.inputs:
            try:
                sock.close()
            except Exception as e:
                print(f"Error closing socket: {e}")
        print("Server stopped.")

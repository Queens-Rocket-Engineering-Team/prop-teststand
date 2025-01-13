import socket
import threading
import time


class DeviceSearcher:
    def __init__(self, broadcast_ip:str ="255.255.255.255", port:int=40000) -> None:
        self.deviceList: set[str] = set()  # List to store the IP addresses of devices
        self.stopListening_flag = False  # Flag to control the listening thread
        self.broadcast_ip = broadcast_ip
        self.port = port

        self.localIP = socket.gethostbyname(socket.gethostname())
        print(f"Local IP: {self.localIP}")

    def sendBroadcastMessage(self, message: str) -> None:
        # Create a UDP socket. AF_INET is IPV4, SOCK_DGRAM is UDP
        broadcastSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # SOL_SOCKET lets us change socket layer settings, SO_BROADCAST allows us to send broadcast
        # messages. The 1 is the value to set the option to.
        broadcastSock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # Broadcast the message
        print(f"Sending broadcast message to {self.broadcast_ip}:{self.port}")
        broadcastSock.sendto(message.encode(), (self.broadcast_ip, self.port))

        broadcastSock.close()

    def listenForDevices(self, sock: socket.socket) -> None:

        # Ensure the flag is set to False when the thread starts
        self.stopListening_flag = False

        print(f"Listening for devices on {sock.getsockname()[0]}:{sock.getsockname()[1]}")
        while not self.stopListening_flag:
            try:
                data, addr = sock.recvfrom(1024)  # Buffer size is 1024 bytes
                if addr[0] == self.localIP:
                    pass  # Ignore messages from this device
                elif data.decode() == "ACK":
                    print(f"Received ACK from {addr[0]}")
                    self.deviceList.add(addr[0]) # Store IP address of any device that responded
                else:
                    print(f"Received unknown response from {addr[0]}: {data.decode()}")
            except TimeoutError:
                # The timeout is only included to stop the program from hanging up on the recvfrom
                # call we don't actually care if it times out
                continue
            except Exception as e:
                if not self.stopListening_flag:
                    print(f"Error receiving data: {e}")

        print("Listener thread exiting...")

    def searchForDevices(self) -> None:
        # Create a socket to listen for responses
        self.listenSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listenSock.bind(("", self.port))  # Bind to all NICs on the specified port.
        self.listenSock.settimeout(0.5)  # Set a timeout for the socket to prevent blocking

        # Start a thread to listen for responses
        self.listenerThread = threading.Thread(target=self.listenForDevices, args=(self.listenSock,))
        #self.listenerThread.daemon = True  # Makes it so the Daemon will close when the main program exits
        self.listenerThread.start()

        # Send the broadcast message to discover devices. This comes after the listener thread is
        # started so the SEARCH message will appear in the listener thread.
        self.sendBroadcastMessage("SEARCH")

    def stopListening(self) -> None:
        self.stopListening_flag = True
        self.listenerThread.join()
        self.listenSock.close()


if __name__ == "main":
    searcher = DeviceSearcher()
    searcher.searchForDevices()
    time.sleep(5)  # Wait for devices to respond
    searcher.stopListening()
    print(f"Found devices: {searcher.deviceList}")
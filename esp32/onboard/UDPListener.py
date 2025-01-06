import socket  # noqa: INP001 # This is a micropython library

import network
import uasyncio as asyncio  # noqa: I001 # This is a micropython library
import ubinascii


class UDPSearcher:
    def __init__(self, port:int=40000) -> None:
        self.deviceList = []  # List to store the IP addresses of devices
        self.stop_flag = False  # Flag to control the listening loop
        self.port = port    

    async def listenForDevices(self, sock: socket.socket) -> None:
        print(f"Listening for devices on {sock.getsockname()[0]}:{sock.getsockname()[1]}")
        while not self.stop_flag:
            try:
                # Set a timeout for the recvfrom() call to prevent indefinite blocking
                sock.settimeout(1.0)  # 1-second timeout
                data, addr = await asyncio.to_thread(sock.recvfrom, 1024)  # Non-blocking recvfrom
                if data.decode() == "SEARCH":
                    print(f"Received SEARCH from {addr[0]}")
                    await self.sendACK(addr)
                else:
                    print(f"Received unknown response from {addr[0]}: {data.decode()}")
            except Exception as e:
                if not self.stop_flag:
                    print(f"Error receiving data: {e}")

        print("Listener thread exiting...")

    async def sendACK(self, addr: tuple) -> None:
        # Get the ESP32's IP and MAC address
        ip_address = self.get_ip_address()
        mac_address = self.get_mac_address()

        ack_message = f"ACK: IP={ip_address}, MAC={mac_address}"

        # Send the ACK message to the requesting device
        broadcastSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        broadcastSock.sendto(ack_message.encode(), addr)
        broadcastSock.close()

        print(f"Sent ACK to {addr[0]} with IP={ip_address} and MAC={mac_address}")

    def get_ip_address(self) -> str:
        # Assuming ESP32 is connected to a Wi-Fi network
        wlan = network.WLAN(network.STA_IF)
        return wlan.ifconfig()[0]  # Get the IP address of the ESP32

    def get_mac_address(self) -> str:
        # Get the MAC address of the ESP32
        wlan = network.WLAN(network.STA_IF)
        mac = wlan.config('mac')
        return ubinascii.hexlify(mac, ':').decode()

    async def searchForDevices(self) -> None:
        # Create a socket to listen for responses
        listenSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listenSock.bind(("", self.port))  # Bind to all NICs on the specified port.

        # Start a task to listen for responses
        asyncio.create_task(self.listenForDevices(listenSock))

        # Wait until the user presses the stop key for the program
        try:
            while True:
                await asyncio.sleep(1)  # Non-blocking sleep to keep the event loop running
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_flag = True  # Signal the listener to stop
            listenSock.close()  # Close the listening socket
            print("Stopping search for devices...")

# Main function to start the search
async def main():
    searcher = UDPSearcher()
    await searcher.searchForDevices()

# Start the asyncio event loop
asyncio.run(main())

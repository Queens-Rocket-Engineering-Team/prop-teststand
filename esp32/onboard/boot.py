# BASE MICROPYTHON BOOT.PY-----------------------------------------------|  # noqa: INP001
# # This is all micropython code to be executed on the esp32 system level and doesn't require a __init__.py file

# This file is executed on every boot (including wake-boot from deep sleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()
#------------------------------------------------------------------------|

import socket

import wifi_tools as wt


def listen_for_search(UDPPort:int = 40000) -> str:
    """Listen for an incoming search message and respond with an ACK message.

    This function generates a UDP socket and then listens incoming SEARCH messages from another
    device on the network, likely the control server. The default port for broadcasting on the server
    side is 40000.
    """

    # Create the UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Bind the socket to a specific port (e.g., 12345)
    sock.bind(("", UDPPort))

    print(f"Listening for search message on port {UDPPort}")

    while True:
        # Receive a message (max 1024 bytes)
        message, addr = sock.recvfrom(1024)
        print("Received message:", message.decode(), "from", addr)

        # Check if the message matches the search message
        if message.decode() == "SEARCH":
            print(f"SEARCH received from {addr}. Sending ACK.")

            # Get the ESP32's IP and MAC address
            # ip_address = socket.getaddrinfo('esp32', 1)[0][-1][0]
            # mac_address = ubinascii.hexlify(network.WLAN().config('mac'),':').decode()

            # Construct the ACK message with IP and MAC address
            ack_message = "ACK"

            # Send the ACK response back to the sender
            sock.sendto(ack_message.encode(), (addr[0], UDPPort))
            print("Sent Message:", ack_message)


TCPRequests = ("SEARCH", # Message received when server is searching for client sensors
               "SREAD", # Reads a single value from all sensors
               "CREAD", # Continuously reads data from all sensors until STOP received
               "STOP", # Stops continuous reading
               "STAT", # Returns number of sensors and types
               )

wlan = wt.connectWifi("Nolito", "6138201079")




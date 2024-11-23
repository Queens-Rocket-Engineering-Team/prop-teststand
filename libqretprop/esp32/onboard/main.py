import socket  # noqa: I001, INP001 # Ignore no namespace error and library not found errors

import wifi_tools as wt

global wlan

def hostTCPSocket (ipAddress: str, port: int = 8080) -> socket.socket:
    """Host a TCP socket on the specified IP address and port number.

    The default port number is 8080, and this function will return the server socket object. This function handles nothing
    but the creation of the socket object and the binding to the specified IP address and port number.
    """


    # Create a socket object
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # AF_INET is the address family for IPv4, SOCK_STREAM is the socket type for TCP
    server_socket.bind((ipAddress, port))
    server_socket.listen(1) # Allow only one connection at a time MAYBE CHANGE LATER

    print(f"Server running on {ipAddress}:{port}")

    return server_socket

def main() -> None:
    try:
        wlan = wt.connectWifi("Hous-fi", "nothomeless")
        ipAddress = wlan.ifconfig()[0] # Extract the IP address from the network configuration

        server_socket = hostTCPSocket(ipAddress) # Generate the server socket default port it 8080

        while True: # Keep server running and trying to accept connections
            client_socket, client_addr = server_socket.accept() # Wait for a connection to be made
            print(f"Connection from {client_addr}")

            while True:
                try:
                    # Receiving whatever was sent in 1024 byte chunks. Make sure the server and client agree on packet size
                    receivedMessage = client_socket.recv(1024).decode("utf-8")  # Python defaults to utf-8 encoding but this is nice and explicit
                    if not receivedMessage:
                        break  # If no message is received, break the loop
                    print(f"Received: {receivedMessage}")

                    # Sending a response
                    responseMessage = f"Hello from ESP32! You sent me this: {receivedMessage}"
                    client_socket.send(responseMessage.encode("utf-8"))  # Python defaults to utf-8 encoding but this is nice and explicit
                except Exception as e: 
                    print(f"An error occurred while receiving/sending data: {e}")
                    break

            client_socket.close()

        wt.disconnectWifi(wlan)

    except wt.WiFiTimeoutError as e:
        print(f"Failed to connect to Wi-Fi: {e}")

    except Exception as e:
        print(f"An error occurred: {e}")

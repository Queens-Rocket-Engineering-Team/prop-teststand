import wifi_tools as wt  # noqa: INP001 # This is a micropython library


global wlan

def main() -> None:
    try:
        wlan = wt.connectWifi("Nolito", "6138201079")
        ipAddress = wlan.ifconfig()[0] # Extract the IP address from the network configuration

        server_socket = wt.hostTCPSocket(ipAddress) # Generate the server socket default port it 8080

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

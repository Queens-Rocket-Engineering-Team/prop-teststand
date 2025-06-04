import socket


# Configure the desktop as a TCP client
ESP32_HOST_HOME = "192.168.2.43"  # ESP32's IP address (fixed at Noah's house)
ESP32_HOST_GRANDMAS = "192.168.86.223"  # ESP32's IP address (fixed at Noah's house)

PORT = 8080

# Create a socket
client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.connect((ESP32_HOST_GRANDMAS, PORT))

try:
    while True:
        message = input("Enter a message to send: ")
        if message.lower() == "exit":
            break
        client_socket.send(message.encode("utf-8"))  # Send the message to the server. Python uses utf-8 encoding by default

        # Receive response from ESP32
        data = client_socket.recv(1024)  # Make sure this is the same buffer size as the server. Probably just leave at 1024 bytes.
        print("From ESP32:", data.decode("utf-8")) # Python uses utf-8 encoding by default
finally:
    client_socket.close()

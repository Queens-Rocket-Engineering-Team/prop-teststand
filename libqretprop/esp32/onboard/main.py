import socket  # noqa: I001, INP001 # Ignore no namespace error and library not found errors
import network  # type:ignore # This is a micropython library

import wifi_tools as wt


def start_web_server(wlan: network.wlan) -> None:
    serverAddress = "0.0.0.0"

    # Create a socket object
    addr = socket.getaddrinfo(serverAddress, 80)[0][-1] # Checks the address info for the server (port 80 in this case)
    server_socket = socket.socket()
    server_socket.bind(addr)
    server_socket.listen(5)

    print(f"Web server running on http://{wlan.ifconfig()[0]}:80")

    while True:
        # Accept incoming connections
        client_socket, client_addr = server_socket.accept()
        print(f"Connection from {client_addr}")

        # Receive the request
        request = client_socket.recv(1024)
        request_str = request.decode()
        print("Request:", request_str)

        # Check if the request is for the image
        if "GET /FLARE.png" in request_str:
            try:
                with open("FLARE.png", 'rb') as img:
                    image_data = img.read()
                # Send HTTP response headers for the PNG image
                client_socket.send(b"HTTP/1.1 200 OK\r\n")
                client_socket.send(b"Content-Type: image/png\r\n")
                client_socket.send(b"Content-Length: " + str(len(image_data)).encode() + b"\r\n")
                client_socket.send(b"\r\n")
                # Send the image content
                client_socket.sendall(image_data) # Sendall method is required instead of "send" method to make sure image gets sent
            except Exception as e:
                print("Error serving image:", e)
        else:
            # Prepare an HTTP response with HTML content
            response = b"""\
HTTP/1.1 200 OK
Content-Type: text/html

<!DOCTYPE html>
<html>
  <head><title>ESP32 Web Server</title></head>
  <body>
    <h1>FLARE is running!</h1>
    <img src="/FLARE.png" alt="FLARE Image" style="max-width:100%;height:auto;">
  </body>
</html>
"""
            # Send the response
            client_socket.send(response)

        client_socket.close()


def main() -> None:

    try:
        wlan = wt.connectWifi("Hous-fi", "nothomeless")
        ipAddress = wlan.ifconfig()[0]

        start_web_server(wlan)
        wt.disconnectWifi(wlan)
    except Exception as e:
        print(e)


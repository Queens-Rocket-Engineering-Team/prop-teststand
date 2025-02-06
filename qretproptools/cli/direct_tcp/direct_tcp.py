import select
import socket
import sys

from libqretprop.esp32interface.ESPDevice.ESPDevice import ESPDevice


def main() -> None:

    if len(sys.argv) != 3: # Ensure the correct number of arguments are passed
        print("Usage: direct_tcp <IP> <PORT>")
        sys.exit(1)

    ip_address = sys.argv[1]
    try:
        port_number = int(sys.argv[2]) # Force the port number to be an integer
    except ValueError:
        print("Error: Port must be an integer.")
        sys.exit(1)

    running = True

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # TCP socket parameters
        sock.connect((ip_address, port_number))
        print(f"Connected to {ip_address}:{port_number}")

        while running:
            readable, _ , _ = select.select([sock], [], [], 0.1) # Check if the socket is readable. Select is blocking so we need a timeout to allow for Ctrl+C

            for sock in readable:
                data = sock.recv(1024) # Read the data from the socket
                messageType = data.decode("utf-8")[0:4] # Get the first 4 characters of the data

                print(messageType)

                if not data:
                    print("Connection closed by server")
                    sock.close()
                    sys.exit(1)

                if messageType == "CONF":
                    print("Received config file.")
                    device = ESPDevice.fromConfigBytes(data[4:], ip_address) # Create an ESPDevice object from the config bytes
                    print(f"{device.name} is a ({device.type}) type device.")
                    if isinstance(device, ESPDevice):
                        sensorNames = [s.name for s in device.sensors]
                        print(f"Sensor list: {sensorNames}")

    except KeyboardInterrupt: # Gracefully close socket on Ctrl+C
        if running:
            running = False

        print("\nClosing connection...", flush=True)
        sock.close()

    except Exception as e:
        print(f"Error: {e}", flush=True)
        sock.close()
        sys.exit(1)


if __name__ == "__main__":  # Ensure it runs only when executed directly
    main()


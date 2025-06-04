import csv
import select
import socket
import sys

from libqretprop.ESPObjects.ESPDevice.ESPDevice import ESPDevice
from libqretprop.ESPObjects.SensorMonitor.SensorMonitor import SensorMonitor


def storeData(device: SensorMonitor, values: list[str]) -> None:
    """Store data values from an ESP32 device into the corresponding sensor objects.

    Appends the timestamp and sensor readings to the appropriate lists within the SensorMonitor instance.

    device (SensorMonitor): The device instance containing sensor objects and data storage lists.
    values (list[str]): List of string values where the first element is the timestamp and the remaining elements are
                        sensor readings, ordered according to sensor instantiation.
    """

    device.dataTimes.append(float(values[0])) # Log the time stamp of the data points

    for sensor, value in zip(device.sensors, values[1:], strict=False):
        sensor.data.append(float(value)) # Log the actual data points

def sendCommand(sock: socket.socket, command: str) -> None:
    """Send a command to the ESP32 device over the TCP socket.

    Parameters.
    ----------
        sock (socket.socket): The socket to send the command over.
        command (str): The command to send to the ESP32 device.
    """

    sock.sendall(command.encode())
    print("↦", command)

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
    buffer = "" # Buffer to store the data from the socket

    # The list of devices that have been discovered
    devices : dict[str, SensorMonitor] = {} # Dictionary of devices with the device name as the key and the ESPDevice object as the value

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # TCP socket parameters
        sock.connect((ip_address, port_number))
        print(f"Connected to {ip_address}:{port_number}")

        while running:
            readable, _ , _ = select.select([sock, sys.stdin], [], [], 0.1) # Check if the socket is readable. Select is blocking so we need a timeout to allow for Ctrl+C

            for src in readable:
                if src is sock: # This is where we handle data coming back from the ESP32

                    data = sock.recv(1024) # Read the data from the socket
                    messageType = data.decode("utf-8")[0:4] # Get the first 4 characters of the data

                    if messageType != "DATA": # Don't print stream data to the console
                        print(messageType)

                    if not data: # If there is no data, the connection has been closed
                        print("Server closed connection.")
                        sock.close()
                        sys.exit(1)

                    # Use a buffer style so we can split on newlines
                    chunk = data.decode("utf-8") # Use chunks as may contain multiple lines of data from TCP combining multiple packets into one
                    buffer += chunk # Add the chunk to the buffer

                    # Split the buffer into valid call lines
                    lines = buffer.split("\n") # Split the data into a list of lines to avoid TCP combining multiple packets into one
                    complete, buffer = lines[:-1],  lines[-1] # Get all but the last line from the buffer. The last line may be incomplete .

                    # print(complete) # Debug print every line received

                    for line in complete: # Process each line in the buffer
                        if not line: # Skip empty lines
                            continue

                        header, payload = line[:4], line[4:]

                        # Handle the startup CONF message when the connection is first established
                        if messageType == "CONF":
                            print("Received config file.")
                            device = ESPDevice.fromConfigBytes(data[4:], ip_address) # Create an ESPDevice object from the config bytes
                            print(f"{device.name} is a ({device.type}) type device.") # Print the device name and type to console

                            if device.name in devices: # Check if the device is already in the list of known devices
                                print(f"Device {device.name} was already discovered.")
                            else: devices[device.name] = device # Add the device to the list of known devices

                            if isinstance(device, ESPDevice): # Print out the active sensor names if the device is a SensorMonitor
                                sensorNames = [s.name for s in device.sensors]
                                print(f"Sensor list: {sensorNames}")

                        elif header == "DATA":
                            # Now you know this is a well‐formed DATA line
                            values = list(payload.split(","))
                            print("← DATA:", values)   # only one print per complete packet

                            # store it in your SensorMonitor
                            device = next(iter(devices.values()))
                            storeData(device, values)

                        else:
                            # something unexpected
                            print("Dropping unknown header:", header)

                #####
                # COMMANDS
                #####
                else:  # src must be sys.stdin. This is where we read user input commands.
                    line = sys.stdin.readline().strip()
                    if not line:
                        continue
                    cmd = line.upper()



                    # Use match-case for command handling (Python 3.10+)
                    match cmd:
                        case "GETS": sendCommand(sock, "GETS")
                        case "STRM": sendCommand(sock, "STRM")
                        case "STOP": sendCommand(sock, "STOP")
                        case "_DEVS":
                            print(f"Currently connected devices: {devices.keys()}")
                        case "_READINGS":
                            for device in devices.values():
                                print(f"Device: {device.name}")
                                for sensor in device.sensors:
                                    print(f"Sensor: {sensor.name}, Data: {sensor.data}")
                        case "_EXPORT":
                            print("Exporting data to CSV...")
                            device = next(iter(devices.values()))
                            headers = ["Time (ms)", *(sensor.name for sensor in device.sensors)]
                            columns = [device.dataTimes, *(sensor.data for sensor in device.sensors)]
                            with open(f"{device.name}_data.csv", "w", newline="") as csvfile:
                                writer = csv.writer(csvfile)
                                writer.writerow(headers)
                                writer.writerows(zip(*columns, strict=True))
                            print(f"→ Wrote {len(columns[0])} rows to sensor_data.csv")
                        case "EXIT" | "QUIT":
                            print("Closing Connection!")
                            sock.close()
                            sys.exit(0)
                        case _:
                            print(f"Unknown op-code: {line}")

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


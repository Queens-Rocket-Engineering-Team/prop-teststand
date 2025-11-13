#!/usr/bin/env python3
"""Mock ESP32 sensor device for testing the binary protocol.

This simulates a sensor monitor device that:
- Responds to SSDP discovery broadcasts
- Connects to the server via TCP
- Sends configuration using binary protocol
- Streams sensor data
- Responds to control commands

Usage:
    python3 mock_device.py                    # Auto-discover server
    python3 mock_device.py --server 127.0.0.1 # Connect to specific server
"""

import argparse
import asyncio
import json
import random
import socket
import struct
import time

from libqretprop.protocol import (
    AckPacket,
    ConfigPacket,
    ControlPacket,
    ControlState,
    DataPacket,
    decode_packet,
    DeviceStatus,
    PacketType,
    StatusPacket,
    StreamStartPacket,
)


class MockSensorDevice:
    """Simulates an ESP32 sensor monitor device."""

    def __init__(self, device_name: str = "MockDevice", server_ip: str | None = None):
        self.device_name = device_name
        self.server_ip = server_ip
        self.server_port = 50000

        # Device configuration
        self.config = {
            "deviceName": device_name,
            "deviceType": "Sensor Monitor",
            "sensorInfo": {
                "thermocouples": {
                    "TC1": {
                        "ADCIndex": 0,
                        "highPin": 1,
                        "lowPin": 2,
                        "type": "K",
                        "units": "C"
                    },
                    "TC2": {
                        "ADCIndex": 1,
                        "highPin": 3,
                        "lowPin": 4,
                        "type": "K",
                        "units": "C"
                    }
                },
                "pressureTransducers": {
                    "PT1": {
                        "ADCIndex": 2,
                        "pin": 5,
                        "maxPressure_PSI": 500,
                        "units": "PSI"
                    }
                }
            },
            "controls": {
                "AVFILL": {
                    "pin": 10,
                    "type": "valve",
                    "defaultState": "CLOSED"
                },
                "AVVENT": {
                    "pin": 11,
                    "type": "valve",
                    "defaultState": "CLOSED"
                }
            }
        }

        # Simulated sensor values
        self.tc1_temp = 23.0  # °C
        self.tc2_temp = 25.0  # °C
        self.pt1_pressure = 14.7  # PSI

        # Control states
        self.valve_states = {
            "AVFILL": "CLOSED",
            "AVVENT": "CLOSED"
        }

        # Streaming state
        self.streaming = False
        self.stream_frequency = 10  # Hz
        self.stream_task = None

        # Socket
        self.sock = None
        self.ssdp_sock = None

    def print_status(self, message: str, level: str = "INFO"):
        """Print colored status message."""
        colors = {
            "INFO": "\033[94m",   # Blue
            "SUCCESS": "\033[92m", # Green
            "WARNING": "\033[93m", # Yellow
            "ERROR": "\033[91m",   # Red
            "DATA": "\033[96m",    # Cyan
        }
        reset = "\033[0m"
        timestamp = time.strftime("%H:%M:%S")
        color = colors.get(level, "")
        print(f"{color}[{timestamp}] [{self.device_name}] {message}{reset}")

    async def start_ssdp_listener(self):
        """Listen for SSDP discovery broadcasts and respond."""
        self.print_status("Starting SSDP listener on 239.255.255.250:1900")

        # Create SSDP socket
        self.ssdp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass  # Not available on all systems

        # Bind to SSDP port
        self.ssdp_sock.bind(("", 1900))

        # Join multicast group
        mreq = struct.pack("4sL", socket.inet_aton("239.255.255.250"), socket.INADDR_ANY)
        self.ssdp_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        self.ssdp_sock.setblocking(False)

        loop = asyncio.get_event_loop()

        self.print_status("Waiting for SSDP discovery broadcast...", "INFO")

        while True:
            try:
                data, addr = await loop.sock_recvfrom(self.ssdp_sock, 1024)
                message = data.decode('utf-8', errors='ignore')

                # Check if it's an M-SEARCH request
                if "M-SEARCH" in message and "urn:qretprop:espdevice:1" in message:
                    self.print_status(f"Received discovery from {addr[0]}", "SUCCESS")

                    # Send SSDP response
                    response = (
                        "HTTP/1.1 200 OK\r\n"
                        "CACHE-CONTROL: max-age=1800\r\n"
                        "EXT:\r\n"
                        "LOCATION: http://192.168.1.50:50000\r\n"
                        "SERVER: ESP32/1.0 UPnP/1.0\r\n"
                        "ST: urn:qretprop:espdevice:1\r\n"
                        "USN: uuid:mock-device-001::urn:qretprop:espdevice:1\r\n"
                        "\r\n"
                    )

                    self.ssdp_sock.sendto(response.encode(), addr)
                    self.print_status(f"Sent SSDP response to {addr[0]}", "SUCCESS")

                    # Remember server IP and connect
                    if self.server_ip is None:
                        self.server_ip = addr[0]
                        await asyncio.sleep(0.5)  # Give server time to start listener
                        await self.connect_to_server()

            except asyncio.CancelledError:
                break
            except Exception as e:
                if "Resource temporarily unavailable" not in str(e):
                    self.print_status(f"SSDP error: {e}", "ERROR")
                await asyncio.sleep(0.1)

    async def connect_to_server(self):
        """Connect to server via TCP and send config."""
        if self.sock is not None:
            self.print_status("Already connected to server", "WARNING")
            return

        self.print_status(f"Connecting to server at {self.server_ip}:{self.server_port}", "INFO")

        try:
            loop = asyncio.get_event_loop()
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setblocking(False)

            await loop.sock_connect(self.sock, (self.server_ip, self.server_port))
            self.print_status("TCP connection established", "SUCCESS")

            # Send configuration
            await self.send_config()

            # Start listening for commands
            asyncio.create_task(self.handle_commands())

        except Exception as e:
            self.print_status(f"Connection failed: {e}", "ERROR")
            self.sock = None

    async def send_config(self):
        """Send device configuration to server."""
        config_json = json.dumps(self.config)
        packet = ConfigPacket.create(config_json)

        loop = asyncio.get_event_loop()
        await loop.sock_sendall(self.sock, packet.pack())

        self.print_status(f"Sent CONFIG ({len(packet.pack())} bytes)", "SUCCESS")

    async def handle_commands(self):
        """Listen for and handle commands from server."""
        loop = asyncio.get_event_loop()
        buffer = b""

        self.print_status("Listening for commands...", "INFO")

        try:
            while True:
                data = await loop.sock_recv(self.sock, 1024)
                if not data:
                    self.print_status("Server disconnected", "ERROR")
                    break

                self.print_status(f"Received {len(data)} bytes: {data.hex()[:60]}...", "DATA")
                buffer += data
                self.print_status(f"Buffer now {len(buffer)} bytes", "DATA")

                # Try to decode packets
                while len(buffer) >= 10:  # Minimum packet size
                    try:
                        self.print_status(f"Attempting to decode packet from buffer...", "DATA")
                        packet = decode_packet(buffer)
                        packet_size = len(packet.pack())
                        self.print_status(f"Decoded {packet.header.packet_type.name} packet ({packet_size} bytes)", "SUCCESS")

                        # Handle different packet types
                        if packet.header.packet_type == PacketType.TIMESYNC:
                            self.print_status(f"Received TIMESYNC", "INFO")
                            # Send ACK
                            ack = AckPacket.create(PacketType.TIMESYNC)
                            await loop.sock_sendall(self.sock, ack.pack())

                        elif packet.header.packet_type == PacketType.CONTROL:
                            await self.handle_control_command(packet)

                        elif packet.header.packet_type == PacketType.STREAM_START:
                            await self.handle_stream_start(packet)

                        elif packet.header.packet_type == PacketType.STREAM_STOP:
                            await self.handle_stream_stop()

                        elif packet.header.packet_type == PacketType.GET_SINGLE:
                            await self.send_single_reading()

                        elif packet.header.packet_type == PacketType.STATUS_REQUEST:
                            await self.send_status()

                        elif packet.header.packet_type == PacketType.HEARTBEAT:
                            # Respond to heartbeat
                            ack = AckPacket.create(PacketType.HEARTBEAT)
                            await loop.sock_sendall(self.sock, ack.pack())

                        # Remove processed packet
                        buffer = buffer[packet_size:]

                    except ValueError:
                        # Not enough data yet
                        break
                    except Exception as e:
                        self.print_status(f"Error decoding packet: {e}", "ERROR")
                        break

        except Exception as e:
            self.print_status(f"Command handler error: {e}", "ERROR")

    async def handle_control_command(self, packet: ControlPacket):
        """Handle a control command."""
        command_id = packet.command_id
        state = packet.command_state

        # Map command_id to control name
        control_names = list(self.config["controls"].keys())
        if command_id < len(control_names):
            control_name = control_names[command_id]
            state_str = "OPEN" if state == ControlState.OPEN else "CLOSED"

            self.valve_states[control_name] = state_str
            self.print_status(f"Control: {control_name} → {state_str}", "SUCCESS")

            # Send ACK
            loop = asyncio.get_event_loop()
            ack = AckPacket.create(PacketType.CONTROL)
            await loop.sock_sendall(self.sock, ack.pack())
        else:
            self.print_status(f"Invalid command_id: {command_id}", "ERROR")
            # Send NACK
            loop = asyncio.get_event_loop()
            nack = AckPacket.create(PacketType.CONTROL, is_nack=True)
            await loop.sock_sendall(self.sock, nack.pack())

    async def handle_stream_start(self, packet: StreamStartPacket):
        """Start streaming sensor data."""
        self.stream_frequency = packet.frequency_hz
        self.streaming = True

        self.print_status(f"Starting stream at {self.stream_frequency} Hz", "SUCCESS")

        # Send ACK
        loop = asyncio.get_event_loop()
        ack = AckPacket.create(PacketType.STREAM_START)
        await loop.sock_sendall(self.sock, ack.pack())

        # Start streaming task
        if self.stream_task:
            self.stream_task.cancel()
        self.stream_task = asyncio.create_task(self.stream_data())

    async def handle_stream_stop(self):
        """Stop streaming sensor data."""
        self.streaming = False
        self.print_status("Stopping stream", "INFO")

        if self.stream_task:
            self.stream_task.cancel()
            self.stream_task = None

        # Send ACK
        loop = asyncio.get_event_loop()
        ack = AckPacket.create(PacketType.STREAM_STOP)
        await loop.sock_sendall(self.sock, ack.pack())

    async def stream_data(self):
        """Stream sensor data at specified frequency."""
        interval = 1.0 / self.stream_frequency

        try:
            while self.streaming:
                await self.send_sensor_data()
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def send_sensor_data(self):
        """Send current sensor readings."""
        # Simulate sensor readings with small variations
        self.tc1_temp += random.uniform(-0.5, 0.5)
        self.tc2_temp += random.uniform(-0.5, 0.5)
        self.pt1_pressure += random.uniform(-0.2, 0.2)

        # Keep in reasonable ranges
        self.tc1_temp = max(20.0, min(30.0, self.tc1_temp))
        self.tc2_temp = max(20.0, min(30.0, self.tc2_temp))
        self.pt1_pressure = max(10.0, min(20.0, self.pt1_pressure))

        loop = asyncio.get_event_loop()

        # Send data for each sensor (using sensor indices)
        sensors = [
            (0, self.tc1_temp, "TC1"),
            (1, self.tc2_temp, "TC2"),
            (2, self.pt1_pressure, "PT1"),
        ]

        for sensor_id, value, name in sensors:
            packet = DataPacket.create(sensor_id=sensor_id, data=value)
            await loop.sock_sendall(self.sock, packet.pack())

        # Print occasionally (not every packet)
        if random.random() < 0.1:  # 10% of the time
            self.print_status(
                f"Data: TC1={self.tc1_temp:.1f}°C TC2={self.tc2_temp:.1f}°C PT1={self.pt1_pressure:.1f}PSI",
                "DATA"
            )

    async def send_single_reading(self):
        """Send a single sensor reading."""
        self.print_status("Sending single reading", "INFO")
        await self.send_sensor_data()

        # Send ACK
        loop = asyncio.get_event_loop()
        ack = AckPacket.create(PacketType.GET_SINGLE)
        await loop.sock_sendall(self.sock, ack.pack())

    async def send_status(self):
        """Send device status."""
        status = StatusPacket.create(DeviceStatus.ACTIVE)

        loop = asyncio.get_event_loop()
        await loop.sock_sendall(self.sock, status.pack())

        self.print_status("Sent STATUS: ACTIVE", "INFO")

    async def run(self):
        """Main run loop."""
        self.print_status("=== Mock Sensor Device Started ===", "SUCCESS")
        self.print_status(f"Device name: {self.device_name}", "INFO")
        self.print_status(f"Sensors: TC1, TC2, PT1", "INFO")
        self.print_status(f"Controls: AVFILL, AVVENT", "INFO")
        print()

        if self.server_ip:
            # Direct connection mode
            self.print_status(f"Connecting directly to {self.server_ip}", "INFO")
            await self.connect_to_server()

            # Keep running
            try:
                await asyncio.Event().wait()
            except KeyboardInterrupt:
                pass
        else:
            # Discovery mode
            self.print_status("Waiting for server discovery...", "INFO")
            try:
                await self.start_ssdp_listener()
            except KeyboardInterrupt:
                pass

        # Cleanup
        if self.sock:
            self.sock.close()
        if self.ssdp_sock:
            self.ssdp_sock.close()

        self.print_status("=== Mock Device Stopped ===", "INFO")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Mock ESP32 sensor device for protocol testing")
    parser.add_argument("--server", "-s", help="Server IP address (default: auto-discover)")
    parser.add_argument("--name", "-n", default="MockDevice", help="Device name")

    args = parser.parse_args()

    device = MockSensorDevice(device_name=args.name, server_ip=args.server)
    await device.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nStopped by user")


#!/usr/bin/env python3
"""Mock ESP32 sensor device for testing the binary protocol v2.

This simulates a sensor monitor device that:
- Responds to SSDP discovery broadcasts
- Connects to the server via TCP
- Sends configuration using binary protocol v2
- Streams batched sensor data
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
    HEADER_SIZE,
    AckPacket,
    ConfigPacket,
    ControlPacket,
    ControlState,
    ControlStatus,
    DataPacket,
    DeviceStatus,
    ErrorCode,
    NackPacket,
    PacketType,
    SensorReading,
    SimplePacket,
    StatusPacket,
    StreamStartPacket,
    Unit,
    decode_packet_client,
    get_packet_len,
)

import contextlib


class MockSensorDevice:
    """Simulates an ESP32 sensor monitor device."""

    def __init__(self, device_name: str = "MockDevice", server_ip: str | None = None):
        self.device_name = device_name
        self.server_ip = server_ip
        self.server_port = 50000
        self.server_udp_port = 50001
        self.server_udp_port = 50001

        # Device configuration
        self.config = {
            "device_name": device_name,
            "device_type": "Sensor Monitor",
            "sensor_info": {
                "thermocouple": {},
                "pressure_transducer": {
                    "PT101": {
                        "sensor_index": "PT1",
                        "resistor_ohms": 350,
                        "max_pressure_PSI": 500,
                        "unit": "PSI",
                    },
                    "PT201": {
                        "sensor_index": "PT1",
                        "resistor_ohms": 350,
                        "max_pressure_PSI": 500,
                        "unit": "PSI",
                    },
                    "PT202": {
                        "sensor_index": "PT1",
                        "resistor_ohms": 350,
                        "max_pressure_PSI": 500,
                        "unit": "PSI",
                    },
                    "PT204": {
                        "sensor_index": "PT1",
                        "resistor_ohms": 350,
                        "max_pressure_PSI": 500,
                        "unit": "PSI",
                    },
                },
            },
            "controls": {
                "AV101": {
                    "control_index": "AV_DUMP",
                    "type": "valve",
                    "default_state": "CLOSED",
                },
                "AV201": {
                    "control_index": "AV_FILL",
                    "type": "valve",
                    "default_state": "CLOSED",
                },
                "AV202": {
                    "control_index": "AV_DUMP",
                    "type": "valve",
                    "default_state": "OPEN",
                },
                "AV203": {
                    "control_index": "AV_3",
                    "type": "valve",
                    "default_state": "CLOSED",
                },
                "AV204": {
                    "control_index": "AV_4",
                    "type": "valve",
                    "default_state": "OPEN",
                },
                "AV205": {
                    "control_index": "AV_5",
                    "type": "valve",
                    "default_state": "CLOSED",
                },
            },
        }

        # Simulated sensor values
        self.tc1_temp = 23.0
        self.tc2_temp = 25.0
        self.pt1_pressure = 14.7

        # Control states
        self.valve_states = {
            "AV101": "CLOSED",
            "AV201": "CLOSED",
            "AV202": "OPEN",
            "AV203": "CLOSED",
            "AV204": "OPEN",
            "AV205": "CLOSED",
        }

        # Streaming state
        self.streaming = False
        self.stream_frequency = 10
        self.stream_task = None
        self.command_task = None
        self.ssdp_task = None

        # Timesync offset: added to local ticks to produce server-scale timestamps
        self.timesync_offset = 0

        # Socket
        self.sock = None
        self.ssdp_sock = None
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # Does not need to be None because no "connected" state for UDP
        self.udp_sock.setblocking(False)

    def reset_device_state(self, announce: bool = False):
        """Reset runtime state after a disconnect."""
        self.streaming = False
        if self.stream_task:
            self.stream_task.cancel()
            self.stream_task = None

        self.tc1_temp = 23.0
        self.tc2_temp = 25.0
        self.pt1_pressure = 14.7

        self.valve_states = {
            "AV101": "CLOSED",
            "AV201": "CLOSED",
            "AV202": "OPEN",
            "AV203": "CLOSED",
            "AV204": "OPEN",
            "AV205": "CLOSED",
        }

        self.timesync_offset = 0

        if announce:
            self.print_status("Device state reset after disconnect", "INFO")

    def ensure_ssdp_listener(self):
        """Start SSDP listener in background if not already running."""
        if self.ssdp_task and not self.ssdp_task.done():
            return
        self.ssdp_task = asyncio.create_task(self.start_ssdp_listener())

    async def handle_server_disconnect(self):
        """Clean up socket and resume discovery mode after a server disconnect."""
        if self.sock:
            with contextlib.suppress(Exception):
                self.sock.close()
        self.sock = None

        self.server_ip = None
        self.reset_device_state(announce=True)
        self.print_status("Listening for discovery after disconnect...", "WARNING")
        self.ensure_ssdp_listener()

    def _get_adjusted_ts(self) -> int:
        """
        Return the TIMESYNCed timestamp to use for outgoing packets by applying the offset to local monotonic time.
        """
        # Convert our local monotonic time to server time by applying the offset, and write it into seven bytes for the header
        return (int(time.monotonic() * 1000) + self.timesync_offset) & 0xFFFFFFFF

    def print_status(self, message: str, level: str = "INFO"):
        colors = {
            "INFO": "\033[94m",
            "SUCCESS": "\033[92m",
            "WARNING": "\033[93m",
            "ERROR": "\033[91m",
            "DATA": "\033[96m",
        }
        reset = "\033[0m"
        timestamp = time.strftime("%H:%M:%S")
        color = colors.get(level, "")
        print(f"{color}[{timestamp}] [{self.device_name}] {message}{reset}")

    async def start_ssdp_listener(self):
        """Listen for SSDP discovery broadcasts and extract server IP."""
        self.print_status("Starting SSDP listener on 239.255.255.250:1900")

        if self.ssdp_sock:
            with contextlib.suppress(Exception):
                self.ssdp_sock.close()
            self.ssdp_sock = None

        self.ssdp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        with contextlib.suppress(AttributeError):
            self.ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        self.ssdp_sock.bind(("", 1900))

        ssdp_membership_request = struct.pack("4sL", socket.inet_aton("239.255.255.250"), socket.INADDR_ANY)
        self.ssdp_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, ssdp_membership_request)

        self.ssdp_sock.setblocking(False)

        loop = asyncio.get_event_loop()

        self.print_status("Waiting for SSDP discovery broadcast...", "INFO")

        while True:
            try:
                data, addr = await loop.sock_recvfrom(self.ssdp_sock, 1024)
                message = data.decode("utf-8", errors="ignore")

                if "M-SEARCH" in message and "urn:qretprop:espdevice:1" in message:
                    self.print_status(f"Received discovery from {addr[0]}", "SUCCESS")

                    if self.server_ip is None:
                        self.server_ip = addr[0]
                        await asyncio.sleep(0.5)
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

            await self.send_config()

            self.command_task = asyncio.create_task(self.handle_commands())

        except Exception as e:
            self.print_status(f"Connection failed: {e}", "ERROR")
            self.sock = None

    async def send_config(self):
        """Send device configuration to server."""
        config_json = json.dumps(self.config)
        packet = ConfigPacket.create(config_json)
        encoded_packet = packet.encode()

        loop = asyncio.get_event_loop()
        await loop.sock_sendall(self.sock, encoded_packet)

        self.print_status(f"Sent CONFIG ({len(encoded_packet)} bytes)", "SUCCESS")

    async def handle_commands(self):
        """Listen for and handle commands from server using LENGTH-based framing."""
        loop = asyncio.get_event_loop()
        buffer = b""

        self.print_status("Listening for commands...", "INFO")

        try:
            while True:
                data = await loop.sock_recv(self.sock, 4096)
                if not data:
                    self.print_status("Server disconnected", "WARNING")
                    break

                buffer += data

                while len(buffer) >= HEADER_SIZE:
                    try:
                        packet_len = get_packet_len(buffer)
                        if len(buffer) < packet_len:
                            break  # Need more data

                        packet_data = buffer[:packet_len]
                        packet = decode_packet_client(packet_data)

                        self.print_status(f"Decoded {packet.__class__.__name__} ({packet_len} bytes)", "SUCCESS")

                        if isinstance(packet, SimplePacket) and packet.packet_type == PacketType.TIMESYNC:
                            # Convert our local monotonic time to server time by applying the offset, and write it into
                            # a 7 byte sequence for the header
                            server_ts = packet.timestamp
                            device_ts = int(time.monotonic() * 1000) & 0xFFFFFFFF

                            self.timesync_offset = server_ts - device_ts
                            self.print_status(f"TIMESYNC: locked to server (offset={self.timesync_offset}ms)", "SUCCESS")

                            ack = AckPacket.create(PacketType.TIMESYNC, packet.sequence)
                            ack.timestamp = self._get_adjusted_ts()

                            await loop.sock_sendall(self.sock, ack.encode())

                        elif isinstance(packet, SimplePacket) and packet.packet_type == PacketType.ESTOP:
                            self.print_status("Received ESTOP - stopping stream and resetting state", "WARNING")
                            await self.handle_stream_stop()
                            self.reset_device_state(announce=True)

                        elif isinstance(packet, ControlPacket):
                            await self.handle_control_command(packet)

                        elif isinstance(packet, StreamStartPacket):
                            await self.handle_stream_start(packet)

                        elif isinstance(packet, SimplePacket) and packet.packet_type == PacketType.STREAM_STOP:
                            await self.handle_stream_stop(packet)

                        elif isinstance(packet, SimplePacket) and packet.packet_type == PacketType.GET_SINGLE:
                            await self.send_single_reading(packet)

                        elif isinstance(packet, SimplePacket) and packet.packet_type == PacketType.STATUS_REQUEST:
                            await self.send_status()

                        elif isinstance(packet, SimplePacket) and packet.packet_type == PacketType.HEARTBEAT:
                            ack = AckPacket.create(PacketType.HEARTBEAT, packet.sequence)
                            ack.timestamp = self._get_adjusted_ts()

                            await loop.sock_sendall(self.sock, ack.encode())

                        buffer = buffer[packet_len:]

                    except ValueError:
                        break
                    except Exception as e:
                        self.print_status(f"Error decoding packet: {e}", "ERROR")
                        break

        except asyncio.CancelledError:
            # Task was cancelled, likely due to shutdown; avoid treating as an error.
            self.print_status("Command handler cancelled", "INFO")
            raise
        except Exception as e:
            self.print_status(f"Command handler error: {e}", "ERROR")
        finally:
            self.command_task = None
            # Avoid triggering disconnect handling when this task is being cancelled
            task = asyncio.current_task()
            if self.sock is not None and not (task and task.cancelled()):
                await self.handle_server_disconnect()

    async def handle_control_command(self, packet: ControlPacket):
        command_id = packet.command_id
        state = packet.command_state

        control_names = list(self.config["controls"].keys())
        loop = asyncio.get_event_loop()

        if command_id < len(control_names):
            control_name = control_names[command_id]
            state_str = "OPEN" if state == ControlState.OPEN else "CLOSED"

            self.valve_states[control_name] = state_str
            self.print_status(f"Control: {control_name} -> {state_str}", "SUCCESS")

            ack = AckPacket.create(PacketType.CONTROL, packet.sequence)
            ack.timestamp = self._get_adjusted_ts()

            await loop.sock_sendall(self.sock, ack.encode())
        else:
            self.print_status(f"Invalid command_id: {command_id}", "ERROR")
            nack = NackPacket.create(PacketType.CONTROL, packet.sequence, ErrorCode.INVALID_ID)
            nack.timestamp = self._get_adjusted_ts()

            await loop.sock_sendall(self.sock, nack.encode())

    async def handle_stream_start(self, packet: StreamStartPacket):
        self.stream_frequency = packet.frequency_hz
        self.streaming = True

        self.print_status(f"Starting stream at {self.stream_frequency} Hz", "SUCCESS")

        loop = asyncio.get_event_loop()
        ack = AckPacket.create(PacketType.STREAM_START, packet.sequence)
        ack.timestamp = self._get_adjusted_ts()
        await loop.sock_sendall(self.sock, ack.encode())

        if self.stream_task:
            self.stream_task.cancel()
        self.stream_task = asyncio.create_task(self.stream_data())

    async def handle_stream_stop(self, packet=None):
        self.streaming = False
        self.print_status("Stopping stream", "INFO")

        if self.stream_task:
            self.stream_task.cancel()
            self.stream_task = None

        loop = asyncio.get_event_loop()
        seq = packet.sequence if packet else 0
        ack = AckPacket.create(PacketType.STREAM_STOP, seq)
        ack.timestamp = self._get_adjusted_ts()
        await loop.sock_sendall(self.sock, ack.encode())

    async def stream_data(self):
        interval = 1.0 / self.stream_frequency
        next_send = time.monotonic()
        next_send = time.monotonic()

        try:
            while self.streaming:
                now = time.monotonic()
                if now < next_send:
                    await asyncio.sleep(0)
                    continue
                now = time.monotonic()
                if now < next_send:
                    await asyncio.sleep(0)
                    continue
                await self.send_sensor_data()
                next_send += interval
                # If we've fallen behind, reset to avoid a catch-up burst
                if time.monotonic() > next_send:
                    next_send = time.monotonic() + interval
                next_send += interval
                # If we've fallen behind, reset to avoid a catch-up burst
                if time.monotonic() > next_send:
                    next_send = time.monotonic() + interval
        except asyncio.CancelledError:
            pass

    async def send_sensor_data(self):
        """Send all sensor readings in a single batched DATA packet."""
        # Simulate sensor readings with small variations
        self.tc1_temp += random.uniform(-0.5, 0.5)
        self.tc2_temp += random.uniform(-0.5, 0.5)
        self.pt1_pressure += random.uniform(-0.2, 0.2)

        self.tc1_temp = max(20.0, min(30.0, self.tc1_temp))
        self.tc2_temp = max(20.0, min(30.0, self.tc2_temp))
        self.pt1_pressure = max(10.0, min(20.0, self.pt1_pressure))

        readings = [
            SensorReading(sensor_id=0, unit=Unit.CELSIUS, value=self.tc1_temp),
            SensorReading(sensor_id=1, unit=Unit.CELSIUS, value=self.tc2_temp),
            SensorReading(sensor_id=2, unit=Unit.PSI, value=self.pt1_pressure),
        ]

        packet = DataPacket.create(readings)
        packet.timestamp = self._get_adjusted_ts()

        loop = asyncio.get_event_loop()

        # Send data over UDP for performance
        await loop.sock_sendto(self.udp_sock, packet.encode(), (self.server_ip, self.server_udp_port))

        if random.random() < 0.1:
            self.print_status(f"Data: TC1={self.tc1_temp:.1f}C TC2={self.tc2_temp:.1f}C PT1={self.pt1_pressure:.1f}PSI", "DATA")

    async def send_single_reading(self, request_packet=None):
        self.print_status("Sending single reading", "INFO")
        await self.send_sensor_data()

        loop = asyncio.get_event_loop()
        seq = request_packet.sequence if request_packet else 0
        ack = AckPacket.create(PacketType.GET_SINGLE, seq)
        ack.timestamp = self._get_adjusted_ts()
        await loop.sock_sendall(self.sock, ack.encode())

    async def send_status(self):
        control_states = []
        # Preserve the order of controls as defined in the config
        for control_name in self.config["controls"]:
            state_str = self.valve_states.get(control_name, "UNKNOWN")
            state_enum = ControlState.OPEN if state_str == "OPEN" else ControlState.CLOSED if state_str == "CLOSED" else ControlState.ERROR
            control_states.append(ControlStatus(id=len(control_states), state=state_enum))

        status = StatusPacket.create(DeviceStatus.ACTIVE, control_states=control_states)
        status.timestamp = self._get_adjusted_ts()

        loop = asyncio.get_event_loop()
        await loop.sock_sendall(self.sock, status.encode())

        self.print_status("Sent STATUS: ACTIVE", "INFO")
        self.print_status(
            f"Control states: " + ", ".join(f"{name}={self.valve_states.get(name, 'UNKNOWN')}" for name in self.config["controls"]), "INFO"
        )

    async def run(self):
        self.print_status("=== Mock Sensor Device Started ===", "SUCCESS")
        self.print_status(f"Device name: {self.device_name}", "INFO")
        self.print_status(f"Sensors: TC1, TC2, PT1", "INFO")
        self.print_status(f"Controls: AVFILL, AVVENT", "INFO")
        print()

        if self.server_ip:
            self.print_status(f"Connecting directly to {self.server_ip}", "INFO")
            await self.connect_to_server()

            try:
                await asyncio.Event().wait()
            except KeyboardInterrupt:
                pass
        else:
            self.print_status("Waiting for server discovery...", "INFO")
            self.ensure_ssdp_listener()
            try:
                await asyncio.Event().wait()
            except KeyboardInterrupt:
                pass

        if self.sock:
            self.sock.close()
        if self.command_task:
            self.command_task.cancel()
        if self.stream_task:
            self.stream_task.cancel()
        if self.ssdp_task:
            self.ssdp_task.cancel()
        if self.ssdp_sock:
            self.ssdp_sock.close()

        self.print_status("=== Mock Device Stopped ===", "INFO")


async def async_main():
    parser = argparse.ArgumentParser(description="Mock ESP32 sensor device for protocol testing")
    parser.add_argument("--server", "-s", help="Server IP address (default: auto-discover)")
    parser.add_argument("--name", "-n", default="MockDevice", help="Device name")

    args = parser.parse_args()

    device = MockSensorDevice(device_name=args.name, server_ip=args.server)
    await device.run()


# Required as entrypoint for uv run
def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n\nStopped by user")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nStopped by user")

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

Library usage (for tests):
    async with MockSensorDevice(server_ip="127.0.0.1", server_port=p, server_udp_port=u) as dev:
        await asyncio.wait_for(dev.timesync_received.wait(), timeout=2.0)
        ...
"""

import argparse
import asyncio
import contextlib
import json
import logging
import random
import socket
import struct
import time
from typing import Any

from libqretprop.qlcp.config_models import SensorConfig
from libqretprop.qlcp.config_parser import parse_config
from libqretprop.qlcp.constants import HEADER_SIZE
from libqretprop.qlcp.decoding import decode_packet_client
from libqretprop.qlcp.enums import (
    ControlState,
    DeviceStatus,
    ErrorCode,
    PacketType,
    Unit,
)
from libqretprop.qlcp.framing import get_packet_len
from libqretprop.qlcp.packets import (
    AckPacket,
    ConfigPacket,
    ControlPacket,
    ControlStatus,
    DataPacket,
    NackPacket,
    SensorReading,
    SimplePacket,
    StatusPacket,
    StreamStartPacket,
)


logger = logging.getLogger(__name__)

# Default device config: 2 thermocouples (ids 0-1, CELSIUS) + 2 pressure transducers (ids 2-3, PSI).
# Sensor ordering in sensor_info determines the ids assigned by parse_config.
_DEFAULT_CONFIG: dict[str, Any] = {
    "device_name": "MockDevice",
    "device_type": "Sensor Monitor",
    "sensor_info": {
        "thermocouple": {
            "TC101": {
                "sensor_index": "TC1",
                "type": "K",
                "unit": "C",
            },
            "TC201": {
                "sensor_index": "TC2",
                "type": "K",
                "unit": "C",
            },
        },
        "pressure_transducer": {
            "PT101": {
                "sensor_index": "PT1",
                "resistor_ohms": 350,
                "max_pressure_PSI": 500,
                "unit": "PSI",
            },
            "PT201": {
                "sensor_index": "PT2",
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
    },
}


class MockSensorDevice:
    """Simulates an ESP32 sensor monitor device.

    Can be used as an async context manager for test isolation::

        async with MockSensorDevice(server_ip="127.0.0.1", server_port=p, server_udp_port=u) as dev:
            await asyncio.wait_for(dev.timesync_received.wait(), timeout=2.0)
            # device is registered and synced
    """

    def __init__(
        self,
        device_name: str = "MockDevice",
        server_ip: str | None = None,
        server_port: int = 50000,
        server_udp_port: int = 50001,
        config: dict[str, Any] | None = None,
    ):
        self.server_ip = server_ip
        self.server_port = server_port
        self.server_udp_port = server_udp_port

        # Build config: start from the default, override device_name; or use caller-supplied config.
        if config is not None:
            self.config = config
        else:
            self.config = {**_DEFAULT_CONFIG, "device_name": device_name}

        # Parse with the same parser the server uses — ids are derived, not hardcoded.
        self._device_config = parse_config(self.config)

        # Per-sensor simulated state (keyed by sensor_id).
        self._sensor_values: dict[int, float] = {
            sid: self._initial_value(s)
            for sid, s in self._device_config.sensors_by_id.items()
        }

        # Control states (keyed by control name), initialised from config defaults.
        self.valve_states: dict[str, str] = {
            c.name: c.default.name
            for c in self._device_config.controls_by_id.values()
        }

        # Streaming state
        self.streaming = False
        self.stream_frequency = 10
        self.stream_task: asyncio.Task[None] | None = None
        self.command_task: asyncio.Task[None] | None = None
        self.ssdp_task: asyncio.Task[None] | None = None

        # Timesync offset: added to local monotonic ms to produce server-scale timestamps.
        self.timesync_offset = 0

        # Sockets
        self.sock: socket.socket | None = None
        self.ssdp_sock: socket.socket | None = None
        # UDP socket is reusable (no "connected" state); created once at startup.
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setblocking(False)

        # Controls CLI-mode auto-reconnect via SSDP; False in library/test mode.
        self._auto_reconnect = False

        # Observability events for test synchronisation — never use fixed sleeps in tests;
        # await these instead.
        self.config_sent: asyncio.Event = asyncio.Event()
        self.timesync_received: asyncio.Event = asyncio.Event()
        self.control_handled: asyncio.Event = asyncio.Event()
        self.stream_started: asyncio.Event = asyncio.Event()
        self.stream_stopped: asyncio.Event = asyncio.Event()
        self.data_sent: asyncio.Event = asyncio.Event()

    # ---------------------------------------------------------------------- #
    # Properties                                                               #
    # ---------------------------------------------------------------------- #

    @property
    def device_name(self) -> str:
        return self._device_config.name

    # ---------------------------------------------------------------------- #
    # Simulation helpers                                                       #
    # ---------------------------------------------------------------------- #

    @staticmethod
    def _initial_value(sensor: SensorConfig) -> float:
        """Return a sensible initial simulated value for a sensor unit."""
        baselines: dict[Unit, float] = {
            Unit.CELSIUS: 23.0,
            Unit.FAHRENHEIT: 73.0,
            Unit.KELVIN: 296.0,
            Unit.PSI: 14.7,
            Unit.BAR: 1.0,
            Unit.PASCAL: 101325.0,
            Unit.VOLTS: 3.3,
            Unit.AMPS: 0.1,
            Unit.OHMS: 100.0,
        }
        return baselines.get(sensor.unit, 0.0)

    @staticmethod
    def _step_value(sensor: SensorConfig, current: float) -> float:
        """Randomly evolve a simulated sensor value within a plausible range."""
        unit = sensor.unit
        if unit == Unit.CELSIUS:
            return max(20.0, min(30.0, current + random.uniform(-0.5, 0.5)))
        if unit == Unit.FAHRENHEIT:
            return max(68.0, min(86.0, current + random.uniform(-0.9, 0.9)))
        if unit == Unit.KELVIN:
            return max(293.0, min(303.0, current + random.uniform(-0.5, 0.5)))
        if unit == Unit.PSI:
            return max(10.0, min(20.0, current + random.uniform(-0.2, 0.2)))
        if unit == Unit.BAR:
            return max(0.5, min(1.5, current + random.uniform(-0.01, 0.01)))
        if unit == Unit.PASCAL:
            return max(90000.0, min(110000.0, current + random.uniform(-10, 10)))
        if unit in (Unit.KILOGRAMS, Unit.GRAMS, Unit.POUNDS, Unit.NEWTONS):
            return max(0.0, current + random.uniform(-0.1, 0.1))
        if unit == Unit.VOLTS:
            return max(3.0, min(3.6, current + random.uniform(-0.01, 0.01)))
        if unit == Unit.AMPS:
            return max(0.0, min(0.5, current + random.uniform(-0.005, 0.005)))
        if unit == Unit.OHMS:
            return max(0.0, current + random.uniform(-1, 1))
        return current + random.uniform(-0.1, 0.1)

    # ---------------------------------------------------------------------- #
    # Lifecycle                                                                #
    # ---------------------------------------------------------------------- #

    def reset_device_state(self, *, announce: bool = False) -> None:
        """Reset runtime state after a disconnect or ESTOP."""
        self.streaming = False
        if self.stream_task:
            self.stream_task.cancel()
            self.stream_task = None

        self._sensor_values = {
            sid: self._initial_value(s)
            for sid, s in self._device_config.sensors_by_id.items()
        }
        self.valve_states = {
            c.name: c.default.name
            for c in self._device_config.controls_by_id.values()
        }
        self.timesync_offset = 0

        # Clear observability events so test code can re-await them after a reset.
        self.config_sent.clear()
        self.timesync_received.clear()
        self.control_handled.clear()
        self.stream_started.clear()
        self.stream_stopped.clear()
        self.data_sent.clear()

        if announce:
            logger.info("Device state reset after disconnect")

    async def start(self) -> None:
        """Connect directly to server_ip (no SSDP). Intended for library/test use.

        Raises RuntimeError if server_ip is not set.
        """
        if self.server_ip is None:
            raise RuntimeError("server_ip must be set to call start(); use run() for SSDP discovery")
        await self.connect_to_server()

    async def stop(self) -> None:
        """Cancel all tasks and close all sockets. Idempotent."""
        self.streaming = False

        if self.stream_task:
            self.stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.stream_task
            self.stream_task = None

        if self.command_task:
            self.command_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.command_task
            self.command_task = None

        if self.ssdp_task:
            self.ssdp_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.ssdp_task
            self.ssdp_task = None

        if self.sock:
            with contextlib.suppress(Exception):
                self.sock.close()
            self.sock = None

        if self.ssdp_sock:
            with contextlib.suppress(Exception):
                self.ssdp_sock.close()
            self.ssdp_sock = None

        with contextlib.suppress(Exception):
            self.udp_sock.close()

        logger.info("Mock device stopped")

    async def __aenter__(self) -> "MockSensorDevice":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ---------------------------------------------------------------------- #
    # Timestamp helper                                                         #
    # ---------------------------------------------------------------------- #

    def _get_adjusted_ts(self) -> int:
        """Return a server-scale timestamp (ms) by applying the TIMESYNC offset."""
        return (int(time.monotonic() * 1000) + self.timesync_offset) & 0xFFFFFFFF

    # ---------------------------------------------------------------------- #
    # SSDP discovery                                                           #
    # ---------------------------------------------------------------------- #

    def ensure_ssdp_listener(self) -> None:
        """Start the SSDP listener in the background if not already running."""
        if self.ssdp_task and not self.ssdp_task.done():
            return
        self.ssdp_task = asyncio.create_task(self.start_ssdp_listener())

    async def handle_server_disconnect(self) -> None:
        """Clean up the TCP socket and optionally resume SSDP discovery."""
        if self.sock:
            with contextlib.suppress(Exception):
                self.sock.close()
        self.sock = None
        self.server_ip = None
        self.reset_device_state(announce=True)
        if self._auto_reconnect:
            logger.warning("Listening for discovery after disconnect…")
            self.ensure_ssdp_listener()

    async def start_ssdp_listener(self) -> None:
        """Listen for SSDP M-SEARCH broadcasts and initiate connection on discovery."""
        logger.info("Starting SSDP listener on 239.255.255.250:1900")

        if self.ssdp_sock:
            with contextlib.suppress(Exception):
                self.ssdp_sock.close()
            self.ssdp_sock = None

        self.ssdp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with contextlib.suppress(AttributeError):
            self.ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.ssdp_sock.bind(("", 1900))

        membership = struct.pack("4sL", socket.inet_aton("239.255.255.250"), socket.INADDR_ANY)
        self.ssdp_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        self.ssdp_sock.setblocking(False)

        loop = asyncio.get_event_loop()
        logger.info("Waiting for SSDP discovery broadcast…")

        while True:
            try:
                data, addr = await loop.sock_recvfrom(self.ssdp_sock, 1024)
                message = data.decode("utf-8", errors="ignore")

                if "M-SEARCH" in message and "urn:qretprop:espdevice:1" in message:
                    logger.info(f"Received discovery from {addr[0]}")
                    if self.server_ip is None:
                        self.server_ip = addr[0]
                        await asyncio.sleep(0.5)
                        await self.connect_to_server()

            except asyncio.CancelledError:
                break
            except Exception as e:
                if "Resource temporarily unavailable" not in str(e):
                    logger.error(f"SSDP error: {e}")
                await asyncio.sleep(0.1)

    # ---------------------------------------------------------------------- #
    # TCP connection and config                                                #
    # ---------------------------------------------------------------------- #

    async def connect_to_server(self) -> None:
        """Open a TCP connection to server_ip:server_port and send CONFIG."""
        if self.sock is not None:
            logger.warning("Already connected to server")
            return

        logger.info(f"Connecting to {self.server_ip}:{self.server_port}")

        try:
            loop = asyncio.get_event_loop()
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setblocking(False)
            await loop.sock_connect(self.sock, (self.server_ip, self.server_port))
            logger.info("TCP connection established")

            await self.send_config()
            self.command_task = asyncio.create_task(self.handle_commands())

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            if self.sock:
                with contextlib.suppress(Exception):
                    self.sock.close()
            self.sock = None

    async def send_config(self) -> None:
        """Send the device CONFIG packet to the server."""
        config_json = json.dumps(self.config)
        packet = ConfigPacket.create(config_json)
        encoded = packet.encode()

        loop = asyncio.get_event_loop()
        await loop.sock_sendall(self.sock, encoded)

        self.config_sent.set()
        logger.info(f"Sent CONFIG ({len(encoded)} bytes)")

    # ---------------------------------------------------------------------- #
    # Command receive loop                                                     #
    # ---------------------------------------------------------------------- #

    async def handle_commands(self) -> None:
        """Listen for and handle commands from the server using length-based framing."""
        loop = asyncio.get_event_loop()
        buffer = b""

        logger.info("Listening for commands…")

        try:
            while True:
                data = await loop.sock_recv(self.sock, 4096)
                if not data:
                    logger.warning("Server disconnected")
                    break

                buffer += data

                while len(buffer) >= HEADER_SIZE:
                    try:
                        packet_len = get_packet_len(buffer)
                        if len(buffer) < packet_len:
                            break

                        packet_data = buffer[:packet_len]
                        packet = decode_packet_client(packet_data)

                        logger.debug(f"Decoded {packet.__class__.__name__} ({packet_len} bytes)")

                        if isinstance(packet, SimplePacket) and packet.packet_type == PacketType.TIMESYNC:
                            server_ts = packet.timestamp
                            device_ts = int(time.monotonic() * 1000) & 0xFFFFFFFF
                            self.timesync_offset = server_ts - device_ts
                            logger.info(f"TIMESYNC: locked to server (offset={self.timesync_offset} ms)")

                            ack = AckPacket.create(PacketType.TIMESYNC, packet.sequence)
                            ack.timestamp = self._get_adjusted_ts()
                            await loop.sock_sendall(self.sock, ack.encode())

                            self.timesync_received.set()

                        elif isinstance(packet, SimplePacket) and packet.packet_type == PacketType.ESTOP:
                            logger.warning("Received ESTOP — stopping stream and resetting state")
                            await self.handle_stream_stop()
                            self.reset_device_state(announce=True)

                        elif isinstance(packet, ControlPacket):
                            await self.handle_control_command(packet)

                        elif isinstance(packet, StreamStartPacket):
                            await self.handle_stream_start(packet)

                        elif isinstance(packet, SimplePacket) and packet.packet_type == PacketType.STREAM_STOP:
                            await self.handle_stream_stop(packet)

                        elif isinstance(packet, SimplePacket) and packet.packet_type == PacketType.GET_SINGLE:
                            await self.send_single_reading()

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
                        logger.error(f"Error decoding packet: {e}")
                        break

        except asyncio.CancelledError:
            logger.info("Command handler cancelled")
            raise
        except Exception as e:
            logger.error(f"Command handler error: {e}")
        finally:
            self.command_task = None
            task = asyncio.current_task()
            if self.sock is not None and not (task and task.cancelled()):
                await self.handle_server_disconnect()

    # ---------------------------------------------------------------------- #
    # Command handlers                                                         #
    # ---------------------------------------------------------------------- #

    async def handle_control_command(self, packet: ControlPacket) -> None:
        if self.sock is None:
            return

        loop = asyncio.get_event_loop()
        command_id = packet.command_id
        state = packet.command_state

        control = self._device_config.controls_by_id.get(command_id)
        if control is not None:
            state_str = "OPEN" if state == ControlState.OPEN else "CLOSED"
            self.valve_states[control.name] = state_str
            logger.info(f"Control: {control.name} → {state_str}")

            ack = AckPacket.create(PacketType.CONTROL, packet.sequence)
            ack.timestamp = self._get_adjusted_ts()
            await loop.sock_sendall(self.sock, ack.encode())

            self.control_handled.set()
        else:
            logger.error(f"Invalid command_id: {command_id}")
            nack = NackPacket.create(PacketType.CONTROL, packet.sequence, ErrorCode.INVALID_ID)
            nack.timestamp = self._get_adjusted_ts()
            await loop.sock_sendall(self.sock, nack.encode())

    async def handle_stream_start(self, packet: StreamStartPacket) -> None:
        if self.sock is None:
            return

        self.stream_frequency = packet.frequency_hz
        self.streaming = True
        logger.info(f"Starting stream at {self.stream_frequency} Hz")

        loop = asyncio.get_event_loop()
        ack = AckPacket.create(PacketType.STREAM_START, packet.sequence)
        ack.timestamp = self._get_adjusted_ts()
        await loop.sock_sendall(self.sock, ack.encode())

        if self.stream_task:
            self.stream_task.cancel()
        self.stream_task = asyncio.create_task(self.stream_data())
        self.stream_started.set()

    async def handle_stream_stop(self, packet: SimplePacket | None = None) -> None:
        if self.sock is None:
            return

        self.streaming = False
        logger.info("Stopping stream")

        if self.stream_task:
            self.stream_task.cancel()
            self.stream_task = None

        loop = asyncio.get_event_loop()
        seq = packet.sequence if packet else 0
        ack = AckPacket.create(PacketType.STREAM_STOP, seq)
        ack.timestamp = self._get_adjusted_ts()
        await loop.sock_sendall(self.sock, ack.encode())
        self.stream_stopped.set()

    # ---------------------------------------------------------------------- #
    # Telemetry                                                                #
    # ---------------------------------------------------------------------- #

    async def stream_data(self) -> None:
        interval = 1.0 / self.stream_frequency
        next_send = time.monotonic()

        try:
            while self.streaming:
                now = time.monotonic()
                if now < next_send:
                    await asyncio.sleep(0)
                    continue
                await self.send_sensor_data()
                next_send += interval
                if time.monotonic() > next_send:
                    next_send = time.monotonic() + interval
        except asyncio.CancelledError:
            pass

    async def send_sensor_data(self) -> None:
        """Build and send a DATA packet over UDP with one reading per configured sensor."""
        readings: list[SensorReading] = []
        for sensor_id, sensor in self._device_config.sensors_by_id.items():
            self._sensor_values[sensor_id] = self._step_value(sensor, self._sensor_values[sensor_id])
            readings.append(
                SensorReading(
                    sensor_id=sensor_id,
                    unit=sensor.unit,
                    value=self._sensor_values[sensor_id],
                )
            )

        packet = DataPacket.create(readings)
        packet.timestamp = self._get_adjusted_ts()

        loop = asyncio.get_event_loop()
        await loop.sock_sendto(self.udp_sock, packet.encode(), (self.server_ip, self.server_udp_port))

        self.data_sent.set()
        self.data_sent.clear()  # reset immediately so it acts as a pulse for next waiter

        if random.random() < 0.1:
            summary = " ".join(
                f"{s.name}={self._sensor_values[sid]:.1f}{s.unit.name}"
                for sid, s in self._device_config.sensors_by_id.items()
            )
            logger.debug(f"Data: {summary}")

    async def send_single_reading(self) -> None:
        """Send one DATA packet over UDP in response to GET_SINGLE. No ACK is sent."""
        logger.info("Sending single reading")
        await self.send_sensor_data()

    # ---------------------------------------------------------------------- #
    # Status                                                                   #
    # ---------------------------------------------------------------------- #

    async def send_status(self) -> None:
        if self.sock is None:
            return

        control_states = [
            ControlStatus(
                id=control_id,
                state=(
                    ControlState.OPEN
                    if self.valve_states.get(control.name) == "OPEN"
                    else ControlState.CLOSED
                    if self.valve_states.get(control.name) == "CLOSED"
                    else ControlState.ERROR
                ),
            )
            for control_id, control in self._device_config.controls_by_id.items()
        ]

        status = StatusPacket.create(DeviceStatus.ACTIVE, control_states=control_states)
        status.timestamp = self._get_adjusted_ts()

        loop = asyncio.get_event_loop()
        await loop.sock_sendall(self.sock, status.encode())

        logger.info("Sent STATUS: ACTIVE")
        logger.info(
            "Control states: "
            + ", ".join(
                f"{c.name}={self.valve_states.get(c.name, 'UNKNOWN')}"
                for c in self._device_config.controls_by_id.values()
            )
        )

    # ---------------------------------------------------------------------- #
    # CLI run loop                                                             #
    # ---------------------------------------------------------------------- #

    async def run(self) -> None:
        """Run as a CLI device until KeyboardInterrupt.

        Connects directly if server_ip is given, otherwise waits for SSDP discovery
        and auto-reconnects after disconnects.
        """
        logger.info("=== Mock Sensor Device Started ===")
        logger.info(f"Device name: {self.device_name}")
        logger.info(
            "Sensors: "
            + ", ".join(
                f"{s.name} ({s.unit.name})"
                for s in self._device_config.sensors_by_id.values()
            )
        )
        logger.info(
            "Controls: " + ", ".join(c.name for c in self._device_config.controls_by_id.values())
        )

        if self.server_ip:
            logger.info(f"Connecting directly to {self.server_ip}")
            await self.connect_to_server()
        else:
            self._auto_reconnect = True
            logger.info("Waiting for server discovery…")
            self.ensure_ssdp_listener()

        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            await self.stop()

        logger.info("=== Mock Device Stopped ===")


# --------------------------------------------------------------------------- #
# CLI entry point                                                               #
# --------------------------------------------------------------------------- #


class _ColoredFormatter(logging.Formatter):
    """Logging formatter that adds ANSI colour codes by log level."""

    _COLORS = {
        logging.DEBUG: "\033[96m",
        logging.INFO: "\033[94m",
        logging.WARNING: "\033[93m",
        logging.ERROR: "\033[91m",
        logging.CRITICAL: "\033[91m",
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelno, "")
        return f"{color}{super().format(record)}{self._RESET}"


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Mock ESP32 sensor device for protocol testing")
    parser.add_argument("--server", "-s", help="Server IP address (default: auto-discover)")
    parser.add_argument("--name", "-n", default="MockDevice", help="Device name")
    args = parser.parse_args()

    # Configure coloured console logging for CLI use.
    handler = logging.StreamHandler()
    handler.setFormatter(
        _ColoredFormatter(
            fmt="%(asctime)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger("qretproptools")
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    device = MockSensorDevice(device_name=args.name, server_ip=args.server)
    await device.run()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n\nStopped by user")


if __name__ == "__main__":
    main()

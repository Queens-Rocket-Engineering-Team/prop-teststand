"""Binary TCP protocol for launch panel device communication.

This module implements a binary protocol to replace string-based commands.
All devices communicate with the central server using structured binary packets.

Protocol Overview:
- All packets start with a common header (packet_type, flags, timestamp, version)
- Specific packet types add additional fields
- Uses struct module for binary packing/unpacking
- Big-endian byte order (network byte order)
"""

import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import ClassVar


# ============================================================================
# PROTOCOL CONSTANTS
# ============================================================================

MAGIC_NUMBER = 0x5  # 4-bit magic number (0101 binary) - used to identify QRET Propulsion packets


class PacketType(IntEnum):
    """Enumeration of all packet types in the protocol."""

    # EMERGENCY - Highest priority
    ESTOP = 0x00          # Emergency stop ALL - immediately halt all operations

    # Server -> Device Commands
    DISCOVERY = 0x01      # Discovery request broadcast
    TIMESYNC = 0x02       # Time synchronization
    CONTROL = 0x03        # Control command (valve, etc.)
    STATUS_REQUEST = 0x04 # Request device status
    STREAM_START = 0x05   # Start streaming data
    STREAM_STOP = 0x06    # Stop streaming data
    GET_SINGLE = 0x07     # Request single data point
    HEARTBEAT = 0x08      # Keep-alive heartbeat

    # Device -> Server Responses
    CONFIG = 0x10         # Device configuration (JSON payload)
    DATA = 0x11           # Sensor data packet
    STATUS = 0x12         # Device status response
    ACK = 0x13            # Acknowledgment
    NACK = 0x14           # Negative acknowledgment (error)


class DeviceStatus(IntEnum):
    """Device operational status codes."""
    INACTIVE = 0
    ACTIVE = 1
    ERROR = 2
    CALIBRATING = 3


class ControlState(IntEnum):
    """Control state for valves and other binary actuators."""
    CLOSED = 0
    OPEN = 1
    ERROR = 255


class Unit(IntEnum):
    """Units for sensor measurements."""
    VOLTS = 0x00
    AMPS = 0x01
    CELSIUS = 0x02
    FAHRENHEIT = 0x03
    KELVIN = 0x04
    PSI = 0x05
    BAR = 0x06
    PASCAL = 0x07
    GRAMS = 0x08
    KILOGRAMS = 0x09
    POUNDS = 0x0A
    NEWTONS = 0x0B
    SECONDS = 0x0C
    MILLISECONDS = 0x0D
    HERTZ = 0x0E
    PERCENT = 0x0F
    UNITLESS = 0xFF


# ============================================================================
# PACKET STRUCTURES
# ============================================================================

PROTOCOL_VERSION = 1  # Current protocol version (0-15)


@dataclass
class PacketHeader:
    """Common header present in all packets.

    Format: >BBI (6 bytes total)
    - byte 0: magic (4 bits high) + version (4 bits low)
    - byte 1: packet_type (1 byte)
    - bytes 2-5: timestamp (4 bytes, milliseconds since midnight)
    """
    STRUCT_FORMAT: ClassVar[str] = ">BBI"
    SIZE: ClassVar[int] = struct.calcsize(STRUCT_FORMAT)

    magic: int    # 4 bits - protocol identifier
    version: int  # 4 bits - protocol version (0-15)
    packet_type: PacketType
    timestamp: int  # ms since midnight

    def pack(self) -> bytes:
        """Pack header into binary format."""
        # Combine magic (high 4 bits) and version (low 4 bits) into one byte
        magic_version_byte = ((self.magic & 0x0F) << 4) | (self.version & 0x0F)

        return struct.pack(
            self.STRUCT_FORMAT,
            magic_version_byte,
            self.packet_type,
            self.timestamp
        )

    @classmethod
    def unpack(cls, data: bytes) -> "PacketHeader":
        """Unpack binary data into PacketHeader."""
        if len(data) < cls.SIZE:
            raise ValueError(f"Insufficient data for header: {len(data)} < {cls.SIZE}")

        magic_version_byte, packet_type, timestamp = struct.unpack(cls.STRUCT_FORMAT, data[:cls.SIZE])

        # Extract magic (high 4 bits) and version (low 4 bits)
        magic = (magic_version_byte >> 4) & 0x0F
        version = magic_version_byte & 0x0F

        if magic != MAGIC_NUMBER:
            raise ValueError(f"Invalid magic number: {magic:#x} != {MAGIC_NUMBER:#x}")

        return cls(
            magic=magic,
            version=version,
            packet_type=PacketType(packet_type),
            timestamp=timestamp
        )


# ============================================================================
# SPECIFIC PACKET TYPES
# ============================================================================

@dataclass
class DiscoveryPacket:
    """Discovery packet sent by server to find devices on network.

    Total size: 10 bytes (header only)
    """
    header: PacketHeader

    def pack(self) -> bytes:
        """Pack discovery packet."""
        return self.header.pack()

    @classmethod
    def create(cls) -> "DiscoveryPacket":
        """Create a new discovery packet."""
        header = PacketHeader(
            magic=MAGIC_NUMBER,
            version=PROTOCOL_VERSION,
            packet_type=PacketType.DISCOVERY,
            timestamp=_get_timestamp_ms()
        )
        return cls(header=header)

    @classmethod
    def unpack(cls, data: bytes) -> "DiscoveryPacket":
        """Unpack binary data into DiscoveryPacket."""
        header = PacketHeader.unpack(data)
        return cls(header=header)


@dataclass
class TimeSyncPacket:
    """Time synchronization packet.

    Format: header + >Q (8 bytes)
    Total size: 18 bytes
    - server_time_ms: 8 bytes (absolute time in ms since epoch)
    """
    PAYLOAD_FORMAT: ClassVar[str] = ">Q"

    header: PacketHeader
    server_time_ms: int  # milliseconds since Unix epoch

    def pack(self) -> bytes:
        """Pack time sync packet."""
        return self.header.pack() + struct.pack(self.PAYLOAD_FORMAT, self.server_time_ms)

    @classmethod
    def create(cls, server_time_ms: int | None = None) -> "TimeSyncPacket":
        """Create a new time sync packet."""
        if server_time_ms is None:
            server_time_ms = int(time.time() * 1000)

        header = PacketHeader(
            magic=MAGIC_NUMBER,
            version=PROTOCOL_VERSION,
            packet_type=PacketType.TIMESYNC,
            timestamp=_get_timestamp_ms()
        )
        return cls(header=header, server_time_ms=server_time_ms)

    @classmethod
    def unpack(cls, data: bytes) -> "TimeSyncPacket":
        """Unpack binary data into TimeSyncPacket."""
        header = PacketHeader.unpack(data)
        payload_start = PacketHeader.SIZE
        server_time_ms, = struct.unpack(cls.PAYLOAD_FORMAT, data[payload_start:payload_start+8])
        return cls(header=header, server_time_ms=server_time_ms)


@dataclass
class ControlPacket:
    """Control command packet (e.g., valve open/close).

    Format: header + >BB (2 bytes, no padding)
    Total size: 8 bytes
    - command_id: 1 byte (index in device's control array)
    - command_state: 1 byte (ControlState enum)
    """
    PAYLOAD_FORMAT: ClassVar[str] = ">BB"

    header: PacketHeader
    command_id: int  # Index in device's configuration
    command_state: ControlState

    def pack(self) -> bytes:
        """Pack control packet."""
        return self.header.pack() + struct.pack(
            self.PAYLOAD_FORMAT,
            self.command_id,
            self.command_state
        )

    @classmethod
    def create(cls, command_id: int, command_state: ControlState) -> "ControlPacket":
        """Create a new control packet."""
        header = PacketHeader(
            magic=MAGIC_NUMBER,
            version=PROTOCOL_VERSION,
            packet_type=PacketType.CONTROL,
            timestamp=_get_timestamp_ms()
        )
        return cls(header=header, command_id=command_id, command_state=command_state)

    @classmethod
    def unpack(cls, data: bytes) -> "ControlPacket":
        """Unpack binary data into ControlPacket."""
        header = PacketHeader.unpack(data)
        payload_start = PacketHeader.SIZE
        command_id, command_state = struct.unpack(cls.PAYLOAD_FORMAT, data[payload_start:payload_start+2])
        return cls(header=header, command_id=command_id, command_state=ControlState(command_state))


@dataclass
class DataPacket:
    """Sensor data packet from device to server.

    Format: header + >BBf (6 bytes, no padding)
    Total size: 12 bytes
    - sensor_id: 1 byte (index in device's sensor array, max 255 sensors)
    - unit: 1 byte (Unit enum)
    - data: 4 bytes (32-bit float)
    """
    PAYLOAD_FORMAT: ClassVar[str] = ">BBf"

    header: PacketHeader
    sensor_id: int  # Index in device's configuration
    unit: Unit  # Unit of measurement
    data: float  # 32-bit float sensor reading

    def pack(self) -> bytes:
        """Pack data packet."""
        return self.header.pack() + struct.pack(
            self.PAYLOAD_FORMAT,
            self.sensor_id,
            self.unit,
            self.data
        )

    @classmethod
    def create(cls, sensor_id: int, data: float, unit: Unit = Unit.UNITLESS) -> "DataPacket":
        """Create a new data packet."""
        header = PacketHeader(
            magic=MAGIC_NUMBER,
            version=PROTOCOL_VERSION,
            packet_type=PacketType.DATA,
            timestamp=_get_timestamp_ms()
        )
        return cls(header=header, sensor_id=sensor_id, unit=unit, data=data)

    @classmethod
    def unpack(cls, data: bytes) -> "DataPacket":
        """Unpack binary data into DataPacket."""
        header = PacketHeader.unpack(data)
        payload_start = PacketHeader.SIZE
        sensor_id, unit, data_value = struct.unpack(cls.PAYLOAD_FORMAT, data[payload_start:payload_start+6])
        return cls(header=header, sensor_id=sensor_id, unit=Unit(unit), data=data_value)


@dataclass
class StatusPacket:
    """Device status packet.

    Format: header + >B (1 byte, no padding)
    Total size: 7 bytes
    - status: 1 byte (DeviceStatus enum)
    """
    PAYLOAD_FORMAT: ClassVar[str] = ">B"

    header: PacketHeader
    status: DeviceStatus

    def pack(self) -> bytes:
        """Pack status packet."""
        return self.header.pack() + struct.pack(self.PAYLOAD_FORMAT, self.status)

    @classmethod
    def create(cls, status: DeviceStatus) -> "StatusPacket":
        """Create a new status packet."""
        header = PacketHeader(
            magic=MAGIC_NUMBER,
            version=PROTOCOL_VERSION,
            packet_type=PacketType.STATUS,
            timestamp=_get_timestamp_ms()
        )
        return cls(header=header, status=status)

    @classmethod
    def unpack(cls, data: bytes) -> "StatusPacket":
        """Unpack binary data into StatusPacket."""
        header = PacketHeader.unpack(data)
        payload_start = PacketHeader.SIZE
        status, = struct.unpack(cls.PAYLOAD_FORMAT, data[payload_start:payload_start+1])
        return cls(header=header, status=DeviceStatus(status))


@dataclass
class ConfigPacket:
    """Device configuration packet (contains JSON payload).

    Format: header + >I + variable length JSON
    Total size: 16 + len(config_json) bytes
    - json_length: 4 bytes
    - config_json: variable (UTF-8 encoded JSON string)
    """
    LENGTH_FORMAT: ClassVar[str] = ">I"

    header: PacketHeader
    config_json: str  # JSON configuration as string

    def pack(self) -> bytes:
        """Pack config packet."""
        json_bytes = self.config_json.encode('utf-8')
        json_length = len(json_bytes)

        return (
            self.header.pack() +
            struct.pack(self.LENGTH_FORMAT, json_length) +
            json_bytes
        )

    @classmethod
    def create(cls, config_json: str) -> "ConfigPacket":
        """Create a new config packet."""
        header = PacketHeader(
            magic=MAGIC_NUMBER,
            version=PROTOCOL_VERSION,
            packet_type=PacketType.CONFIG,
            timestamp=_get_timestamp_ms()
        )
        return cls(header=header, config_json=config_json)

    @classmethod
    def unpack(cls, data: bytes) -> "ConfigPacket":
        """Unpack binary data into ConfigPacket."""
        header = PacketHeader.unpack(data)
        payload_start = PacketHeader.SIZE
        json_length, = struct.unpack(cls.LENGTH_FORMAT, data[payload_start:payload_start+4])

        json_start = payload_start + 4
        json_end = json_start + json_length
        config_json = data[json_start:json_end].decode('utf-8')

        return cls(header=header, config_json=config_json)


@dataclass
class StreamStartPacket:
    """Start streaming data at specified frequency.

    Format: header + >B (1 byte, no padding)
    Total size: 7 bytes
    - frequency_hz: 1 byte (samples per second, max 255 Hz)
    """
    PAYLOAD_FORMAT: ClassVar[str] = ">B"

    header: PacketHeader
    frequency_hz: int

    def pack(self) -> bytes:
        """Pack stream start packet."""
        return self.header.pack() + struct.pack(self.PAYLOAD_FORMAT, self.frequency_hz)

    @classmethod
    def create(cls, frequency_hz: int) -> "StreamStartPacket":
        """Create a new stream start packet."""
        header = PacketHeader(
            magic=MAGIC_NUMBER,
            version=PROTOCOL_VERSION,
            packet_type=PacketType.STREAM_START,
            timestamp=_get_timestamp_ms()
        )
        return cls(header=header, frequency_hz=frequency_hz)

    @classmethod
    def unpack(cls, data: bytes) -> "StreamStartPacket":
        """Unpack binary data into StreamStartPacket."""
        header = PacketHeader.unpack(data)
        payload_start = PacketHeader.SIZE
        frequency_hz, = struct.unpack(cls.PAYLOAD_FORMAT, data[payload_start:payload_start+1])
        return cls(header=header, frequency_hz=frequency_hz)


@dataclass
class SimplePacket:
    """Simple packet with no additional payload (heartbeat, stop, etc.).

    Total size: 10 bytes (header only)
    """
    header: PacketHeader

    def pack(self) -> bytes:
        """Pack simple packet."""
        return self.header.pack()

    @classmethod
    def create(cls, packet_type: PacketType) -> "SimplePacket":
        """Create a new simple packet of the specified type."""
        header = PacketHeader(
            magic=MAGIC_NUMBER,
            version=PROTOCOL_VERSION,
            packet_type=packet_type,
            timestamp=_get_timestamp_ms()
        )
        return cls(header=header)

    @classmethod
    def unpack(cls, data: bytes) -> "SimplePacket":
        """Unpack binary data into SimplePacket."""
        header = PacketHeader.unpack(data)
        return cls(header=header)


@dataclass
class AckPacket:
    """Acknowledgment packet with optional error code.

    Format: header + >B (1 byte, no padding)
    Total size: 7 bytes
    - ack_packet_type: 1 byte (type of packet being acknowledged)
    """
    PAYLOAD_FORMAT: ClassVar[str] = ">B"

    header: PacketHeader
    ack_packet_type: PacketType  # Which packet type is being acknowledged

    def pack(self) -> bytes:
        """Pack ACK packet."""
        return self.header.pack() + struct.pack(self.PAYLOAD_FORMAT, self.ack_packet_type)

    @classmethod
    def create(cls, ack_packet_type: PacketType, is_nack: bool = False) -> "AckPacket":
        """Create a new ACK or NACK packet."""
        pkt_type = PacketType.NACK if is_nack else PacketType.ACK
        header = PacketHeader(
            magic=MAGIC_NUMBER,
            version=PROTOCOL_VERSION,
            packet_type=pkt_type,
            timestamp=_get_timestamp_ms()
        )
        return cls(header=header, ack_packet_type=ack_packet_type)

    @classmethod
    def unpack(cls, data: bytes) -> "AckPacket":
        """Unpack binary data into AckPacket."""
        header = PacketHeader.unpack(data)
        payload_start = PacketHeader.SIZE
        ack_packet_type, = struct.unpack(cls.PAYLOAD_FORMAT, data[payload_start:payload_start+1])
        return cls(header=header, ack_packet_type=PacketType(ack_packet_type))


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _get_timestamp_ms() -> int:
    """Get milliseconds since midnight (local time).

    Returns:
        Milliseconds since midnight as integer.
    """
    now = time.time()
    midnight = now - (now % 86400)  # 86400 seconds in a day
    return int((now - midnight) * 1000)


def decode_packet(data: bytes):
    """Decode a binary packet into the appropriate packet object.

    Args:
        data: Binary packet data

    Returns:
        Appropriate packet object based on packet type

    Raises:
        ValueError: If packet type is unknown or data is invalid
    """
    if len(data) < PacketHeader.SIZE:
        raise ValueError(f"Packet too small: {len(data)} bytes")

    header = PacketHeader.unpack(data)

    # Map packet types to their classes
    packet_map = {
        PacketType.ESTOP: SimplePacket,
        PacketType.DISCOVERY: DiscoveryPacket,
        PacketType.TIMESYNC: TimeSyncPacket,
        PacketType.CONTROL: ControlPacket,
        PacketType.DATA: DataPacket,
        PacketType.STATUS: StatusPacket,
        PacketType.CONFIG: ConfigPacket,
        PacketType.STREAM_START: StreamStartPacket,
        PacketType.STREAM_STOP: SimplePacket,
        PacketType.GET_SINGLE: SimplePacket,
        PacketType.HEARTBEAT: SimplePacket,
        PacketType.STATUS_REQUEST: SimplePacket,
        PacketType.ACK: AckPacket,
        PacketType.NACK: AckPacket,
    }

    packet_class = packet_map.get(header.packet_type)
    if packet_class is None:
        raise ValueError(f"Unknown packet type: {header.packet_type}")

    return packet_class.unpack(data)


def get_packet_size(packet_type: PacketType) -> int:
    """Get the expected size of a packet type.

    Args:
        packet_type: Type of packet

    Returns:
        Expected packet size in bytes (0 for variable-length packets like CONFIG)
    """
    sizes = {
        PacketType.ESTOP: 6,              # Header only - EMERGENCY
        PacketType.DISCOVERY: 6,          # Header only
        PacketType.TIMESYNC: 14,          # Header + 8 bytes
        PacketType.CONTROL: 8,            # Header + 2 bytes
        PacketType.DATA: 12,              # Header + 6 bytes
        PacketType.STATUS: 7,             # Header + 1 byte
        PacketType.CONFIG: 0,             # Variable length
        PacketType.STREAM_START: 7,       # Header + 1 byte
        PacketType.STREAM_STOP: 6,        # Header only
        PacketType.GET_SINGLE: 6,         # Header only
        PacketType.HEARTBEAT: 6,          # Header only
        PacketType.STATUS_REQUEST: 6,     # Header only
        PacketType.ACK: 7,                # Header + 1 byte
        PacketType.NACK: 7,               # Header + 1 byte
    }
    return sizes.get(packet_type, 0)


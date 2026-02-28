"""Binary TCP protocol v2 for launch panel device communication.

All devices communicate with the central server using structured binary packets.
Big-endian byte order (network byte order). All packets share a 9-byte header
with a LENGTH field for trivial TCP framing.
"""

import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import ClassVar


# ============================================================================
# PROTOCOL CONSTANTS
# ============================================================================

PROTOCOL_VERSION = 2


class PacketType(IntEnum):
    # Emergency
    ESTOP = 0x00

    # Server -> Device
    DISCOVERY = 0x01
    TIMESYNC = 0x02
    CONTROL = 0x03
    STATUS_REQUEST = 0x04
    STREAM_START = 0x05
    STREAM_STOP = 0x06
    GET_SINGLE = 0x07
    HEARTBEAT = 0x08

    # Device -> Server
    CONFIG = 0x10
    DATA = 0x11
    STATUS = 0x12
    ACK = 0x13
    NACK = 0x14


class DeviceStatus(IntEnum):
    INACTIVE = 0
    ACTIVE = 1
    ERROR = 2
    CALIBRATING = 3


class ControlState(IntEnum):
    CLOSED = 0
    OPEN = 1
    ERROR = 255


class Unit(IntEnum):
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
    OHMS = 0x0F
    UNITLESS = 0xFF


class ErrorCode(IntEnum):
    NONE = 0x00
    UNKNOWN_TYPE = 0x01
    INVALID_ID = 0x02
    HARDWARE_FAULT = 0x03
    BUSY = 0x04
    NOT_STREAMING = 0x05
    INVALID_PARAM = 0x06


# ============================================================================
# PACKET HEADER (9 bytes)
# ============================================================================

_sequence_counter = 0


def _next_sequence() -> int:
    global _sequence_counter
    seq = _sequence_counter
    _sequence_counter = (_sequence_counter + 1) & 0xFF
    return seq


def _get_timestamp_ms() -> int:
    return int(time.monotonic() * 1000)


@dataclass
class PacketHeader:
    """Common 9-byte header for all packets.

    Format: >BBBHI
    Byte 0:    version   (uint8)
    Byte 1:    packet_type (uint8)
    Byte 2:    sequence  (uint8)
    Bytes 3-4: length    (uint16) - total packet size including header
    Bytes 5-8: timestamp (uint32) - ms since boot/session
    """
    STRUCT_FORMAT: ClassVar[str] = ">BBBHI"
    SIZE: ClassVar[int] = struct.calcsize(">BBBHI")  # 9

    version: int
    packet_type: PacketType
    sequence: int
    length: int
    timestamp: int

    def pack(self) -> bytes:
        return struct.pack(
            self.STRUCT_FORMAT,
            self.version,
            self.packet_type,
            self.sequence,
            self.length,
            self.timestamp,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "PacketHeader":
        if len(data) < cls.SIZE:
            raise ValueError(f"Insufficient data for header: {len(data)} < {cls.SIZE}")

        version, packet_type, sequence, length, timestamp = struct.unpack(
            cls.STRUCT_FORMAT, data[:cls.SIZE]
        )

        return cls(
            version=version,
            packet_type=PacketType(packet_type),
            sequence=sequence,
            length=length,
            timestamp=timestamp,
        )


def _make_header(packet_type: PacketType, total_length: int, sequence: int | None = None) -> PacketHeader:
    if sequence is None:
        sequence = _next_sequence()
    return PacketHeader(
        version=PROTOCOL_VERSION,
        packet_type=packet_type,
        sequence=sequence,
        length=total_length,
        timestamp=_get_timestamp_ms(),
    )


# ============================================================================
# PACKET TYPES
# ============================================================================

@dataclass
class SimplePacket:
    """Header-only packet (9 bytes). Used for ESTOP, DISCOVERY, HEARTBEAT,
    STREAM_STOP, GET_SINGLE, STATUS_REQUEST."""
    header: PacketHeader

    def pack(self) -> bytes:
        return self.header.pack()

    @classmethod
    def create(cls, packet_type: PacketType) -> "SimplePacket":
        header = _make_header(packet_type, PacketHeader.SIZE)
        return cls(header=header)

    @classmethod
    def unpack(cls, data: bytes) -> "SimplePacket":
        header = PacketHeader.unpack(data)
        return cls(header=header)


# Keep DiscoveryPacket as an alias for clarity
DiscoveryPacket = SimplePacket


@dataclass
class StatusPacket:
    """Device status. Header + 1 byte status. Total: 10 bytes."""
    PAYLOAD_FORMAT: ClassVar[str] = ">B"

    header: PacketHeader
    status: DeviceStatus

    def pack(self) -> bytes:
        return self.header.pack() + struct.pack(self.PAYLOAD_FORMAT, self.status)

    @classmethod
    def create(cls, status: DeviceStatus) -> "StatusPacket":
        header = _make_header(PacketType.STATUS, PacketHeader.SIZE + 1)
        return cls(header=header, status=status)

    @classmethod
    def unpack(cls, data: bytes) -> "StatusPacket":
        header = PacketHeader.unpack(data)
        s = PacketHeader.SIZE
        status, = struct.unpack(cls.PAYLOAD_FORMAT, data[s:s + 1])
        return cls(header=header, status=DeviceStatus(status))


@dataclass
class StreamStartPacket:
    """Start streaming. Header + 2 bytes frequency_hz (uint16). Total: 11 bytes."""
    PAYLOAD_FORMAT: ClassVar[str] = ">H"

    header: PacketHeader
    frequency_hz: int

    def pack(self) -> bytes:
        return self.header.pack() + struct.pack(self.PAYLOAD_FORMAT, self.frequency_hz)

    @classmethod
    def create(cls, frequency_hz: int) -> "StreamStartPacket":
        header = _make_header(PacketType.STREAM_START, PacketHeader.SIZE + 2)
        return cls(header=header, frequency_hz=frequency_hz)

    @classmethod
    def unpack(cls, data: bytes) -> "StreamStartPacket":
        header = PacketHeader.unpack(data)
        s = PacketHeader.SIZE
        frequency_hz, = struct.unpack(cls.PAYLOAD_FORMAT, data[s:s + 2])
        return cls(header=header, frequency_hz=frequency_hz)


@dataclass
class ControlPacket:
    """Control command. Header + command_id (1B) + command_state (1B). Total: 11 bytes."""
    PAYLOAD_FORMAT: ClassVar[str] = ">BB"

    header: PacketHeader
    command_id: int
    command_state: ControlState

    def pack(self) -> bytes:
        return self.header.pack() + struct.pack(
            self.PAYLOAD_FORMAT, self.command_id, self.command_state
        )

    @classmethod
    def create(cls, command_id: int, command_state: ControlState) -> "ControlPacket":
        header = _make_header(PacketType.CONTROL, PacketHeader.SIZE + 2)
        return cls(header=header, command_id=command_id, command_state=command_state)

    @classmethod
    def unpack(cls, data: bytes) -> "ControlPacket":
        header = PacketHeader.unpack(data)
        s = PacketHeader.SIZE
        command_id, command_state = struct.unpack(cls.PAYLOAD_FORMAT, data[s:s + 2])
        return cls(header=header, command_id=command_id, command_state=ControlState(command_state))


@dataclass
class AckPacket:
    """ACK packet. Header + ack_packet_type (1B) + ack_sequence (1B) + error_code (1B).
    Total: 12 bytes. error_code is always 0x00 for ACK."""
    PAYLOAD_FORMAT: ClassVar[str] = ">BBB"

    header: PacketHeader
    ack_packet_type: PacketType
    ack_sequence: int
    error_code: ErrorCode

    def pack(self) -> bytes:
        return self.header.pack() + struct.pack(
            self.PAYLOAD_FORMAT, self.ack_packet_type, self.ack_sequence, self.error_code
        )

    @classmethod
    def create(cls, ack_packet_type: PacketType, ack_sequence: int = 0) -> "AckPacket":
        header = _make_header(PacketType.ACK, PacketHeader.SIZE + 3)
        return cls(
            header=header,
            ack_packet_type=ack_packet_type,
            ack_sequence=ack_sequence,
            error_code=ErrorCode.NONE,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "AckPacket":
        header = PacketHeader.unpack(data)
        s = PacketHeader.SIZE
        ack_type, ack_seq, err = struct.unpack(cls.PAYLOAD_FORMAT, data[s:s + 3])
        return cls(
            header=header,
            ack_packet_type=PacketType(ack_type),
            ack_sequence=ack_seq,
            error_code=ErrorCode(err),
        )


@dataclass
class NackPacket:
    """NACK packet. Header + nack_packet_type (1B) + nack_sequence (1B) + error_code (1B).
    Total: 12 bytes."""
    PAYLOAD_FORMAT: ClassVar[str] = ">BBB"

    header: PacketHeader
    nack_packet_type: PacketType
    nack_sequence: int
    error_code: ErrorCode

    def pack(self) -> bytes:
        return self.header.pack() + struct.pack(
            self.PAYLOAD_FORMAT, self.nack_packet_type, self.nack_sequence, self.error_code
        )

    @classmethod
    def create(cls, nack_packet_type: PacketType, nack_sequence: int = 0,
               error_code: ErrorCode = ErrorCode.UNKNOWN_TYPE) -> "NackPacket":
        header = _make_header(PacketType.NACK, PacketHeader.SIZE + 3)
        return cls(
            header=header,
            nack_packet_type=nack_packet_type,
            nack_sequence=nack_sequence,
            error_code=error_code,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "NackPacket":
        header = PacketHeader.unpack(data)
        s = PacketHeader.SIZE
        nack_type, nack_seq, err = struct.unpack(cls.PAYLOAD_FORMAT, data[s:s + 3])
        return cls(
            header=header,
            nack_packet_type=PacketType(nack_type),
            nack_sequence=nack_seq,
            error_code=ErrorCode(err),
        )



@dataclass
class SensorReading:
    """A single sensor reading within a batched DATA packet."""
    sensor_id: int
    unit: Unit
    value: float


@dataclass
class DataPacket:
    """Batched sensor data. Header + count (1B) + [sensor_id (1B) + unit (1B) + value (4B float)] * N.
    Total: 10 + 6*N bytes."""
    READING_FORMAT: ClassVar[str] = ">BBf"
    READING_SIZE: ClassVar[int] = 6

    header: PacketHeader
    readings: list[SensorReading] = field(default_factory=list)

    def pack(self) -> bytes:
        payload = struct.pack(">B", len(self.readings))
        for r in self.readings:
            payload += struct.pack(self.READING_FORMAT, r.sensor_id, r.unit, r.value)
        return self.header.pack() + payload

    @classmethod
    def create(cls, readings: list[SensorReading] | None = None, *,
               sensor_id: int | None = None, data: float | None = None,
               unit: Unit = Unit.UNITLESS) -> "DataPacket":
        if readings is None:
            if sensor_id is not None and data is not None:
                readings = [SensorReading(sensor_id=sensor_id, unit=unit, value=data)]
            else:
                readings = []
        total = PacketHeader.SIZE + 1 + cls.READING_SIZE * len(readings)
        header = _make_header(PacketType.DATA, total)
        return cls(header=header, readings=readings)

    @classmethod
    def unpack(cls, data: bytes) -> "DataPacket":
        header = PacketHeader.unpack(data)
        s = PacketHeader.SIZE
        count, = struct.unpack(">B", data[s:s + 1])
        s += 1
        readings = []
        for _ in range(count):
            sid, unit_val, value = struct.unpack(cls.READING_FORMAT, data[s:s + cls.READING_SIZE])
            readings.append(SensorReading(sensor_id=sid, unit=Unit(unit_val), value=value))
            s += cls.READING_SIZE
        return cls(header=header, readings=readings)


@dataclass
class ConfigPacket:
    """Device configuration (JSON payload).
    Header + json_length (4B uint32) + json_data (UTF-8).
    Total: 13 + json_len bytes."""
    LENGTH_FORMAT: ClassVar[str] = ">I"

    header: PacketHeader
    config_json: str

    def pack(self) -> bytes:
        json_bytes = self.config_json.encode('utf-8')
        return (
            self.header.pack() +
            struct.pack(self.LENGTH_FORMAT, len(json_bytes)) +
            json_bytes
        )

    @classmethod
    def create(cls, config_json: str) -> "ConfigPacket":
        json_bytes = config_json.encode('utf-8')
        total = PacketHeader.SIZE + 4 + len(json_bytes)
        header = _make_header(PacketType.CONFIG, total)
        return cls(header=header, config_json=config_json)

    @classmethod
    def unpack(cls, data: bytes) -> "ConfigPacket":
        header = PacketHeader.unpack(data)
        s = PacketHeader.SIZE
        json_length, = struct.unpack(cls.LENGTH_FORMAT, data[s:s + 4])
        s += 4
        config_json = data[s:s + json_length].decode('utf-8')
        return cls(header=header, config_json=config_json)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def decode_packet(data: bytes):
    """Decode a binary packet from data. Uses the LENGTH field in the header
    to determine packet boundaries."""
    if len(data) < PacketHeader.SIZE:
        raise ValueError(f"Packet too small: {len(data)} bytes")

    header = PacketHeader.unpack(data)

    if len(data) < header.length:
        raise ValueError(f"Incomplete packet: have {len(data)}, need {header.length}")

    packet_data = data[:header.length]

    packet_map = {
        PacketType.ESTOP: SimplePacket,
        PacketType.DISCOVERY: SimplePacket,
        PacketType.HEARTBEAT: SimplePacket,
        PacketType.STREAM_STOP: SimplePacket,
        PacketType.GET_SINGLE: SimplePacket,
        PacketType.STATUS_REQUEST: SimplePacket,
        PacketType.STATUS: StatusPacket,
        PacketType.STREAM_START: StreamStartPacket,
        PacketType.CONTROL: ControlPacket,
        PacketType.ACK: AckPacket,
        PacketType.NACK: NackPacket,
        PacketType.TIMESYNC: SimplePacket,
        PacketType.DATA: DataPacket,
        PacketType.CONFIG: ConfigPacket,
    }

    packet_class = packet_map.get(header.packet_type)
    if packet_class is None:
        raise ValueError(f"Unknown packet type: {header.packet_type}")

    return packet_class.unpack(packet_data)

import time
from dataclasses import dataclass, field
from enum import IntEnum

from libqretprop._protocol._qlcp import lib as _lib, ffi as _ffi

# ============================================================================
# CONSTANTS
# ============================================================================

_MAX_CONTROLS = 32
_MAX_SENSORS  = 32
_MAX_CONFIG   = 8192 # Much larger than any expected config JSON
_ENCODE_BUF_SIZE = 8192  # Large enough for any packet type to avoid buffer-too-small errors on encoding

# Make sure the compiled C library actually exported the header-size constant.
# If this assert fails, the compiled extension or generated headers are out
# of sync with the shared library and we should rebuild the protocol artifacts.
if not hasattr(_lib, "QLCP_HEADER_SIZE"):
     raise RuntimeError(
         "QLCP_HEADER_SIZE missing from compiled qlcp library; rebuild required"
     )
HEADER_SIZE = int(_lib.QLCP_HEADER_SIZE)

# ============================================================================
# UTILS
# ============================================================================

class QLCPError(Exception):
    """Raised when an error occurs in the QLCP protocol."""

def _check(ret, context: str) -> None:
    """Check the return code from a cffi call and raise an exception if it's an error."""
    if ret == _lib.QLCP_OK:
        return
    names = {
        _lib.QLCP_NULL_PTR:           "null pointer",
        _lib.QLCP_NO_MEM:             "buffer too small",
        _lib.QLCP_LEN_MISMATCH:       "length mismatch",
        _lib.QLCP_VERSION_MISMATCH:   "protocol version mismatch",
        _lib.QLCP_INVALID_PACKET_TYPE:"invalid packet type",
    }
    raise QLCPError(f"{context}: {names.get(ret, f'unknown error {ret}')}")


def _encode_buf():
    """Helper to create a new encoding buffer and length pointer for encoding packets."""
    return _ffi.new(f"uint8_t[{_ENCODE_BUF_SIZE}]"), _ffi.new("size_t *", _ENCODE_BUF_SIZE)

def get_packet_len(data: bytes) -> int:
    """Get the total length of a QLCP packet from its header. Useful for determining how many bytes to read for a full packet."""
    buf = _ffi.from_buffer(data)
    data_len = _ffi.new("uint16_t *")
    _check(_lib.qlcp_get_packet_len(data_len, buf, len(data)), "get_packet_len")
    return int(data_len[0])


def peek_packet_type(data: bytes) -> int:
    """Peek the packet-type byte from a raw packet header.

    Accesses the packet type from raw bytes for fast-path packet type checks without a full decode

    Raises `QLCPError` if the provided buffer is too short to contain that byte.
    """
    if len(data) < 2:
        raise QLCPError(f"packet too small to peek type: {len(data)} bytes")
    # In QLCP v2 the packet-type is the second byte of the header
    return data[1]

# ============================================================================
# ENUMS
# ============================================================================

PacketType = IntEnum("PacketType", {
    "ESTOP":          _lib.QLCP_PT_ESTOP,
    "DISCOVERY":      _lib.QLCP_PT_DISCOVERY,
    "TIMESYNC":       _lib.QLCP_PT_TIMESYNC,
    "CONTROL":        _lib.QLCP_PT_CONTROL,
    "STATUS_REQUEST": _lib.QLCP_PT_STATUS_REQUEST,
    "STREAM_START":   _lib.QLCP_PT_STREAM_START,
    "STREAM_STOP":    _lib.QLCP_PT_STREAM_STOP,
    "GET_SINGLE":     _lib.QLCP_PT_GET_SINGLE,
    "HEARTBEAT":      _lib.QLCP_PT_HEARTBEAT,
    "CONFIG":         _lib.QLCP_PT_CONFIG,
    "DATA":           _lib.QLCP_PT_DATA,
    "STATUS":         _lib.QLCP_PT_STATUS,
    "ACK":            _lib.QLCP_PT_ACK,
    "NACK":           _lib.QLCP_PT_NACK,
})

DeviceStatus = IntEnum("DeviceStatus", {
    "INACTIVE":    _lib.QLCP_DS_INACTIVE,
    "ACTIVE":      _lib.QLCP_DS_ACTIVE,
    "ERROR":       _lib.QLCP_DS_ERROR,
    "CALIBRATING": _lib.QLCP_DS_CALIBRATING,
})

ControlState = IntEnum("ControlState", {
    "CLOSED": _lib.QLCP_CS_CLOSED,
    "OPEN":   _lib.QLCP_CS_OPEN,
    "ERROR":  _lib.QLCP_CS_ERROR,
})

Unit = IntEnum("Unit", {
    "VOLTS":        _lib.QLCP_UNIT_VOLTS,
    "AMPS":         _lib.QLCP_UNIT_AMPS,
    "CELSIUS":      _lib.QLCP_UNIT_CELSIUS,
    "FAHRENHEIT":   _lib.QLCP_UNIT_FAHRENHEIT,
    "KELVIN":       _lib.QLCP_UNIT_KELVIN,
    "PSI":          _lib.QLCP_UNIT_PSI,
    "BAR":          _lib.QLCP_UNIT_BAR,
    "PASCAL":       _lib.QLCP_UNIT_PASCAL,
    "GRAMS":        _lib.QLCP_UNIT_GRAMS,
    "KILOGRAMS":    _lib.QLCP_UNIT_KILOGRAMS,
    "POUNDS":       _lib.QLCP_UNIT_POUNDS,
    "NEWTONS":      _lib.QLCP_UNIT_NEWTONS,
    "SECONDS":      _lib.QLCP_UNIT_SECONDS,
    "MILLISECONDS": _lib.QLCP_UNIT_MILLISECONDS,
    "HERTZ":        _lib.QLCP_UNIT_HERTZ,
    "OHMS":         _lib.QLCP_UNIT_OHMS,
    "UNITLESS":     _lib.QLCP_UNIT_UNITLESS,
})

ErrorCode = IntEnum("ErrorCode", {
    "NONE":           _lib.QLCP_ERR_NONE,
    "UNKNOWN_TYPE":   _lib.QLCP_ERR_UNKNOWN_TYPE,
    "INVALID_ID":     _lib.QLCP_ERR_INVALID_ID,
    "HARDWARE_FAULT": _lib.QLCP_ERR_HARDWARE_FAULT,
    "BUSY":           _lib.QLCP_ERR_BUSY,
    "NOT_STREAMING":  _lib.QLCP_ERR_NOT_STREAMING,
    "INVALID_PARAM":  _lib.QLCP_ERR_INVALID_PARAM,
})

# ============================================================================
# SEQUENCE AND TIMESTAMP
# ============================================================================

_sequence_counter = 0


def _next_sequence() -> int:
    global _sequence_counter
    seq = _sequence_counter
    _sequence_counter = (_sequence_counter + 1) & 0xFF
    return seq

def _get_timestamp_ms() -> int:
    return int(time.monotonic() * 1000)

# ============================================================================
# PACKET TYPES AND ENCODING
# ============================================================================

@dataclass
class SimplePacket:
    """Header-only packet. Used for ESTOP, DISCOVERY, HEARTBEAT,
    TIMESYNC, STREAM_STOP, GET_SINGLE, STATUS_REQUEST."""
    packet_type: PacketType
    sequence: int
    timestamp: int

    @classmethod
    def create(cls, packet_type: PacketType) -> "SimplePacket":
        return cls(
            packet_type=packet_type,
            sequence=_next_sequence(),
            timestamp=_get_timestamp_ms(),
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new("qlcp_header_only_packet *", {
            "sequence":  self.sequence,
            "timestamp": self.timestamp,
        })
        _check(
            _lib.qlcp_encode_header_only(buf, buf_len, self.packet_type, pkt),
            "encode_header_only"
        )
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class ControlStatus:
    id: int
    state: ControlState

@dataclass
class StatusPacket:
    """Device status + batched control states."""
    sequence: int
    timestamp: int
    status: DeviceStatus
    control_states: list[ControlStatus] = field(default_factory=list)

    @classmethod
    def create(cls, status: DeviceStatus, control_states: list[ControlStatus] | None = None) -> "StatusPacket":
        return cls(
            sequence=_next_sequence(),
            timestamp=_get_timestamp_ms(),
            status=status,
            control_states=control_states or [],
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        control_arr = _ffi.new(f"qlcp_control_data[{_MAX_CONTROLS}]")
        for i, ctrl in enumerate(self.control_states):
            if i >= _MAX_CONTROLS:
                raise QLCPError(f"too many controls in status packet: {len(self.control_states)} (max {_MAX_CONTROLS})")
            control_arr[i].control_id = ctrl.id
            control_arr[i].control_state = ctrl.state

        pkt = _ffi.new("qlcp_status_packet *", {
            "header": {"sequence": self.sequence, "timestamp": self.timestamp},
            "device_status": self.status,
            "control_data": control_arr,
            "control_count": min(len(self.control_states), _MAX_CONTROLS),
        })
        _check(
            _lib.qlcp_encode_status(buf, buf_len, pkt),
            "encode_status"
        )
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class StreamStartPacket:
    """Start streaming at the given frequency."""
    sequence: int
    timestamp: int
    frequency_hz: int

    @classmethod
    def create(cls, frequency_hz: int) -> "StreamStartPacket":
        return cls(
            sequence=_next_sequence(),
            timestamp=_get_timestamp_ms(),
            frequency_hz=frequency_hz,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new("qlcp_stream_start_packet *", {
            "header": {"sequence": self.sequence, "timestamp": self.timestamp},
            "stream_frequency": self.frequency_hz,
        })
        _check(
            _lib.qlcp_encode_stream_start(buf, buf_len, pkt),
            "encode_stream_start"
        )
        return bytes(_ffi.buffer(buf, buf_len[0]))

@dataclass
class ControlPacket:
    """Control command."""
    sequence: int
    timestamp: int
    command_id: int
    command_state: ControlState

    @classmethod
    def create(cls, command_id: int, command_state: ControlState) -> "ControlPacket":
        return cls(
            sequence=_next_sequence(),
            timestamp=_get_timestamp_ms(),
            command_id=command_id,
            command_state=command_state,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new("qlcp_control_packet *", {
            "header": {"sequence": self.sequence, "timestamp": self.timestamp},
            "command_id": self.command_id,
            "command_state": self.command_state,
        })
        _check(
            _lib.qlcp_encode_control(buf, buf_len, pkt),
            "encode_control"
        )
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class AckPacket:
    """ACK packet."""
    sequence: int
    timestamp: int
    ack_packet_type: PacketType
    ack_sequence: int

    @classmethod
    def create(cls, ack_packet_type: PacketType, ack_sequence: int = 0) -> "AckPacket":
        return cls(
            sequence=_next_sequence(),
            timestamp=_get_timestamp_ms(),
            ack_packet_type=ack_packet_type,
            ack_sequence=ack_sequence,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new("qlcp_ack_packet *", {
            "header": {"sequence": self.sequence, "timestamp": self.timestamp},
            "ack_packet_type": self.ack_packet_type,
            "ack_sequence": self.ack_sequence,
        })
        _check(
            _lib.qlcp_encode_ack(buf, buf_len, pkt),
            "encode_ack"
        )
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class NackPacket:
    """NACK packet."""
    sequence: int
    timestamp: int
    nack_packet_type: PacketType
    nack_sequence: int
    error_code: ErrorCode

    @classmethod
    def create(cls, nack_packet_type: PacketType, nack_sequence: int = 0,
               error_code: ErrorCode = ErrorCode.UNKNOWN_TYPE) -> "NackPacket":
        return cls(
            sequence=_next_sequence(),
            timestamp=_get_timestamp_ms(),
            nack_packet_type=nack_packet_type,
            nack_sequence=nack_sequence,
            error_code=error_code,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new("qlcp_nack_packet *", {
            "header": {"sequence": self.sequence, "timestamp": self.timestamp},
            "nack_packet_type": self.nack_packet_type,
            "nack_sequence": self.nack_sequence,
            "nack_error_code": self.error_code,
        })
        _check(
            _lib.qlcp_encode_nack(buf, buf_len, pkt),
            "encode_nack"
        )
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class SensorReading:
    """A single sensor reading within a DATA packet."""
    sensor_id: int
    unit: Unit
    value: float


@dataclass
class DataPacket:
    """Batched sensor data."""
    sequence: int
    timestamp: int
    readings: list[SensorReading] = field(default_factory=list)

    @classmethod
    def create(cls, readings: list[SensorReading]) -> "DataPacket":
        return cls(
            sequence=_next_sequence(),
            timestamp=_get_timestamp_ms(),
            readings=readings,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        sensor_arr = _ffi.new(f"qlcp_sensor_data[{_MAX_SENSORS}]")
        for i, reading in enumerate(self.readings):
            if i >= _MAX_SENSORS:
                break
            sensor_arr[i].sensor_id = reading.sensor_id
            sensor_arr[i].unit = reading.unit
            sensor_arr[i].value = reading.value

        pkt = _ffi.new("qlcp_data_packet *", {
            "header": {"sequence": self.sequence, "timestamp": self.timestamp},
            "sensor_data": sensor_arr,
            "sensor_count": min(len(self.readings), _MAX_SENSORS),
        })
        _check(
            _lib.qlcp_encode_data(buf, buf_len, pkt),
            "encode_data"
        )
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class ConfigPacket:
    """Device configuration (JSON payload)."""
    sequence: int
    timestamp: int
    config_json: str

    @classmethod
    def create(cls, config_json: str) -> "ConfigPacket":
        return cls(
            sequence=_next_sequence(),
            timestamp=_get_timestamp_ms(),
            config_json=config_json,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        conf_bytes = self.config_json.encode()
        conf_buf = _ffi.new(f"char[{_MAX_CONFIG}]", conf_bytes)
        conf_buf_len = len(conf_bytes)

        if conf_buf_len > _MAX_CONFIG:
            raise QLCPError(f"config JSON too large: {conf_buf_len} bytes (max {_MAX_CONFIG})")

        pkt = _ffi.new("qlcp_config_packet *", {
            "header": {"sequence": self.sequence, "timestamp": self.timestamp},
            "config_data": conf_buf,
            "config_data_len": conf_buf_len,
        })
        _check(
            _lib.qlcp_encode_config(buf, buf_len, pkt),
            "encode_config"
        )
        return bytes(_ffi.buffer(buf, buf_len[0]))


# ============================================================================
# DECODING
# ============================================================================


# Reuse buffers for client->server decoding to avoid unncessary allocations on the critical data packet path
# These are only used in a single thread so it's safe to reuse
_ctrl_arr = _ffi.new(f"qlcp_control_data[{_MAX_CONTROLS}]")
_sens_arr = _ffi.new(f"qlcp_sensor_data[{_MAX_SENSORS}]")
_conf_buf = _ffi.new(f"char[{_MAX_CONFIG}]")
_buffers = _ffi.new("qlcp_server_payload_buffers *", {
    "control_data":     _ctrl_arr, "control_data_len": _MAX_CONTROLS,
    "sensor_data":      _sens_arr, "sensor_data_len":  _MAX_SENSORS,
    "config_data":      _conf_buf, "config_data_len":  _MAX_CONFIG,
})
_payload = _ffi.new("qlcp_server_payload *")

# Cache for converting unit integers to enums without constructing a new enum for every sensor reading
_unit_cache: dict[int, Unit] = {u.value: u for u in Unit}

ServerReceivedPacket = StatusPacket | DataPacket | ConfigPacket | AckPacket | NackPacket
ClientReceivedPacket = SimplePacket | ControlPacket | StreamStartPacket | AckPacket | NackPacket

def decode_packet_server(data: bytes) -> ServerReceivedPacket:
    """
    Decode a client->server packet. For use by the main server when receiving packets from devices.
    """
    if len(data) < HEADER_SIZE:
        raise QLCPError(f"packet too small: {len(data)} bytes")

    buf = _ffi.from_buffer(data)  # zero-copy

    _check(
        _lib.qlcp_decode_client_to_server(_payload, _buffers, buf, len(data)),
        "decode_packet"
    )

    return _server_payload_to_python(_payload)


def _server_payload_to_python(payload):
    """
    Convert a decoded C payload struct into a Python dataclass based on the packet type. Do not call this directly — use decode_packet_server() instead.
    """
    payload_type = payload.packet_type
    payload_data = payload.payload_data

    if payload_type == _lib.QLCP_PT_STATUS:
        return StatusPacket(
            sequence=payload_data.status.header.sequence,
            timestamp=payload_data.status.header.timestamp,
            status=DeviceStatus(payload_data.status.device_status),
            control_states=[
                ControlStatus(
                    id=payload_data.status.control_data[i].control_id,
                    state=ControlState(payload_data.status.control_data[i].control_state),
                )
                for i in range(payload_data.status.control_count)
            ],
        )
    elif payload_type == _lib.QLCP_PT_DATA:
        return DataPacket(
            sequence=payload_data.data.header.sequence,
            timestamp=payload_data.data.header.timestamp,
            readings=[
                SensorReading(
                    sensor_id=payload_data.data.sensor_data[i].sensor_id,
                    value=payload_data.data.sensor_data[i].value,
                    unit=_unit_cache[payload_data.data.sensor_data[i].unit],
                )
                for i in range(payload_data.data.sensor_count)
            ],
        )
    elif payload_type == _lib.QLCP_PT_CONFIG:
        return ConfigPacket(
            sequence=payload_data.config.header.sequence,
            timestamp=payload_data.config.header.timestamp,
            config_json=_ffi.string(payload_data.config.config_data, payload_data.config.config_data_len).decode(),
        )
    elif payload_type == _lib.QLCP_PT_ACK:
        return AckPacket(
            sequence=payload_data.ack.header.sequence,
            timestamp=payload_data.ack.header.timestamp,
            ack_packet_type=PacketType(payload_data.ack.ack_packet_type),
            ack_sequence=payload_data.ack.ack_sequence,
        )
    elif payload_type == _lib.QLCP_PT_NACK:
        return NackPacket(
            sequence=payload_data.nack.header.sequence,
            timestamp=payload_data.nack.header.timestamp,
            nack_packet_type=PacketType(payload_data.nack.nack_packet_type),
            nack_sequence=payload_data.nack.nack_sequence,
            error_code=ErrorCode(payload_data.nack.nack_error_code),
        )
    else:
        raise QLCPError(f"unknown packet type: {payload_type}")

def decode_packet_client(data: bytes) -> ClientReceivedPacket:
    """Decode a server->client packet. For use by the mock device."""
    if len(data) < HEADER_SIZE:
        raise QLCPError(f"packet too small: {len(data)} bytes")

    buf = _ffi.from_buffer(data)
    payload = _ffi.new("qlcp_client_payload *")

    _check(
        _lib.qlcp_decode_server_to_client(payload, buf, len(data)),
        "decode_packet_client"
    )

    return _client_payload_to_python(payload)


def _client_payload_to_python(payload):
    """
    Convert a decoded C payload struct into a Python dataclass based on the packet type. Do not call this directly — use decode_packet_client() instead.
    """
    pt = payload.packet_type
    pd = payload.payload_data

    if pt in (
        _lib.QLCP_PT_ESTOP, _lib.QLCP_PT_DISCOVERY, _lib.QLCP_PT_TIMESYNC,
        _lib.QLCP_PT_STREAM_STOP, _lib.QLCP_PT_GET_SINGLE,
        _lib.QLCP_PT_HEARTBEAT, _lib.QLCP_PT_STATUS_REQUEST,
    ):
        return SimplePacket(
            packet_type=PacketType(pt),
            sequence=pd.header_only.sequence,
            timestamp=pd.header_only.timestamp,
        )
    elif pt == _lib.QLCP_PT_CONTROL:
        return ControlPacket(
            sequence=pd.control.header.sequence,
            timestamp=pd.control.header.timestamp,
            command_id=pd.control.command_id,
            command_state=ControlState(pd.control.command_state),
        )
    elif pt == _lib.QLCP_PT_STREAM_START:
        return StreamStartPacket(
            sequence=pd.stream_start.header.sequence,
            timestamp=pd.stream_start.header.timestamp,
            frequency_hz=pd.stream_start.stream_frequency,
        )
    elif pt == _lib.QLCP_PT_ACK:
        return AckPacket(
            sequence=pd.ack.header.sequence,
            timestamp=pd.ack.header.timestamp,
            ack_packet_type=PacketType(pd.ack.ack_packet_type),
            ack_sequence=pd.ack.ack_sequence,
        )
    elif pt == _lib.QLCP_PT_NACK:
        return NackPacket(
            sequence=pd.nack.header.sequence,
            timestamp=pd.nack.header.timestamp,
            nack_packet_type=PacketType(pd.nack.nack_packet_type),
            nack_sequence=pd.nack.nack_sequence,
            error_code=ErrorCode(pd.nack.nack_error_code),
        )
    else:
        raise QLCPError(f"unexpected packet type from server: {pt:#04x}")
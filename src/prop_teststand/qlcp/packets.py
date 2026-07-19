from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from prop_teststand.qlcp._bindings import ffi as _ffi
from prop_teststand.qlcp._bindings import lib as _lib
from prop_teststand.qlcp.enums import ControlState, ControlType
from prop_teststand.qlcp.native import (
    ENCODE_BUF_SIZE,
    MAX_CONFIG,
    MAX_CONTROLS,
    MAX_SENSORS,
    QLCPError,
    check_qlcp_error,
    get_timestamp_us,
    next_sequence,
)


if TYPE_CHECKING:
    from prop_teststand.qlcp.enums import ErrorCode, PacketType


class EncodablePacket(Protocol):
    """QLCP packet-like object that can be encoded for transport."""

    def encode(self) -> bytes: ...


def _encode_buf() -> tuple[Any, Any]:
    """Create a new encoding buffer and length pointer for encoding packets."""
    return _ffi.new(f"uint8_t[{ENCODE_BUF_SIZE}]"), _ffi.new("size_t *", ENCODE_BUF_SIZE)

@dataclass
class PacketHeader:
    """Common header fields for all QLCP packets."""

    sequence: int
    timestamp_us: int

@dataclass
class SimplePacket:
    """Header-only packet."""

    header: PacketHeader
    packet_type: PacketType

    @classmethod
    def create(cls, packet_type: PacketType) -> SimplePacket:
        return cls(
            packet_type=packet_type,
            header=PacketHeader(
                sequence=next_sequence(),
                timestamp_us=get_timestamp_us(),
            ),
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new(
            "qlcp_header_only_packet *",
            {
                "header": {
                    "sequence": self.header.sequence,
                    "timestamp_us": self.header.timestamp_us,
                },
                "packet_type": self.packet_type,
            },
        )
        check_qlcp_error(
            _lib.qlcp_encode_header_only(buf, buf_len, pkt),
            "encode_header_only",
        )
        return bytes(_ffi.buffer(buf, buf_len[0]))

@dataclass
class ControlStatus:
    id: int
    type: ControlType
    state: ControlState | int | float


@dataclass
class StatusPacket:
    """Device status + batched control states."""

    header: PacketHeader
    ack_packet_type: PacketType
    ack_sequence: int
    control_states: list[ControlStatus] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        ack_packet_type: PacketType,
        ack_sequence: int,
        control_states: list[ControlStatus] | None = None,
    ) -> StatusPacket:
        return cls(
            header=PacketHeader(
                sequence=next_sequence(),
                timestamp_us=get_timestamp_us(),
            ),
            ack_packet_type=ack_packet_type,
            ack_sequence=ack_sequence,
            control_states=control_states or [],
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        control_arr = _ffi.new(f"qlcp_control_data[{MAX_CONTROLS}]")
        for i, ctrl in enumerate(self.control_states):
            if i >= MAX_CONTROLS:
                message = f"too many controls in status packet: {len(self.control_states)} (max {MAX_CONTROLS})"
                raise QLCPError(message)
            control_arr[i].id = ctrl.id
            control_arr[i].type = ctrl.type
            match ctrl.type:
                case ControlType.BOOL:
                    control_arr[i].state.control_bool = ctrl.state
                case ControlType.UINT32:
                    control_arr[i].state.control_uint32 = ctrl.state
                case ControlType.INT32:
                    control_arr[i].state.control_int32 = ctrl.state
                case ControlType.FLOAT32:
                    control_arr[i].state.control_float32 = ctrl.state

        pkt = _ffi.new(
            "qlcp_status_packet *",
            {
                "header": {
                    "sequence": self.header.sequence,
                    "timestamp_us": self.header.timestamp_us,
                },
                "ack_packet_type": self.ack_packet_type,
                "ack_sequence": self.ack_sequence,
                "control_data": control_arr,
                "control_count": min(len(self.control_states), MAX_CONTROLS),
            },
        )
        check_qlcp_error(_lib.qlcp_encode_status(buf, buf_len, pkt), "encode_status")
        return bytes(_ffi.buffer(buf, buf_len[0]))

@dataclass
class StreamStartPacket:
    """Start streaming at the given frequency."""

    header: PacketHeader
    frequency_hz: int

    @classmethod
    def create(cls, frequency_hz: int) -> StreamStartPacket:
        return cls(
            header=PacketHeader(
                sequence=next_sequence(),
                timestamp_us=get_timestamp_us(),
            ),
            frequency_hz=frequency_hz,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new(
            "qlcp_stream_start_packet *",
            {
                "header": {
                    "sequence": self.header.sequence,
                    "timestamp_us": self.header.timestamp_us,
                },
                "stream_frequency": self.frequency_hz,
            },
        )
        check_qlcp_error(_lib.qlcp_encode_stream_start(buf, buf_len, pkt), "encode_stream_start")
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class ControlPacket:
    """Control command."""

    header: PacketHeader
    control_id: int
    control_type: ControlType
    control_state: ControlState | int | float

    @classmethod
    def create(cls, control_id: int, control_type: ControlType, control_state: ControlState | int | float) -> ControlPacket:
        return cls(
            header=PacketHeader(
                sequence=next_sequence(),
                timestamp_us=get_timestamp_us(),
            ),
            control_id=control_id,
            control_type=control_type,
            control_state=control_state,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        control_data = _ffi.new(
            "qlcp_control_data *",
            {
                "id": self.control_id,
                "type": self.control_type,
            },
        )

        # Validate control state type and assign union field
        match self.control_type:
            case ControlType.BOOL:
                if not isinstance(self.control_state, ControlState):
                    msg = f"control_state must be ControlState for BOOL control, got {type(self.control_state)}"
                    raise QLCPError(msg)

                control_data.state.control_bool = self.control_state
            case ControlType.UINT32:
                if not isinstance(self.control_state, int):
                    msg = f"control_state must be int for UINT32 control, got {type(self.control_state)}"
                    raise QLCPError(msg)

                control_data.state.control_uint32 = self.control_state
            case ControlType.INT32:
                if not isinstance(self.control_state, int):
                    msg = f"control_state must be int for INT32 control, got {type(self.control_state)}"
                    raise QLCPError(msg)

                control_data.state.control_int32 = self.control_state
            case ControlType.FLOAT32:
                if not isinstance(self.control_state, float):
                    msg = f"control_state must be float for FLOAT32 control, got {type(self.control_state)}"
                    raise QLCPError(msg)

                control_data.state.control_float32 = self.control_state

        pkt = _ffi.new(
            "qlcp_control_packet *",
            {
                "header": {
                    "sequence": self.header.sequence,
                    "timestamp_us": self.header.timestamp_us,
                },
                "control_data": control_data[0],
            },
        )
        check_qlcp_error(_lib.qlcp_encode_control(buf, buf_len, pkt), "encode_control")
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class AckPacket:
    """ACK packet."""

    header: PacketHeader
    ack_packet_type: PacketType
    ack_sequence: int

    @classmethod
    def create(cls, ack_packet_type: PacketType, ack_sequence: int) -> AckPacket:
        return cls(
            header=PacketHeader(
                sequence=next_sequence(),
                timestamp_us=get_timestamp_us(),
            ),
            ack_packet_type=ack_packet_type,
            ack_sequence=ack_sequence,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new(
            "qlcp_ack_packet *",
            {
                "header": {
                    "sequence": self.header.sequence,
                    "timestamp_us": self.header.timestamp_us,
                },
                "ack_packet_type": self.ack_packet_type,
                "ack_sequence": self.ack_sequence,
            },
        )
        check_qlcp_error(_lib.qlcp_encode_ack(buf, buf_len, pkt), "encode_ack")
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class NackPacket:
    """NACK packet."""

    header: PacketHeader
    nack_packet_type: PacketType
    nack_sequence: int
    error_code: ErrorCode

    @classmethod
    def create(cls, nack_packet_type: PacketType, nack_sequence: int, error_code: ErrorCode) -> NackPacket:
        return cls(
            header=PacketHeader(
                sequence=next_sequence(),
                timestamp_us=get_timestamp_us(),
            ),
            nack_packet_type=nack_packet_type,
            nack_sequence=nack_sequence,
            error_code=error_code,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new(
            "qlcp_nack_packet *",
            {
                "header": {
                    "sequence": self.header.sequence,
                    "timestamp_us": self.header.timestamp_us,
                },
                "nack_packet_type": self.nack_packet_type,
                "nack_sequence": self.nack_sequence,
                "nack_error_code": self.error_code,
            },
        )
        check_qlcp_error(_lib.qlcp_encode_nack(buf, buf_len, pkt), "encode_nack")
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class TimesyncResponsePacket:
    """Timesync response packet."""

    header: PacketHeader
    ack_packet_type: PacketType
    ack_sequence: int
    t1_echo_us: int
    t2_us: int

    @classmethod
    def create(cls, ack_packet_type: PacketType, ack_sequence: int, t1_echo_us: int, t2_us: int) -> TimesyncResponsePacket:
        return cls(
            header=PacketHeader(
                sequence=next_sequence(),
                timestamp_us=get_timestamp_us(),
            ),
            ack_packet_type=ack_packet_type,
            ack_sequence=ack_sequence,
            t1_echo_us=t1_echo_us,
            t2_us=t2_us,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new(
            "qlcp_timesync_resp_packet *",
            {
                "header": {
                    "sequence": self.header.sequence,
                    "timestamp_us": self.header.timestamp_us,
                },
                "ack_packet_type": self.ack_packet_type,
                "ack_sequence": self.ack_sequence,
                "t1_echo_us": self.t1_echo_us,
                "t2_us": self.t2_us,
            },
        )
        check_qlcp_error(_lib.qlcp_encode_timesync_resp(buf, buf_len, pkt), "encode_timesync_resp")
        return bytes(_ffi.buffer(buf, buf_len[0]))

@dataclass
class SensorReading:
    """A single sensor reading within a DATA packet."""

    sensor_id: int
    value: float


@dataclass
class DataPacket:
    """Batched sensor data."""

    header: PacketHeader
    readings: list[SensorReading] = field(default_factory=list)

    @classmethod
    def create(cls, readings: list[SensorReading]) -> DataPacket:
        return cls(
            header=PacketHeader(
                sequence=next_sequence(),
                timestamp_us=get_timestamp_us(),
            ),
            readings=readings,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        sensor_arr = _ffi.new(f"qlcp_sensor_data[{MAX_SENSORS}]")
        for i, reading in enumerate(self.readings):
            if i >= MAX_SENSORS:
                break
            sensor_arr[i].id = reading.sensor_id
            sensor_arr[i].value = reading.value

        pkt = _ffi.new(
            "qlcp_data_packet *",
            {
                "header": {
                    "sequence": self.header.sequence,
                    "timestamp_us": self.header.timestamp_us,
                },
                "sensor_data": sensor_arr,
                "sensor_count": min(len(self.readings), MAX_SENSORS),
            },
        )
        check_qlcp_error(_lib.qlcp_encode_data(buf, buf_len, pkt), "encode_data")
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class ConfigPacket:
    """Device configuration (JSON payload)."""

    header: PacketHeader
    config_json: str

    @classmethod
    def create(cls, config_json: str) -> ConfigPacket:
        return cls(
            header=PacketHeader(
                sequence=next_sequence(),
                timestamp_us=get_timestamp_us(),
            ),
            config_json=config_json,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        conf_bytes = self.config_json.encode()
        conf_buf = _ffi.new(f"char[{MAX_CONFIG}]", conf_bytes)
        conf_buf_len = len(conf_bytes)

        if conf_buf_len > MAX_CONFIG:
            message = f"config JSON too large: {conf_buf_len} bytes (max {MAX_CONFIG})"
            raise QLCPError(message)

        pkt = _ffi.new(
            "qlcp_config_packet *",
            {
                "header": {
                    "sequence": self.header.sequence,
                    "timestamp_us": self.header.timestamp_us,
                },
                "config_data": conf_buf,
                "config_data_len": conf_buf_len,
            },
        )
        check_qlcp_error(_lib.qlcp_encode_config(buf, buf_len, pkt), "encode_config")
        return bytes(_ffi.buffer(buf, buf_len[0]))

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol

from libqretprop.qlcp._bindings import ffi as _ffi
from libqretprop.qlcp._bindings import lib as _lib
from libqretprop.qlcp.constants import ENCODE_BUF_SIZE, MAX_CONFIG, MAX_CONTROLS, MAX_SENSORS
from libqretprop.qlcp.enums import ControlState, DeviceStatus, ErrorCode, PacketType, Unit
from libqretprop.qlcp.errors import QLCPError, check_qlcp_error
from libqretprop.qlcp.sequence import get_timestamp_ms, next_sequence


class EncodablePacket(Protocol):
    """QLCP packet-like object that can be encoded for transport."""

    def encode(self) -> bytes: ...


def _encode_buf() -> tuple[Any, Any]:
    """Create a new encoding buffer and length pointer for encoding packets."""
    return _ffi.new(f"uint8_t[{ENCODE_BUF_SIZE}]"), _ffi.new("size_t *", ENCODE_BUF_SIZE)


@dataclass
class SimplePacket:
    """Header-only packet."""

    packet_type: PacketType
    sequence: int
    timestamp: int

    @classmethod
    def create(cls, packet_type: PacketType) -> SimplePacket:
        return cls(
            packet_type=packet_type,
            sequence=next_sequence(),
            timestamp=get_timestamp_ms(),
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new(
            "qlcp_header_only_packet *",
            {
                "sequence": self.sequence,
                "timestamp": self.timestamp,
            },
        )
        check_qlcp_error(
            _lib.qlcp_encode_header_only(buf, buf_len, self.packet_type, pkt),
            "encode_header_only",
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
    def create(
        cls,
        status: DeviceStatus,
        control_states: list[ControlStatus] | None = None,
    ) -> StatusPacket:
        return cls(
            sequence=next_sequence(),
            timestamp=get_timestamp_ms(),
            status=status,
            control_states=control_states or [],
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        control_arr = _ffi.new(f"qlcp_control_data[{MAX_CONTROLS}]")
        for i, ctrl in enumerate(self.control_states):
            if i >= MAX_CONTROLS:
                message = f"too many controls in status packet: {len(self.control_states)} (max {MAX_CONTROLS})"
                raise QLCPError(message)
            control_arr[i].control_id = ctrl.id
            control_arr[i].control_state = ctrl.state

        pkt = _ffi.new(
            "qlcp_status_packet *",
            {
                "header": {"sequence": self.sequence, "timestamp": self.timestamp},
                "device_status": self.status,
                "control_data": control_arr,
                "control_count": min(len(self.control_states), MAX_CONTROLS),
            },
        )
        check_qlcp_error(_lib.qlcp_encode_status(buf, buf_len, pkt), "encode_status")
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class StreamStartPacket:
    """Start streaming at the given frequency."""

    sequence: int
    timestamp: int
    frequency_hz: int

    @classmethod
    def create(cls, frequency_hz: int) -> StreamStartPacket:
        return cls(
            sequence=next_sequence(),
            timestamp=get_timestamp_ms(),
            frequency_hz=frequency_hz,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new(
            "qlcp_stream_start_packet *",
            {
                "header": {"sequence": self.sequence, "timestamp": self.timestamp},
                "stream_frequency": self.frequency_hz,
            },
        )
        check_qlcp_error(_lib.qlcp_encode_stream_start(buf, buf_len, pkt), "encode_stream_start")
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class ControlPacket:
    """Control command."""

    sequence: int
    timestamp: int
    command_id: int
    command_state: ControlState

    @classmethod
    def create(cls, command_id: int, command_state: ControlState) -> ControlPacket:
        return cls(
            sequence=next_sequence(),
            timestamp=get_timestamp_ms(),
            command_id=command_id,
            command_state=command_state,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new(
            "qlcp_control_packet *",
            {
                "header": {"sequence": self.sequence, "timestamp": self.timestamp},
                "command_id": self.command_id,
                "command_state": self.command_state,
            },
        )
        check_qlcp_error(_lib.qlcp_encode_control(buf, buf_len, pkt), "encode_control")
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class AckPacket:
    """ACK packet."""

    sequence: int
    timestamp: int
    ack_packet_type: PacketType
    ack_sequence: int

    @classmethod
    def create(cls, ack_packet_type: PacketType, ack_sequence: int) -> AckPacket:
        return cls(
            sequence=next_sequence(),
            timestamp=get_timestamp_ms(),
            ack_packet_type=ack_packet_type,
            ack_sequence=ack_sequence,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new(
            "qlcp_ack_packet *",
            {
                "header": {"sequence": self.sequence, "timestamp": self.timestamp},
                "ack_packet_type": self.ack_packet_type,
                "ack_sequence": self.ack_sequence,
            },
        )
        check_qlcp_error(_lib.qlcp_encode_ack(buf, buf_len, pkt), "encode_ack")
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class NackPacket:
    """NACK packet."""

    sequence: int
    timestamp: int
    nack_packet_type: PacketType
    nack_sequence: int
    error_code: ErrorCode

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        pkt = _ffi.new(
            "qlcp_nack_packet *",
            {
                "header": {"sequence": self.sequence, "timestamp": self.timestamp},
                "nack_packet_type": self.nack_packet_type,
                "nack_sequence": self.nack_sequence,
                "nack_error_code": self.error_code,
            },
        )
        check_qlcp_error(_lib.qlcp_encode_nack(buf, buf_len, pkt), "encode_nack")
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
    def create(cls, readings: list[SensorReading]) -> DataPacket:
        return cls(
            sequence=next_sequence(),
            timestamp=get_timestamp_ms(),
            readings=readings,
        )

    def encode(self) -> bytes:
        buf, buf_len = _encode_buf()

        sensor_arr = _ffi.new(f"qlcp_sensor_data[{MAX_SENSORS}]")
        for i, reading in enumerate(self.readings):
            if i >= MAX_SENSORS:
                break
            sensor_arr[i].sensor_id = reading.sensor_id
            sensor_arr[i].unit = reading.unit
            sensor_arr[i].value = reading.value

        pkt = _ffi.new(
            "qlcp_data_packet *",
            {
                "header": {"sequence": self.sequence, "timestamp": self.timestamp},
                "sensor_data": sensor_arr,
                "sensor_count": min(len(self.readings), MAX_SENSORS),
            },
        )
        check_qlcp_error(_lib.qlcp_encode_data(buf, buf_len, pkt), "encode_data")
        return bytes(_ffi.buffer(buf, buf_len[0]))


@dataclass
class ConfigPacket:
    """Device configuration (JSON payload)."""

    sequence: int
    timestamp: int
    config_json: str

    @classmethod
    def create(cls, config_json: str) -> ConfigPacket:
        return cls(
            sequence=next_sequence(),
            timestamp=get_timestamp_ms(),
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
                "header": {"sequence": self.sequence, "timestamp": self.timestamp},
                "config_data": conf_buf,
                "config_data_len": conf_buf_len,
            },
        )
        check_qlcp_error(_lib.qlcp_encode_config(buf, buf_len, pkt), "encode_config")
        return bytes(_ffi.buffer(buf, buf_len[0]))

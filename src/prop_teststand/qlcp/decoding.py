from __future__ import annotations
from typing import Any, cast

from prop_teststand.qlcp._bindings import ffi as _ffi
from prop_teststand.qlcp._bindings import lib as _lib
from prop_teststand.qlcp.enums import ControlState, ControlType, ErrorCode, PacketType
from prop_teststand.qlcp.native import HEADER_SIZE, MAX_CONFIG, MAX_CONTROLS, MAX_SENSORS, QLCPError, check_qlcp_error
from prop_teststand.qlcp.packets import (
    AckPacket,
    ConfigPacket,
    ControlPacket,
    ControlStatus,
    DataPacket,
    NackPacket,
    PacketHeader,
    SensorReading,
    SimplePacket,
    StatusPacket,
    StreamStartPacket,
    TimesyncResponsePacket,
)


# Reuse buffers for client->server decoding to avoid unncessary allocations on the critical data packet path
# These are only used in a single thread so it's safe to reuse
_ctrl_arr = _ffi.new(f"qlcp_control_data[{MAX_CONTROLS}]")
_sens_arr = _ffi.new(f"qlcp_sensor_data[{MAX_SENSORS}]")
_conf_buf = _ffi.new(f"char[{MAX_CONFIG}]")
_buffers = _ffi.new(
    "qlcp_server_payload_buffers *",
    {
        "control_data": _ctrl_arr,
        "control_data_len": MAX_CONTROLS,
        "sensor_data": _sens_arr,
        "sensor_data_len": MAX_SENSORS,
        "config_data": _conf_buf,
        "config_data_len": MAX_CONFIG,
    },
)
_payload = _ffi.new("qlcp_server_payload *")

ServerReceivedPacket = SimplePacket | StatusPacket | DataPacket | ConfigPacket | AckPacket | NackPacket
ClientReceivedPacket = SimplePacket | TimesyncResponsePacket| ControlPacket | StreamStartPacket | AckPacket | NackPacket


def decode_packet_server(data: bytes) -> ServerReceivedPacket:
    """Decode a client->server packet."""
    if len(data) < HEADER_SIZE:
        message = f"packet too small: {len(data)} bytes"
        raise QLCPError(message)

    buf = _ffi.from_buffer(data)  # zero-copy

    check_qlcp_error(
        _lib.qlcp_decode_client_to_server(_payload, _buffers, buf, len(data)),
        "decode_packet",
    )

    return _server_payload_to_python(_payload)

def parse_control_state(control_type: ControlType, state: Any) -> ControlState | int | float:
    """Parse a control state from a C data struct based on the control type."""
    match control_type:
        case ControlType.BOOL:
            return ControlState(state.control_bool)
        case ControlType.UINT32:
            return int(state.control_uint32)
        case ControlType.INT32:
            return int(state.control_int32)
        case ControlType.FLOAT32:
            return float(state.control_float32)

def _server_payload_to_python(payload: Any) -> ServerReceivedPacket:
    """Convert a decoded C payload struct into a Python dataclass based on the packet type.

    Do not call this directly; use decode_packet_server() instead.
    """
    payload_type = payload.packet_type
    payload_data = payload.payload_data

    if payload_type == _lib.QLCP_PT_STATUS:
        control_states = []
        for i in range(payload_data.status.control_count):
            control_type = ControlType(payload_data.status.control_data[i].type)
            control_state = parse_control_state(control_type, payload_data.status.control_data[i].state)

            control_states.append(
                ControlStatus(
                    id=payload_data.status.control_data[i].id,
                    type=control_type,
                    state=control_state,
                ),
            )

        return StatusPacket(
            header=PacketHeader(
                sequence=payload_data.status.header.sequence,
                timestamp_us=payload_data.status.header.timestamp_us,
            ),
            ack_packet_type=PacketType(payload_data.status.ack_packet_type),
            ack_sequence=payload_data.status.ack_sequence,
            control_states=control_states,
        )
    if payload_type == _lib.QLCP_PT_DATA:
        return DataPacket(
            header=PacketHeader(
                sequence=payload_data.data.header.sequence,
                timestamp_us=payload_data.data.header.timestamp_us,
            ),
            readings=[
                SensorReading(
                    sensor_id=payload_data.data.sensor_data[i].id,
                    value=payload_data.data.sensor_data[i].value,
                )
                for i in range(payload_data.data.sensor_count)
            ],
        )
    if payload_type == _lib.QLCP_PT_CONFIG:
        return ConfigPacket(
            header=PacketHeader(
                sequence=payload_data.config.header.sequence,
                timestamp_us=payload_data.config.header.timestamp_us,
            ),
            config_json=cast(
                "bytes",
                _ffi.string(
                    payload_data.config.config_data,
                    payload_data.config.config_data_len,
                ),
            ).decode(),
        )
    if payload_type == _lib.QLCP_PT_ACK:
        return AckPacket(
            header=PacketHeader(
                sequence=payload_data.ack.header.sequence,
                timestamp_us=payload_data.ack.header.timestamp_us,
            ),
            ack_packet_type=PacketType(payload_data.ack.ack_packet_type),
            ack_sequence=payload_data.ack.ack_sequence,
        )
    if payload_type == _lib.QLCP_PT_NACK:
        return NackPacket(
            header=PacketHeader(
                sequence=payload_data.nack.header.sequence,
                timestamp_us=payload_data.nack.header.timestamp_us,
            ),
            nack_packet_type=PacketType(payload_data.nack.nack_packet_type),
            nack_sequence=payload_data.nack.nack_sequence,
            error_code=ErrorCode(payload_data.nack.nack_error_code),
        )

    message = f"unknown packet type: {payload_type}"
    raise QLCPError(message)


def decode_packet_client(data: bytes) -> ClientReceivedPacket:
    """Decode a server->client packet. For use by the mock device."""
    if len(data) < HEADER_SIZE:
        message = f"packet too small: {len(data)} bytes"
        raise QLCPError(message)

    buf = _ffi.from_buffer(data)
    payload = _ffi.new("qlcp_client_payload *")

    check_qlcp_error(
        _lib.qlcp_decode_server_to_client(payload, buf, len(data)),
        "decode_packet_client",
    )

    return _client_payload_to_python(payload)


def _client_payload_to_python(payload: Any) -> ClientReceivedPacket:
    """Convert a decoded C payload struct into a Python dataclass based on the packet type.

    Do not call this directly; use decode_packet_client() instead.
    """
    payload_type = payload.packet_type
    payload_data = payload.payload_data

    if payload_type in (
        _lib.QLCP_PT_ESTOP,
        _lib.QLCP_PT_DISCOVERY,
        _lib.QLCP_PT_TIMESYNC_RESP,
        _lib.QLCP_PT_STREAM_STOP,
        _lib.QLCP_PT_GET_SINGLE,
        _lib.QLCP_PT_HEARTBEAT,
        _lib.QLCP_PT_STATUS_REQUEST,
    ):
        return SimplePacket(
            header=PacketHeader(
                sequence=payload_data.header_only.header.sequence,
                timestamp_us=payload_data.header_only.header.timestamp_us,
            ),
            packet_type=PacketType(payload_type),
        )
    if payload_type == _lib.QLCP_PT_CONTROL:
        control_type = ControlType(payload_data.control.control_data.type)
        control_state = parse_control_state(control_type, payload_data.control.control_data.state)

        return ControlPacket(
            header=PacketHeader(
                sequence=payload_data.control.header.sequence,
                timestamp_us=payload_data.control.header.timestamp_us,
            ),
            control_id=payload_data.control.control_data.id,
            control_type=control_type,
            control_state=control_state,
        )
    if payload_type == _lib.QLCP_PT_STREAM_START:
        return StreamStartPacket(
            header=PacketHeader(
                sequence=payload_data.stream_start.header.sequence,
                timestamp_us=payload_data.stream_start.header.timestamp_us,
            ),
            frequency_hz=payload_data.stream_start.stream_frequency,
        )
    if payload_type == _lib.QLCP_PT_ACK:
        return AckPacket(
            header=PacketHeader(
                sequence=payload_data.ack.header.sequence,
                timestamp_us=payload_data.ack.header.timestamp_us,
            ),
            ack_packet_type=PacketType(payload_data.ack.ack_packet_type),
            ack_sequence=payload_data.ack.ack_sequence,
        )
    if payload_type == _lib.QLCP_PT_NACK:
        return NackPacket(
            header=PacketHeader(
                sequence=payload_data.nack.header.sequence,
                timestamp_us=payload_data.nack.header.timestamp_us,
            ),
            nack_packet_type=PacketType(payload_data.nack.nack_packet_type),
            nack_sequence=payload_data.nack.nack_sequence,
            error_code=ErrorCode(payload_data.nack.nack_error_code),
        )

    message = f"unexpected packet type from server: {payload_type:#04x}"
    raise QLCPError(message)

from __future__ import annotations
from typing import Any, cast

from libqretprop.qlcp._bindings import ffi as _ffi
from libqretprop.qlcp._bindings import lib as _lib
from libqretprop.qlcp.enums import ControlState, DeviceStatus, ErrorCode, PacketType, Unit
from libqretprop.qlcp.native import HEADER_SIZE, MAX_CONFIG, MAX_CONTROLS, MAX_SENSORS, QLCPError, check_qlcp_error
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

# Cache for converting unit integers to enums without constructing a new enum for every sensor reading
_unit_cache: dict[int, Unit] = {u.value: u for u in Unit}

ServerReceivedPacket = StatusPacket | DataPacket | ConfigPacket | AckPacket | NackPacket
ClientReceivedPacket = SimplePacket | ControlPacket | StreamStartPacket | AckPacket | NackPacket


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


def _server_payload_to_python(payload: Any) -> ServerReceivedPacket:
    """Convert a decoded C payload struct into a Python dataclass based on the packet type.

    Do not call this directly; use decode_packet_server() instead.
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
    if payload_type == _lib.QLCP_PT_DATA:
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
    if payload_type == _lib.QLCP_PT_CONFIG:
        return ConfigPacket(
            sequence=payload_data.config.header.sequence,
            timestamp=payload_data.config.header.timestamp,
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
            sequence=payload_data.ack.header.sequence,
            timestamp=payload_data.ack.header.timestamp,
            ack_packet_type=PacketType(payload_data.ack.ack_packet_type),
            ack_sequence=payload_data.ack.ack_sequence,
        )
    if payload_type == _lib.QLCP_PT_NACK:
        return NackPacket(
            sequence=payload_data.nack.header.sequence,
            timestamp=payload_data.nack.header.timestamp,
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
        _lib.QLCP_PT_TIMESYNC,
        _lib.QLCP_PT_STREAM_STOP,
        _lib.QLCP_PT_GET_SINGLE,
        _lib.QLCP_PT_HEARTBEAT,
        _lib.QLCP_PT_STATUS_REQUEST,
    ):
        return SimplePacket(
            packet_type=PacketType(payload_type),
            sequence=payload_data.header_only.sequence,
            timestamp=payload_data.header_only.timestamp,
        )
    if payload_type == _lib.QLCP_PT_CONTROL:
        return ControlPacket(
            sequence=payload_data.control.header.sequence,
            timestamp=payload_data.control.header.timestamp,
            command_id=payload_data.control.command_id,
            command_state=ControlState(payload_data.control.command_state),
        )
    if payload_type == _lib.QLCP_PT_STREAM_START:
        return StreamStartPacket(
            sequence=payload_data.stream_start.header.sequence,
            timestamp=payload_data.stream_start.header.timestamp,
            frequency_hz=payload_data.stream_start.stream_frequency,
        )
    if payload_type == _lib.QLCP_PT_ACK:
        return AckPacket(
            sequence=payload_data.ack.header.sequence,
            timestamp=payload_data.ack.header.timestamp,
            ack_packet_type=PacketType(payload_data.ack.ack_packet_type),
            ack_sequence=payload_data.ack.ack_sequence,
        )
    if payload_type == _lib.QLCP_PT_NACK:
        return NackPacket(
            sequence=payload_data.nack.header.sequence,
            timestamp=payload_data.nack.header.timestamp,
            nack_packet_type=PacketType(payload_data.nack.nack_packet_type),
            nack_sequence=payload_data.nack.nack_sequence,
            error_code=ErrorCode(payload_data.nack.nack_error_code),
        )

    message = f"unexpected packet type from server: {payload_type:#04x}"
    raise QLCPError(message)

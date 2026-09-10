from enum import IntEnum

from vector.qlcp._bindings import lib as _lib


PacketType = IntEnum(
    "PacketType",
    {
        "ESTOP": _lib.QLCP_PT_ESTOP,
        "DISCOVERY": _lib.QLCP_PT_DISCOVERY,
        "TIMESYNC_REQ": _lib.QLCP_PT_TIMESYNC_REQ,
        "TIMESYNC_RESP": _lib.QLCP_PT_TIMESYNC_RESP,
        "CONTROL": _lib.QLCP_PT_CONTROL,
        "STATUS_REQUEST": _lib.QLCP_PT_STATUS_REQUEST,
        "STREAM_START": _lib.QLCP_PT_STREAM_START,
        "STREAM_STOP": _lib.QLCP_PT_STREAM_STOP,
        "GET_SINGLE": _lib.QLCP_PT_GET_SINGLE,
        "HEARTBEAT": _lib.QLCP_PT_HEARTBEAT,
        "CONFIG": _lib.QLCP_PT_CONFIG,
        "DATA": _lib.QLCP_PT_DATA,
        "STATUS": _lib.QLCP_PT_STATUS,
        "ACK": _lib.QLCP_PT_ACK,
        "NACK": _lib.QLCP_PT_NACK,
        # Sentinel ack_packet_type for unsolicited STATUS packets. Never appears as a
        # header packet_type.
        "NO_ACK": _lib.QLCP_PT_NO_ACK,
    },
)

ControlType = IntEnum(
    "ControlType",
    {
        "BOOL": _lib.QLCP_CONTROL_BOOL,
        "UINT32": _lib.QLCP_CONTROL_UINT32,
        "INT32": _lib.QLCP_CONTROL_INT32,
        "FLOAT32": _lib.QLCP_CONTROL_FLOAT32,
    },
)

ControlState = IntEnum(
    "ControlState",
    {
        "CLOSED": _lib.QLCP_CS_CLOSED,
        "OPEN": _lib.QLCP_CS_OPEN,
    },
)

# Per-control confirmation status carried on STATUS packets (protocol v3.1+).
ControlConfirmStatus = IntEnum(
    "ControlConfirmStatus",
    {
        "CONFIRMED": _lib.QLCP_CONTROL_STATUS_CONFIRMED,
        "PENDING": _lib.QLCP_CONTROL_STATUS_PENDING,
        "ERROR": _lib.QLCP_CONTROL_STATUS_ERROR,
    },
)

ErrorCode = IntEnum(
    "ErrorCode",
    {
        "NONE": _lib.QLCP_ERR_NONE,
        "UNKNOWN_TYPE": _lib.QLCP_ERR_UNKNOWN_TYPE,
        "INVALID_ID": _lib.QLCP_ERR_INVALID_ID,
        "HARDWARE_FAULT": _lib.QLCP_ERR_HARDWARE_FAULT,
        "BUSY": _lib.QLCP_ERR_BUSY,
        "NOT_STREAMING": _lib.QLCP_ERR_NOT_STREAMING,
        "INVALID_PARAM": _lib.QLCP_ERR_INVALID_PARAM,
    },
)

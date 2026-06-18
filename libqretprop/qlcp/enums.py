from enum import IntEnum

from libqretprop.qlcp._bindings import lib as _lib


PacketType = IntEnum(
    "PacketType",
    {
        "ESTOP": _lib.QLCP_PT_ESTOP,
        "DISCOVERY": _lib.QLCP_PT_DISCOVERY,
        "TIMESYNC": _lib.QLCP_PT_TIMESYNC,
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
    },
)

DeviceStatus = IntEnum(
    "DeviceStatus",
    {
        "INACTIVE": _lib.QLCP_DS_INACTIVE,
        "ACTIVE": _lib.QLCP_DS_ACTIVE,
        "ERROR": _lib.QLCP_DS_ERROR,
        "CALIBRATING": _lib.QLCP_DS_CALIBRATING,
    },
)

ControlState = IntEnum(
    "ControlState",
    {
        "CLOSED": _lib.QLCP_CS_CLOSED,
        "OPEN": _lib.QLCP_CS_OPEN,
        "ERROR": _lib.QLCP_CS_ERROR,
    },
)

Unit = IntEnum(
    "Unit",
    {
        "VOLTS": _lib.QLCP_UNIT_VOLTS,
        "AMPS": _lib.QLCP_UNIT_AMPS,
        "CELSIUS": _lib.QLCP_UNIT_CELSIUS,
        "FAHRENHEIT": _lib.QLCP_UNIT_FAHRENHEIT,
        "KELVIN": _lib.QLCP_UNIT_KELVIN,
        "PSI": _lib.QLCP_UNIT_PSI,
        "BAR": _lib.QLCP_UNIT_BAR,
        "PASCAL": _lib.QLCP_UNIT_PASCAL,
        "GRAMS": _lib.QLCP_UNIT_GRAMS,
        "KILOGRAMS": _lib.QLCP_UNIT_KILOGRAMS,
        "POUNDS": _lib.QLCP_UNIT_POUNDS,
        "NEWTONS": _lib.QLCP_UNIT_NEWTONS,
        "SECONDS": _lib.QLCP_UNIT_SECONDS,
        "MILLISECONDS": _lib.QLCP_UNIT_MILLISECONDS,
        "HERTZ": _lib.QLCP_UNIT_HERTZ,
        "OHMS": _lib.QLCP_UNIT_OHMS,
        "UNITLESS": _lib.QLCP_UNIT_UNITLESS,
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

from __future__ import annotations
import time
from typing import Any

from libqretprop.qlcp._bindings import ffi as _ffi
from libqretprop.qlcp._bindings import lib as _lib


# Local allocation bounds, not protocol-defined.
MAX_CONTROLS = 32
MAX_SENSORS = 32
MAX_CONFIG = 8192
ENCODE_BUF_SIZE = 8192

if not hasattr(_lib, "QLCP_HEADER_SIZE"):
    raise RuntimeError(
        "QLCP_HEADER_SIZE missing from compiled qlcp library; rebuild required",
    )
HEADER_SIZE = int(_lib.QLCP_HEADER_SIZE)

_sequence_counter = 0


class QLCPError(Exception):
    """Raised when an error occurs in the QLCP protocol."""


def check_qlcp_error(ret: Any, context: str) -> None:
    """Raise QLCPError when a cffi protocol call returns an error code."""
    if ret == _lib.QLCP_OK:
        return
    names = {
        _lib.QLCP_NULL_PTR: "null pointer",
        _lib.QLCP_NO_MEM: "buffer too small",
        _lib.QLCP_LEN_MISMATCH: "length mismatch",
        _lib.QLCP_VERSION_MISMATCH: "protocol version mismatch",
        _lib.QLCP_INVALID_PACKET_TYPE: "invalid packet type",
    }
    error_name = names.get(ret, f"unknown error {ret}")
    message = f"{context}: {error_name}"
    raise QLCPError(message)


def get_packet_len(data: bytes) -> int:
    """Get the total length of a QLCP packet from its header."""
    buf = _ffi.from_buffer(data)
    data_len = _ffi.new("uint16_t *")
    check_qlcp_error(_lib.qlcp_get_packet_len(data_len, buf, len(data)), "get_packet_len")
    return int(data_len[0])


def next_sequence() -> int:
    global _sequence_counter  # noqa: PLW0603 - Intentional module-level protocol sequence counter.
    seq = _sequence_counter
    _sequence_counter = (_sequence_counter + 1) & 0xFF
    return seq


def get_timestamp_ms() -> int:
    return (int(time.monotonic() * 1000)) & 0xFFFFFFFF

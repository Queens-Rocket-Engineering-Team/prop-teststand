from typing import Any

from libqretprop.qlcp._bindings import lib as _lib


class QLCPError(Exception):
    """Raised when an error occurs in the QLCP protocol."""


def check_qlcp_error(ret: Any, context: str) -> None:
    """Check the return code from a cffi call and raise an exception if it's an error."""
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

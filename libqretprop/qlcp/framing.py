from libqretprop.qlcp._bindings import ffi as _ffi
from libqretprop.qlcp._bindings import lib as _lib
from libqretprop.qlcp.errors import check_qlcp_error


def get_packet_len(data: bytes) -> int:
    """Get the total length of a QLCP packet from its header. Useful for determining how many bytes to read for a full packet."""
    buf = _ffi.from_buffer(data)
    data_len = _ffi.new("uint16_t *")
    check_qlcp_error(_lib.qlcp_get_packet_len(data_len, buf, len(data)), "get_packet_len")
    return int(data_len[0])

from libqretprop.qlcp._bindings import lib as _lib


# Local allocation bounds, not protocol-defined
MAX_CONTROLS = 32
MAX_SENSORS = 32
MAX_CONFIG = 8192  # Much larger than any expected config JSON
ENCODE_BUF_SIZE = 8192  # Large enough for any packet type to avoid buffer-too-small errors on encoding

# Make sure the compiled C library actually exported the header-size constant.
# If this assert fails, the compiled extension or generated headers are out
# of sync with the shared library and we should rebuild the protocol artifacts.
if not hasattr(_lib, "QLCP_HEADER_SIZE"):
    raise RuntimeError(
        "QLCP_HEADER_SIZE missing from compiled qlcp library; rebuild required",
    )
HEADER_SIZE = int(_lib.QLCP_HEADER_SIZE)

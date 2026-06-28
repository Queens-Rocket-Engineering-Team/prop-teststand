import time


_sequence_counter = 0


def next_sequence() -> int:
    global _sequence_counter  # noqa: PLW0603 - Intentional module-level protocol sequence counter.
    seq = _sequence_counter
    _sequence_counter = (_sequence_counter + 1) & 0xFF
    return seq


def get_timestamp_ms() -> int:
    return (int(time.monotonic() * 1000)) & 0xFFFFFFFF

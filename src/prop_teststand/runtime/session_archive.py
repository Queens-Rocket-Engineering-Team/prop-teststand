"""Streams a recording session's directory as a zip, without staging it on disk."""

from __future__ import annotations
import logging
import time
import zipfile
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


logger = logging.getLogger(__name__)

# Text artifacts are worth deflating; video and Opus audio are already compressed, so
# storing them avoids burning CPU for nothing.
DEFLATED_SUFFIXES = frozenset({".csv", ".json", ".txt", ".log"})

# The zip epoch. Anything earlier cannot be represented in a DOS timestamp.
MIN_ZIP_YEAR = 1980


class _UnseekableSink:
    """A buffer that `zipfile` writes into and a generator drains.

    Exposes write/tell/flush but deliberately not seek: ZipFile probes for seek() and,
    finding none, emits a data descriptor after each entry instead of going back to
    patch the local header. That is what makes it possible to stream out entries whose
    compressed size and CRC are only known once the data has been written.
    """

    __slots__ = ("_chunks", "_position")

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._position = 0

    def write(self, data: bytes) -> int:
        size = len(data)
        self._position += size
        self._chunks.append(bytes(data))
        return size

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        return

    def drain(self) -> bytes:
        if not self._chunks:
            return b""
        drained = b"".join(self._chunks)
        self._chunks.clear()
        return drained


def iter_session_zip(session_dir: Path, arc_root: str, *, chunk_size: int = 1 << 20) -> Iterator[bytes]:
    """Yield a zip of *session_dir*, entries rooted at *arc_root*.

    Synchronous on purpose. Starlette drives a sync iterator on its threadpool, so the
    large reads and any deflate work stay off the event loop that the UDP telemetry
    listener runs on.
    """
    sink = _UnseekableSink()

    # The sink is intentionally narrower than the stubs' writable-file protocol: it has
    # no seek(), which is exactly what puts ZipFile into data-descriptor mode.
    with zipfile.ZipFile(sink, mode="w", allowZip64=True) as archive:  # type: ignore[call-overload]
        for file_path in sorted(path for path in session_dir.rglob("*") if path.is_file() and not path.is_symlink()):
            stat = file_path.stat()
            date_time = time.localtime(stat.st_mtime)[:6]
            if date_time[0] < MIN_ZIP_YEAR:
                date_time = (MIN_ZIP_YEAR, 1, 1, 0, 0, 0)

            relative = file_path.relative_to(session_dir).as_posix()
            info = zipfile.ZipInfo(filename=f"{arc_root}/{relative}", date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED if file_path.suffix.lower() in DEFLATED_SUFFIXES else zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16

            # force_zip64: the entry size is not declared up front, and video routinely
            # exceeds the 4 GiB the classic format can express.
            with archive.open(info, mode="w", force_zip64=True) as destination, file_path.open("rb") as source:
                while chunk := source.read(chunk_size):
                    destination.write(chunk)
                    if data := sink.drain():
                        yield data

            if data := sink.drain():
                yield data

    # Central directory and end-of-archive record, written when ZipFile closed.
    if tail := sink.drain():
        yield tail

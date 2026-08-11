from __future__ import annotations
import io
import os
import zipfile
from pathlib import Path

from prop_teststand.runtime.session_archive import iter_session_zip


SESSION_ID = "2026-08-10_143005_hotfire"


def _make_session(tmp_path: Path) -> Path:
    session_dir = tmp_path / SESSION_ID
    (session_dir / "video").mkdir(parents=True)
    (session_dir / "audio").mkdir()
    (session_dir / "telemetry.csv").write_text("device_timestamp,source\n1.0000,MockDevice\n")
    (session_dir / "session.json").write_text('{"id": "x"}')
    (session_dir / "video" / "Cam1.mp4").write_bytes(b"\x00\x01\x02" * 5000)
    (session_dir / "audio" / "voice.opus").write_bytes(b"\xff\xfe" * 100)
    return session_dir


def _build(session_dir: Path) -> bytes:
    return b"".join(iter_session_zip(session_dir, SESSION_ID))


def test_archive_is_valid_and_rooted_at_the_session_id(tmp_path: Path) -> None:
    blob = _build(_make_session(tmp_path))

    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert archive.testzip() is None
        assert sorted(archive.namelist()) == [
            f"{SESSION_ID}/audio/voice.opus",
            f"{SESSION_ID}/session.json",
            f"{SESSION_ID}/telemetry.csv",
            f"{SESSION_ID}/video/Cam1.mp4",
        ]


def test_contents_round_trip_byte_exact(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    blob = _build(session_dir)

    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert archive.read(f"{SESSION_ID}/telemetry.csv") == (session_dir / "telemetry.csv").read_bytes()
        assert archive.read(f"{SESSION_ID}/video/Cam1.mp4") == (session_dir / "video" / "Cam1.mp4").read_bytes()


def test_text_is_deflated_and_media_is_stored(tmp_path: Path) -> None:
    blob = _build(_make_session(tmp_path))

    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        by_name = {info.filename: info for info in archive.infolist()}

    assert by_name[f"{SESSION_ID}/telemetry.csv"].compress_type == zipfile.ZIP_DEFLATED
    assert by_name[f"{SESSION_ID}/session.json"].compress_type == zipfile.ZIP_DEFLATED
    assert by_name[f"{SESSION_ID}/video/Cam1.mp4"].compress_type == zipfile.ZIP_STORED
    assert by_name[f"{SESSION_ID}/audio/voice.opus"].compress_type == zipfile.ZIP_STORED


def test_every_entry_carries_a_data_descriptor(tmp_path: Path) -> None:
    # Bit 3 is only set when zipfile treats the output as unseekable. If someone adds a
    # seek() to the sink, sizes get back-patched instead and streaming silently breaks.
    blob = _build(_make_session(tmp_path))

    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert all(info.flag_bits & 0x08 for info in archive.infolist())


def test_symlinks_are_skipped(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("not yours")
    (session_dir / "link.txt").symlink_to(secret)

    blob = _build(session_dir)

    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert f"{SESSION_ID}/link.txt" not in archive.namelist()


def test_an_empty_session_still_produces_a_readable_archive(tmp_path: Path) -> None:
    session_dir = tmp_path / SESSION_ID
    session_dir.mkdir()

    with zipfile.ZipFile(io.BytesIO(_build(session_dir))) as archive:
        assert archive.namelist() == []


def test_prehistoric_mtimes_are_clamped_to_the_zip_epoch(tmp_path: Path) -> None:
    session_dir = tmp_path / SESSION_ID
    session_dir.mkdir()
    stale = session_dir / "telemetry.csv"
    stale.write_text("x")
    os.utime(stale, (0, 0))  # 1970, before the zip epoch

    with zipfile.ZipFile(io.BytesIO(_build(session_dir))) as archive:
        assert archive.infolist()[0].date_time == (1980, 1, 1, 0, 0, 0)

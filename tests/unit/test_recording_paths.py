from pathlib import Path, PurePosixPath

import pytest

from vector.config import RECORDINGS_DEFAULTS, RecordingsConfig
from vector.runtime.recording_paths import RecordingPaths


def _paths(root: Path) -> RecordingPaths:
    config: RecordingsConfig = {"root": str(root), "mediamtx_container_root": "/recordings"}
    return RecordingPaths.from_config(config)


def test_from_config_resolves_root(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "sub" / ".." / "recordings")
    assert paths.root == (tmp_path / "recordings").resolve()
    assert paths.container_root == PurePosixPath("/recordings")


def test_session_dir_is_a_child_of_root(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert paths.session_dir("2026-08-10_143005_hotfire") == tmp_path / "2026-08-10_143005_hotfire"


def test_to_container_rewrites_the_mount_point(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    video_dir = paths.session_dir("2026-08-10_143005_hotfire") / "video"

    assert paths.to_container(video_dir) == PurePosixPath("/recordings/2026-08-10_143005_hotfire/video")


def test_to_container_rejects_paths_outside_the_tree(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "recordings")

    with pytest.raises(ValueError, match=r"not in the subpath|does not start with"):
        paths.to_container(tmp_path / "elsewhere" / "video")


def test_ensure_root_creates_a_missing_root(tmp_path: Path) -> None:
    root = tmp_path / "recordings"
    paths = _paths(root)

    paths.ensure_root()

    assert root.is_dir()
    # The write probe must not survive the check.
    assert list(root.iterdir()) == []


def test_ensure_root_raises_when_the_root_is_not_writable(tmp_path: Path) -> None:
    root = tmp_path / "recordings"
    root.mkdir()
    root.chmod(0o500)
    paths = _paths(root)

    try:
        with pytest.raises(RuntimeError, match="not usable"):
            paths.ensure_root()
    finally:
        root.chmod(0o700)


def test_defaults_match_the_compose_bind_mount() -> None:
    # The default container root is only correct while the media service binds
    # ./recordings at /recordings; if that changes, this default must change too.
    assert RECORDINGS_DEFAULTS["mediamtx_container_root"] == "/recordings"

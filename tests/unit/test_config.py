from __future__ import annotations
from typing import TYPE_CHECKING

import pytest

from vector.config import RECORDINGS_DEFAULTS, load_config


if TYPE_CHECKING:
    from pathlib import Path


def test_load_config_fills_in_the_recordings_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("cameras: []\nservices:\n  recordings:\n    root: /data/recordings\n")

    config = load_config(str(config_path))

    assert config["services"]["recordings"]["root"] == "/data/recordings"
    assert config["services"]["recordings"]["mediamtx_container_root"] == RECORDINGS_DEFAULTS["mediamtx_container_root"]


@pytest.mark.parametrize("contents", ["", "# only a comment\n", "just a string\n", "- one\n- two\n"])
def test_load_config_rejects_a_file_that_is_not_a_mapping(tmp_path: Path, contents: str) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(contents)

    # Without this the empty file surfaces as an AttributeError on None, somewhere well
    # away from the config that caused it.
    with pytest.raises(ValueError, match="must contain a YAML mapping"):
        load_config(str(config_path))


def test_load_config_reports_a_missing_file_by_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Configuration file not found"):
        load_config(str(tmp_path / "absent.yaml"))

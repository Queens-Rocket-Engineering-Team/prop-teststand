"""Translation between this process's view of the recordings tree and MediaMTX's."""

from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from prop_teststand.config import RecordingsConfig


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecordingPaths:
    """The shared recordings tree, addressed from both sides of the container boundary.

    The server and MediaMTX see the same directory under different mount points, so a
    path handed to MediaMTX's ``recordPath`` has to be rewritten from ``root`` to
    ``container_root``. One root pair covers every file in the tree; a per-subsystem
    key would need extending every time a new media type is recorded.
    """

    root: Path
    container_root: PurePosixPath

    @classmethod
    def from_config(cls, config: RecordingsConfig) -> RecordingPaths:
        return cls(
            root=Path(config["root"]).resolve(),
            container_root=PurePosixPath(config["mediamtx_container_root"]),
        )

    def session_dir(self, session_id: str) -> Path:
        """Directory holding one session's artifacts. Does not validate *session_id*."""
        return self.root / session_id

    def to_container(self, path: Path) -> PurePosixPath:
        """Rewrite a path under ``root`` into MediaMTX's filesystem.

        Raises ValueError when *path* is outside the shared tree, which means the
        configured roots and the compose bind mounts have drifted apart.
        """
        relative = path.resolve().relative_to(self.root)
        return self.container_root / PurePosixPath(*relative.parts)

    def ensure_root(self) -> None:
        """Create the recordings root, failing loudly when it is not writable.

        A server that refuses to boot is easier to diagnose than sessions that appear
        to start and then silently record nothing.
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".write-probe"
            probe.touch()
            probe.unlink()
        except OSError as exc:
            logger.exception(
                'Recordings root %s is not usable. Fix with: sudo chown -R "$(id -u):$(id -g)" %s',
                self.root,
                self.root,
            )
            message = f"Recordings root {self.root} is not usable: {exc}"
            raise RuntimeError(message) from exc

        logger.info("Recordings root %s maps to %s inside the media server", self.root, self.container_root)

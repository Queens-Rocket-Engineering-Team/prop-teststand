import contextlib
import os
import subprocess
import time
import wave
from pathlib import Path
from threading import Lock
from typing import Any
from wave import Wave_write

from mumble import Mumble

from vector.config import MumbleConfig


class AudioRuntime:
    def __init__(self, config: MumbleConfig) -> None:
        self._config = config
        self._mumble: Mumble | None = None
        self._wav: Wave_write | None = None
        self._file_name: str | None = None
        self._output_dir: Path | None = None
        self._stopping = False
        self._lock = Lock()

    def start(self, output_dir: Path) -> dict[str, str]:
        """Begin recording the Mumble channel, transcoding into *output_dir* on stop.

        Blocking: connects a Mumble client and waits for the handshake. Callers on the
        event loop must dispatch this to a thread.
        """
        with self._lock:
            if self._mumble is not None or self._stopping:
                raise RuntimeError("already recording")

            self._output_dir = output_dir
            file_name = f"mumble_recording_{int(time.time())}"
            temp_recording_dir = Path(self._config["temp_recording_dir"]).resolve()
            temp_recording_dir.mkdir(parents=True, exist_ok=True)
            temp_path = (temp_recording_dir / file_name).with_suffix(".wav")

            wav = wave.open(str(temp_path), "w")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(48000)

            mumble = Mumble(
                self._config["ip"],
                "recorder",
                password=self._config.get("password", ""),
                port=self._config["port"],
                debug=False,
            )
            mumble.callbacks.sound_received.set_handler(self._sound_received_handler)

            self._mumble = mumble
            self._wav = wav
            self._file_name = file_name

            try:
                mumble.start()
                mumble.wait_until_connected()
            except Exception:
                self._clear_recording_state()
                with contextlib.suppress(Exception):
                    mumble.stop()
                with contextlib.suppress(Exception):
                    wav.close()
                raise

            return {"status": "started"}

    def stop(self) -> dict[str, str | None]:
        """Stop recording and transcode to Opus. Blocking: shells out to ffmpeg."""
        with self._lock:
            if self._mumble is None or self._wav is None:
                raise RuntimeError("not recording")

            mumble = self._mumble
            wav = self._wav
            file_name = self._file_name
            output_dir = self._output_dir
            assert file_name is not None
            assert output_dir is not None
            self._stopping = True

        try:
            try:
                mumble.stop()
            finally:
                wav.close()
            self._transcode_to_opus(file_name, output_dir)
        finally:
            with self._lock:
                self._clear_recording_state()
                self._stopping = False

        return {"status": "stopped", "file": f"{file_name}.opus"}

    def close(self) -> None:
        with self._lock:
            mumble = self._mumble
            wav = self._wav
            self._clear_recording_state()
            self._stopping = False

        if mumble is not None:
            with contextlib.suppress(Exception):
                mumble.stop()
        if wav is not None:
            with contextlib.suppress(Exception):
                wav.close()

    def _sound_received_handler(self, _user: object, soundchunk: Any) -> None:
        with self._lock:
            if self._stopping or self._wav is None:
                return
            self._wav.writeframes(soundchunk.pcm)

    def _transcode_to_opus(self, file_name: str, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)

        temp_path = (self._temp_recordings_root() / file_name).with_suffix(".wav")
        output_path = (output_dir / file_name).with_suffix(".opus")

        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(temp_path),
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "96k",
                    "-vbr",
                    "on",
                    "-ar",
                    "48000",
                    str(output_path),
                ],
                check=True,
            )
        finally:
            with contextlib.suppress(Exception):
                os.remove(temp_path)

    def _clear_recording_state(self) -> None:
        self._mumble = None
        self._wav = None
        self._file_name = None
        self._output_dir = None

    def _temp_recordings_root(self) -> Path:
        return Path(self._config["temp_recording_dir"]).resolve()

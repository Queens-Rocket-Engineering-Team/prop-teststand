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

from libqretprop.config import MumbleConfig


class AudioRuntime:
    def __init__(self, config: MumbleConfig) -> None:
        self._config = config
        self._mumble: Mumble | None = None
        self._wav: Wave_write | None = None
        self._file_name: str | None = None
        self._stopping = False
        self._lock = Lock()

    def start(self) -> dict[str, str]:
        with self._lock:
            if self._mumble is not None or self._stopping:
                raise RuntimeError("already recording")

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
        with self._lock:
            if self._mumble is None or self._wav is None:
                raise RuntimeError("not recording")

            mumble = self._mumble
            wav = self._wav
            response_file = self._file_name
            file_name = response_file if response_file else "recording-unknown"
            self._stopping = True

        try:
            try:
                mumble.stop()
            finally:
                wav.close()
            self._transcode_to_opus(file_name)
        finally:
            with self._lock:
                self._clear_recording_state()
                self._stopping = False

        return {"status": "stopped", "file": response_file}

    def list_recordings(self) -> list[dict[str, str]]:
        recordings_dir = self._recordings_root()
        if not recordings_dir.exists():
            return []

        files = [
            {
                "filename": file_path.name,
                "download_path": f"/v1/audio/files/{file_path.name}",
            }
            for file_path in recordings_dir.iterdir()
            if file_path.suffix == ".opus"
        ]

        files.sort(key=lambda file_info: (recordings_dir / file_info["filename"]).stat().st_mtime, reverse=True)
        return files

    def get_recording_path(self, filename: str) -> Path:
        recordings_root = self._recordings_root()
        safe_filename = Path(filename).name
        if safe_filename != filename:
            raise ValueError("Invalid filename")

        file_path = (recordings_root / safe_filename).resolve()
        if file_path.parent != recordings_root:
            raise ValueError("Invalid filename")

        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError("File not found")

        return file_path

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

    def _transcode_to_opus(self, file_name: str) -> str:
        output_dir = self._recordings_root()
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

        return output_path.name

    def _clear_recording_state(self) -> None:
        self._mumble = None
        self._wav = None
        self._file_name = None

    def _recordings_root(self) -> Path:
        return Path(self._config["recording_dir"]).resolve()

    def _temp_recordings_root(self) -> Path:
        return Path(self._config["temp_recording_dir"]).resolve()

import time
import wave
import subprocess
import os

from mumble import Mumble
from pathlib import Path

def start_recording(host: str, port: int, password: str, temp_recording_dir: str):
    file_name = f"mumble_recording_{int(time.time())}"

    # Create temp recording directory if it doesn't exist for first recording
    if not Path(temp_recording_dir).exists():
        Path(temp_recording_dir).mkdir(parents=True, exist_ok=True)

    # Save the recording as a wav file in the temp directory
    # This will be converted to opus and moved to the final recording directory when recording is stopped
    path = (Path(temp_recording_dir).resolve() / file_name).with_suffix('.wav')

    wav = wave.open(str(path), 'w')
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(48000)

    def sound_received_handler(user, soundchunk):
        wav.writeframes(soundchunk.pcm)

    print(f"Joining mumble server at {host}:{port} and recording to {path}")
    mumble = Mumble(host, "recorder", password=password, port=port, debug=False)
    mumble.callbacks.sound_received.set_handler(sound_received_handler)
    mumble.start()
    mumble.wait_until_connected()

    return mumble, wav, file_name

def stop_recording(mumble: Mumble, wav: wave.Wave_write, temp_recording_dir: str, output_dir: str, file_name: str):
    temp_path = (Path(temp_recording_dir).resolve() / file_name).with_suffix('.wav')
    output_path = (Path(output_dir).resolve() / file_name).with_suffix('.opus')

    wav.close()
    mumble.stop()

    # Convert wav to opus using ffmpeg to reduce file size and move to final output directory
    subprocess.run([
        "ffmpeg",
        "-y",  # Overwrite output file if it exists
        "-i", str(temp_path),
        "-c:a", "libopus",
        "-b:a", "32k",
        str(output_path)
    ], check=True)

    # Remove the original wav file
    os.remove(temp_path)

    # Return the file name without fill path for API response
    return str(output_path.name)

from typing import TypedDict

import yaml  # type: ignore[import-untyped]


class AccountServiceConfig(TypedDict):
    username: str
    password: str

class CameraConfig(TypedDict):
    ip: str
    onvif_port: int

class MediaMTXConfig(TypedDict):
    ip: str
    api_port: int
    webrtc_port: int
    recordings_dir: str

class RedisConfig(TypedDict):
    ip: str
    port: int

class MumbleConfig(TypedDict):
    ip: str
    port: int
    recording_dir: str
    temp_recording_dir: str

class ServicesConfig(TypedDict):
    mediamtx: MediaMTXConfig
    redis: RedisConfig
    mumble: MumbleConfig

class ServerConfig(TypedDict):
    accounts: dict[str, AccountServiceConfig]
    cameras: list[CameraConfig]
    services: ServicesConfig

serverConfig: ServerConfig = {
    "accounts": {},
    "cameras": [],
    # Services are required fields, so some default is needed
    "services": {
        "mediamtx": {
            "ip": "",
            "api_port": 0,
            "webrtc_port": 0,
            "recordings_dir": "./recordings/mediamtx",
        },
        "redis": {
            "ip": "",
            "port": 0,
        },
        "mumble": {
            "ip": "",
            "port": 0,
            "recording_dir": "",
            "temp_recording_dir": "",
        },
    }
}

def load_config(config_path: str) -> None:
    global serverConfig

    try:
        with open(config_path, "r") as file:
            serverConfig = yaml.safe_load(file.read())
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Configuration file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse YAML configuration file '{config_path}': {exc}") from exc

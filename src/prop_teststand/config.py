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
    recordings_dir: str

class MumbleConfig(TypedDict):
    ip: str
    port: int
    password: str
    recording_dir: str
    temp_recording_dir: str

class ServicesConfig(TypedDict):
    mediamtx: MediaMTXConfig
    mumble: MumbleConfig

class ServerConfig(TypedDict):
    accounts: dict[str, AccountServiceConfig]
    cameras: list[CameraConfig]
    services: ServicesConfig

def load_config(config_path: str) -> ServerConfig:
    try:
        with open(config_path, "r") as file:
            return yaml.safe_load(file.read())
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Configuration file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse YAML configuration file '{config_path}': {exc}") from exc

from typing import TypedDict

import yaml


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

class RedisConfig(TypedDict):
    ip: str
    port: int

class ServicesConfig(TypedDict):
    mediamtx: MediaMTXConfig
    redis: RedisConfig

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
        },
        "redis": {
            "ip": "",
            "port": 0,
        },
    }
}

def loadConfig(configPath: str) -> None:
    global serverConfig

    try:
        with open(configPath, "r") as file:
            serverConfig = yaml.safe_load(file.read())
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Configuration file not found: {configPath}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse YAML configuration file '{configPath}': {exc}") from exc


from typing import TypedDict

import yaml


class AccountServiceConfig(TypedDict):
    username: str
    password: str

class CameraConfig(TypedDict):
    ip: str
    onvif_port: int

class MediaMTXConfig(TypedDict):
    ip: str | None
    port: int | None

class ServerConfig(TypedDict):
    accounts: dict[str, AccountServiceConfig]
    cameras: list[CameraConfig]
    mediamtx: MediaMTXConfig | None

serverConfig: ServerConfig = {
    "accounts": {},
    "cameras": [],
    "mediamtx": None,
}

def loadConfig(configPath: str) -> None:
    global serverConfig

    with open(configPath, "r") as file:
        serverConfig = yaml.safe_load(file.read())


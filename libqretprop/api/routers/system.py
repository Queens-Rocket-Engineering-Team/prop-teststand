from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from libqretprop.api.security import auth_user


router = APIRouter(tags=["system"])


@router.get("/")  # Define a GET endpoint at the root “/”
async def read_root() -> dict:
    return {"message": "Welcome to the Prop Control API! Authenticate through the /auth endpoint."}


@router.get("/auth")  # Define a GET endpoint at “/auth”
async def read_auth(user: Annotated[str, Depends(auth_user)]) -> dict:
    if user == "noah":
        return {"message": "Welcome back Mr Stark!"}

    return {"message": f"Authenticated as, {user}!"}


@router.get("/health")
async def get_health() -> dict:
    return {"message": "The server is alive!"}


@router.get("/v1/state", summary="Get a structured snapshot of server state")
async def get_state(request: Request) -> dict[str, Any]:
    return request.app.state.runtime.system_state.to_dict()


@router.get("/v1/metrics", summary="Get live server metrics diagnostics")
async def get_metrics(request: Request) -> dict[str, object]:
    return request.app.state.runtime.metrics.to_dict()


class ConfigsResponse(BaseModel):
    count: int
    configs: dict[str, dict[str, Any]]


@router.get("/config", summary="Get the sensor and control config", response_model=ConfigsResponse)
async def get_device_configs(request: Request) -> ConfigsResponse:
    rt = request.app.state.runtime
    configs: dict[str, dict] = {}
    for dev in rt.esp_runtime.get_registered_devices().values():
        configs[getattr(dev, "name", getattr(dev, "id", "unknown"))] = dev.qlcp_config.raw_config
    return ConfigsResponse(count=len(configs), configs=configs)


@router.get("/status", summary="Gets the current state of each valve. Status is reported to the log stream.")
async def get_status(request: Request) -> None:
    rt = request.app.state.runtime
    devices = rt.esp_runtime.get_registered_devices()

    # Trigger a status request to all devices to refresh their latest states.
    for device in devices.values():
        await rt.esp_runtime.get_status(device)


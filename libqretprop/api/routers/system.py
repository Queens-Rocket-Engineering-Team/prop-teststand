from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from libqretprop.api.deps import get_runtime
from libqretprop.api.security import auth_user
from libqretprop.runtime.services import RuntimeServices


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
async def get_state(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> dict[str, Any]:
    return rt.system_state.to_dict()


@router.get("/v1/metrics", summary="Get live server metrics diagnostics")
async def get_metrics(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> dict[str, object]:
    return rt.metrics.to_dict()


class ConfigsResponse(BaseModel):
    count: int
    configs: dict[str, dict[str, Any]]


@router.get("/config", summary="Get the sensor and control config", response_model=ConfigsResponse)
async def get_device_configs(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> ConfigsResponse:
    configs: dict[str, dict] = {}
    for dev in rt.esp_runtime.get_registered_devices().values():
        configs[getattr(dev, "name", getattr(dev, "id", "unknown"))] = dev.qlcp_config.raw_config
    return ConfigsResponse(count=len(configs), configs=configs)


@router.get("/status", summary="Gets the current state of each valve. Status is reported to the log stream.")
async def get_status(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> None:
    devices = rt.esp_runtime.get_registered_devices()
    for device in devices.values():
        await rt.esp_runtime.get_status(device)


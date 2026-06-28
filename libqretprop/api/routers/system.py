from typing import Annotated, Any

from fastapi import APIRouter, Depends

from libqretprop.api.deps import get_runtime
from libqretprop.runtime.services import RuntimeServices


router = APIRouter(tags=["system"])


@router.get("/")
async def read_root() -> dict:
    return {"message": "Welcome to the Prop Control API!"}


@router.get("/health")
async def get_health() -> dict:
    return {"message": "The server is alive!"}


@router.get("/v1/state", summary="Get a structured snapshot of server state")
async def get_state(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> dict[str, Any]:
    return rt.system_state.to_dict()


@router.get("/v1/metrics", summary="Get live server metrics diagnostics")
async def get_metrics(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> dict[str, object]:
    return rt.metrics.to_dict()


@router.get("/status", summary="Gets the current state of each valve. Status is reported to the log stream.")
async def get_status(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> None:
    devices = rt.esp_runtime.get_registered_devices()
    for device in devices.values():
        await rt.esp_runtime.get_status(device)

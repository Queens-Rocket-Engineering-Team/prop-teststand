import logging
from typing import Annotated, Literal, Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from libqretprop.api.deps import get_runtime
from libqretprop.api.models import CommandResponse
from libqretprop.runtime.services import RuntimeServices


logger = logging.getLogger(__name__)
router = APIRouter(tags=["devices"])


class GetSingleCommand(BaseModel):
    command: Literal["GETS"]


class StopCommand(BaseModel):
    command: Literal["STOP"]


class StreamCommand(BaseModel):
    command: Literal["STREAM"]
    frequency_hz: int = Field(gt=0, le=65535)


class ControlCommand(BaseModel):
    command: Literal["CONTROL"]
    control_name: str
    control_state: Literal["OPEN", "CLOSE"]


CommandRequest = Annotated[
    Union[GetSingleCommand, StopCommand, StreamCommand, ControlCommand],
    Field(discriminator="command"),
]


class AutoDiscoveryConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    interval_seconds: float = Field(alias="intervalSeconds")


@router.post(
    "/v1/command",
    summary="Send a command to the devices on the network",
)
async def send_device_command(
    cmd: CommandRequest,
    bg_tasks: BackgroundTasks,
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
) -> CommandResponse:
    logger.info(f"Command sent: {cmd.command!r}")

    devices = rt.esp_runtime.get_registered_devices()
    any_commands_sent = False

    for device in devices.values():
        match cmd:
            case GetSingleCommand():
                any_commands_sent = True
                bg_tasks.add_task(rt.esp_runtime.get_single, device)
            case StopCommand():
                any_commands_sent = True
                bg_tasks.add_task(rt.esp_runtime.stop_streaming, device)
            case StreamCommand(frequency_hz=freq):
                any_commands_sent = True
                bg_tasks.add_task(rt.esp_runtime.start_streaming, device, freq)
            case ControlCommand(control_name=control_name, control_state=control_state):
                if control_name.upper() not in device.controls:
                    continue
                any_commands_sent = True
                bg_tasks.add_task(rt.esp_runtime.set_control, device, control_name.upper(), control_state)

    if not any_commands_sent:
        raise HTTPException(400, "No valid target devices for the command")

    return CommandResponse(
        status="sent",
        message=f"Command {cmd.command!r} sent to {', '.join(d.name for d in devices.values())}.",
    )


@router.get("/v1/autodiscovery", summary="Get autodiscovery settings")
async def get_autodiscovery_settings(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> AutoDiscoveryConfig:
    ds = rt.discovery_service
    return AutoDiscoveryConfig(
        enabled=ds.periodic_enabled,
        intervalSeconds=ds.periodic_interval_s,
    )


@router.post("/v1/autodiscovery", summary="Update autodiscovery settings")
async def update_autodiscovery_settings(
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
    enabled: bool | None = None,
    interval_seconds: Annotated[float | None, Query(alias="intervalSeconds")] = None,
) -> AutoDiscoveryConfig:
    if interval_seconds is not None and interval_seconds <= 0:
        raise HTTPException(400, "intervalSeconds must be greater than 0")

    ds = rt.discovery_service

    if enabled is not None:
        ds.periodic_enabled = enabled

    if interval_seconds is not None:
        ds.periodic_interval_s = interval_seconds

    logger.info(f"User updated autodiscovery: enabled={ds.periodic_enabled}, intervalSeconds={ds.periodic_interval_s}s")

    return AutoDiscoveryConfig(
        enabled=ds.periodic_enabled,
        intervalSeconds=ds.periodic_interval_s,
    )


@router.post("/v1/discover", summary="Send a SSP discover request for new ESP Devices")
async def discover_devices(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> CommandResponse:
    logger.info("User sent device discover command")
    rt.discovery_service.discover()
    return CommandResponse(
        status="sent",
        message="Discovery broadcast sent. Devices will auto-connect.",
    )


@router.post("/v1/estop", summary="Emergency stop - stops all streaming and control commands immediately")
async def emergency_stop(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> None:
    devices = rt.esp_runtime.get_registered_devices()
    for device in devices.values():
        await rt.esp_runtime.emergency_stop(device)

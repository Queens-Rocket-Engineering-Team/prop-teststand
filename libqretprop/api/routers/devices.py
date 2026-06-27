import logging
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from libqretprop.api.models import CommandResponse


logger = logging.getLogger(__name__)
router = APIRouter(tags=["devices"])


class CommandRequest(BaseModel):
    command: Literal["GETS", "STREAM", "STOP", "CONTROL"]
    args: list[str] = []


class AutoDiscoveryConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    interval_seconds: float = Field(alias="intervalSeconds")


@router.post(
    "/v1/command",
    summary="Send a command to the devices on the network",
)  # Define a POST endpoint for device commands at “/command”
async def send_device_command(
    request: Request,
    cmd: CommandRequest,
    bg_tasks: BackgroundTasks,
) -> CommandResponse:
    rt = request.app.state.runtime
    logger.info(f"Command sent: '{cmd.command} {cmd.args}'")

    # Map the relevant command name to their functions
    command_map: dict[str, Callable] = {
        "GETS": rt.esp_runtime.get_single,
        "STREAM": rt.esp_runtime.start_streaming,
        "STOP": rt.esp_runtime.stop_streaming,
        "CONTROL": rt.esp_runtime.set_control,
    }

    devices = rt.esp_runtime.get_registered_devices()

    func = command_map.get(cmd.command)
    if func is None:
        raise HTTPException(400, f"Unknown command {cmd.command!r}")

    # Track if any commands were sent to at least one device, to avoid returning success if all targets were invalid
    any_commands_sent = False

    # Run the command in the background to not block the API
    for device in devices.values():
        if cmd.command == "STREAM":
            # Convert frequency argument to int
            if not cmd.args:
                raise HTTPException(400, "STREAM requires a frequency argument")
            try:
                freq = int(cmd.args[0])
                any_commands_sent = True
                bg_tasks.add_task(func, device, freq)
            except ValueError:
                raise HTTPException(400, f"STREAM frequency must be an integer, got '{cmd.args[0]}'")
        elif cmd.command == "CONTROL":
            # CONTROL needs control_name and control_state
            if len(cmd.args) < 2:
                raise HTTPException(400, "CONTROL requires control name and state")

            control_name = cmd.args[0].upper()
            control_state = cmd.args[1].upper()

            if control_name not in device.controls:
                continue

            any_commands_sent = True
            bg_tasks.add_task(func, device, control_name, control_state)
        else:
            any_commands_sent = True
            bg_tasks.add_task(func, device, *cmd.args)

    if not any_commands_sent:
        raise HTTPException(400, "No valid target devices for the command")

    return CommandResponse(
        status="sent",
        message=f"Command '{cmd.command}' with args: {cmd.args} sent to {', '.join(device.name for device in devices.values())} devices.",
    )


@router.get("/v1/autodiscovery", summary="Get autodiscovery settings")
async def get_autodiscovery_settings(request: Request) -> AutoDiscoveryConfig:
    ds = request.app.state.runtime.discovery_service
    return AutoDiscoveryConfig(
        enabled=ds.periodic_enabled,
        intervalSeconds=ds.periodic_interval_s,
    )


@router.post("/v1/autodiscovery", summary="Update autodiscovery settings")
async def update_autodiscovery_settings(
    request: Request,
    enabled: bool | None = None,
    interval_seconds: Annotated[float | None, Query(alias="intervalSeconds")] = None,
) -> AutoDiscoveryConfig:
    if interval_seconds is not None and interval_seconds <= 0:
        raise HTTPException(400, "intervalSeconds must be greater than 0")

    ds = request.app.state.runtime.discovery_service

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
async def discover_devices(request: Request) -> CommandResponse:
    logger.info("User sent device discover command")
    request.app.state.runtime.discovery_service.discover()
    return CommandResponse(
        status="sent",
        message="Discovery broadcast sent. Devices will auto-connect.",
    )


@router.post("/v1/estop", summary="Emergency stop - stops all streaming and control commands immediately")
async def emergency_stop(request: Request) -> None:
    rt = request.app.state.runtime
    devices = rt.esp_runtime.get_registered_devices()
    for device in devices.values():
        await rt.esp_runtime.emergency_stop(device)


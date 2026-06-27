from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket

from libqretprop.api.deps import get_runtime
from libqretprop.runtime.services import RuntimeServices


router = APIRouter(tags=["streams"])


@router.websocket("/ws/state")
async def websocket_state(
    websocket: WebSocket,
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
) -> None:
    await rt.state_stream.handle_client(websocket)


@router.websocket("/ws/logs")
async def websocket_logs(
    websocket: WebSocket,
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
) -> None:
    await rt.log_stream.handle_client(websocket)


@router.websocket("/ws/telemetry/raw")
async def websocket_raw_telemetry(
    websocket: WebSocket,
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
) -> None:
    await rt.telemetry_stream.handle_client(websocket)


@router.websocket("/ws/telemetry/display")
async def websocket_display_telemetry(
    websocket: WebSocket,
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
) -> None:
    await rt.telemetry_display_stream.handle_client(websocket)

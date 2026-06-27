from fastapi import APIRouter, WebSocket


router = APIRouter(tags=["streams"])


@router.websocket("/ws/state")
async def websocket_state(websocket: WebSocket) -> None:
    await websocket.app.state.runtime.state_stream.handle_client(websocket)


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    await websocket.app.state.runtime.log_stream.handle_client(websocket)


@router.websocket("/ws/telemetry/raw")
async def websocket_raw_telemetry(websocket: WebSocket) -> None:
    await websocket.app.state.runtime.telemetry_stream.handle_client(websocket)


@router.websocket("/ws/telemetry/display")
async def websocket_display_telemetry(websocket: WebSocket) -> None:
    await websocket.app.state.runtime.telemetry_display_stream.handle_client(websocket)


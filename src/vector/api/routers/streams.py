import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket

from vector.api.deps import get_runtime
from vector.runtime.services import RuntimeServices
from vector.runtime.telemetry_display_stream import (
    DEFAULT_DOWNSAMPLE_ALGORITHM,
    DownsampleAlgorithm,
)


logger = logging.getLogger(__name__)

router = APIRouter(tags=["streams"])


def parse_downsample_algorithm(raw: str) -> DownsampleAlgorithm:
    """Coerce the client's ?algorithm= value, falling back to the default.

    Deliberately lenient: typing the query parameter as the enum would make FastAPI
    reject the handshake with code 1008, so a stale GUI would lose live telemetry
    entirely over a cosmetic setting (and reconnect-loop on it).
    """
    try:
        return DownsampleAlgorithm(raw)
    except ValueError:
        logger.warning(
            "Unknown downsample algorithm %r requested; falling back to %s",
            raw,
            DEFAULT_DOWNSAMPLE_ALGORITHM.value,
        )
        return DEFAULT_DOWNSAMPLE_ALGORITHM


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
    algorithm: Annotated[str, Query()] = DEFAULT_DOWNSAMPLE_ALGORITHM.value,
) -> None:
    await rt.telemetry_display_stream.handle_client(websocket, parse_downsample_algorithm(algorithm))

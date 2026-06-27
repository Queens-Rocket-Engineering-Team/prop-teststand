from typing import cast

from starlette.requests import HTTPConnection

from libqretprop.runtime.services import RuntimeServices


def get_runtime(conn: HTTPConnection) -> RuntimeServices:
    """FastAPI dependency that extracts the typed RuntimeServices from app state.

    Works for both HTTP routes (Request) and WebSocket routes (WebSocket) since
    both inherit from HTTPConnection.
    """
    return cast(RuntimeServices, conn.app.state.runtime)

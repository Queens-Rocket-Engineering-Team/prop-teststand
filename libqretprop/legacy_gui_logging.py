from __future__ import annotations
from typing import TYPE_CHECKING

import libqretprop.redis_logging as ml


if TYPE_CHECKING:
    from libqretprop.runtime.esp_device_session import ESPDeviceSession


class LegacyESPLogSink:
    """GUI compatibility sink for legacy text-log ESP device events."""

    def device_connected(self, session: ESPDeviceSession) -> None:
        ml.log(f"{session.name} CONNECTED")

    def device_disconnected(self, session: ESPDeviceSession) -> None:
        ml.log(f"{session.name} DISCONNECTED")

    def control_status(self, session: ESPDeviceSession, control_name: str, state: str) -> None:
        ml.log(f"{session.name} STATUS {control_name} {state}")

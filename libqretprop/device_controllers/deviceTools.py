from __future__ import annotations
from typing import TYPE_CHECKING

import libqretprop.redis_logging as ml
from libqretprop.runtime.esp_device_session import ESPDeviceSession


if TYPE_CHECKING:
    from libqretprop.runtime.services import RuntimeServices


class _LegacyESPLogSink:
    """Temporary GUI compatibility sink for legacy text-log device events."""

    def device_connected(self, session: ESPDeviceSession) -> None:
        ml.log(f"{session.name} CONNECTED")

    def device_disconnected(self, session: ESPDeviceSession) -> None:
        ml.log(f"{session.name} DISCONNECTED")

    def control_status(self, session: ESPDeviceSession, control_name: str, state: str) -> None:
        ml.log(f"{session.name} STATUS {control_name} {state}")


# ---------------------- #
# Device Registry        #
# ---------------------- #


def getRegisteredDevices(runtime: RuntimeServices) -> dict[str, ESPDeviceSession]:
    return runtime.esp_runtime.get_registered_devices()


# ---------------------- #
# Socket Management      #
# ---------------------- #


def closeDeviceConnections(runtime: RuntimeServices) -> None:
    runtime.esp_runtime.close_all()


async def getSingle(runtime: RuntimeServices, device: ESPDeviceSession) -> None:
    await runtime.esp_runtime.get_single(device)


async def startStreaming(runtime: RuntimeServices, device: ESPDeviceSession, Hz: int) -> None:
    await runtime.esp_runtime.start_streaming(device, Hz)


async def stopStreaming(runtime: RuntimeServices, device: ESPDeviceSession) -> None:
    await runtime.esp_runtime.stop_streaming(device)


async def setControl(runtime: RuntimeServices, device: ESPDeviceSession, controlName: str, controlState: str) -> None:
    await runtime.esp_runtime.set_control(device, controlName, controlState)


async def getStatus(runtime: RuntimeServices, device: ESPDeviceSession) -> None:
    await runtime.esp_runtime.get_status(device)


async def emergencyStop(runtime: RuntimeServices, device: ESPDeviceSession) -> None:
    await runtime.esp_runtime.emergency_stop(device)


def cleanup_device(runtime: RuntimeServices, device: ESPDeviceSession) -> None:
    runtime.esp_runtime.cleanup_device(device)


def remove_device(runtime: RuntimeServices, device: ESPDeviceSession) -> None:
    runtime.esp_runtime.remove_device(device)

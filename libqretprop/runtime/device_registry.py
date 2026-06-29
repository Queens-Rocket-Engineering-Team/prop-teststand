from __future__ import annotations
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import ValuesView

    from libqretprop.runtime.esp_connection_runtime import ESPDeviceSession


class DeviceRegistry:
    """Address-keyed live device registry."""

    def __init__(self) -> None:
        self._by_address: dict[str, ESPDeviceSession] = {}

    def register(self, session: ESPDeviceSession) -> None:
        self._by_address[session.address] = session

    def by_address(self, address: str) -> ESPDeviceSession | None:
        return self._by_address.get(address)

    def sessions_named(self, device_name: str) -> list[ESPDeviceSession]:
        return [session for session in self._by_address.values() if session.name == device_name]

    def is_current(self, session: ESPDeviceSession) -> bool:
        registered = self.by_address(session.address)
        return registered is not None and registered.connection_key == session.connection_key

    def remove_current(self, session: ESPDeviceSession) -> ESPDeviceSession | None:
        if not self.is_current(session):
            return None
        return self._by_address.pop(session.address)

    def snapshot_by_address(self) -> dict[str, ESPDeviceSession]:
        return self._by_address.copy()

    def values(self) -> ValuesView[ESPDeviceSession]:
        return self._by_address.values()

    def clear(self) -> None:
        self._by_address.clear()

    def pop(self, address: str, default: ESPDeviceSession | None = None) -> ESPDeviceSession | None:
        return self._by_address.pop(address, default)

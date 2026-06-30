from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING

from kasa import Device, Discover, KasaException


if TYPE_CHECKING:
    from libqretprop.runtime.state_stream import StateStream
    from libqretprop.state.system_state import SystemState

logger = logging.getLogger(__name__)


class KasaRuntime:
    def __init__(self, *, system_state: SystemState, state_stream: StateStream) -> None:
        self._registry: dict[str, Device] = {}
        self._system_state = system_state
        self._state_stream = state_stream

    def _emit(self, event: dict[str, object] | None) -> None:
        """Emit an event to the state stream."""
        self._state_stream.publish(event)

    def get_device(self, host: str) -> Device | None:
        """Return the Kasa device for *host*, or None if not registered."""
        return self._registry.get(host)

    async def get_devices(self) -> list[Device]:
        """Return a list of all registered Kasa devices, updating their info first."""
        devices = list(self._registry.values())
        await asyncio.gather(*(dev.update() for dev in devices))
        return devices

    async def discover(self) -> None:
        """Discover Kasa devices on the network and register them."""
        try:
            logger.info("Sending kasa discovery request...")

            devices = await Discover.discover()
            await asyncio.gather(*(self._register_discovered_device(dev) for dev in devices.values()))

        except Exception:
            logger.exception("Failed to discover Kasa devices")

    async def set_state(self, host: str, active: bool) -> Device:
        """Set the power state of the Kasa device at *host* to *active* (True for on, False for off)."""
        dev = self._require_device(host)

        try:
            if active:
                await dev.turn_on()
                logger.info("Turned on Kasa device at %s", host)
            else:
                await dev.turn_off()
                logger.info("Turned off Kasa device at %s", host)

            await dev.update()  # Update device info after sending command
            self._emit(self._system_state.record_kasa_state(dev.host, dev.is_on))
            return dev

        except KasaException:
            logger.exception("Kasa error controlling device at %s", host)
            self._remove_device(host)
            raise  # Re-raise to be handled by API layer
        except Exception:
            logger.exception("Failed to control Kasa device at %s", host)
            raise  # Re-raise to be handled by API layer

    async def _register_discovered_device(self, dev: Device) -> None:
        """Register a discovered Kasa device, updating its info first."""
        await dev.update()
        logger.info("Discovered Kasa device: %s (%s)", dev.alias if dev.alias is not None else '<No Alias>', dev.host)
        self._registry[dev.host] = dev
        self._emit(self._system_state.register_kasa_device(dev.host, dev.alias or "", dev.model, dev.is_on))

    def _require_device(self, host: str) -> Device:
        """Return the Kasa device for *host*, raising KeyError if not registered."""
        device = self._registry.get(host)
        if device is None:
            logger.error("No Kasa device found at %s", host)
            raise KeyError("No Kasa device found")  # Raise KeyError to be handled by API layer
        return device

    def _remove_device(self, host: str) -> None:
        self._registry.pop(host, None)  # Remove from registry if it becomes unresponsive
        self._emit(self._system_state.mark_kasa_unavailable(host))

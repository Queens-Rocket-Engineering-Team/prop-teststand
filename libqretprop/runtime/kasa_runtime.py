import asyncio
import logging

from kasa import Device, Discover, KasaException


logger = logging.getLogger(__name__)


class KasaRuntime:
    def __init__(self) -> None:
        self._registry: dict[str, Device] = {}

    def get_device(self, host: str) -> Device | None:
        return self._registry.get(host)

    async def get_devices(self) -> list[Device]:
        devices = list(self._registry.values())
        await asyncio.gather(*(dev.update() for dev in devices))
        return devices

    async def discover_kasa_devices(self) -> None:
        try:
            logger.info("Sending kasa discovery request...")

            devices = await Discover.discover()
            await asyncio.gather(*(self._register_discovered_device(dev) for dev in devices.values()))

        except Exception as e:
            logger.error(f"Failed to discover Kasa devices: {e}")

    async def set_kasa_device_state(self, host: str, active: bool) -> Device:
        dev = self._require_device(host)

        try:
            await self._set_power_state(dev, host, active)
            await dev.update()  # Update device info after sending command
            return dev

        except KasaException as ke:
            logger.error(f"Kasa error controlling device at {host}: {ke}")
            self._remove_device(host)
            raise  # Re-raise to be handled by API layer
        except Exception as e:
            logger.error(f"Failed to control Kasa device at {host}: {e}")
            raise  # Re-raise to be handled by API layer

    async def _register_discovered_device(self, dev: Device) -> None:
        await dev.update()
        logger.info(f"Discovered Kasa device: {dev.alias if dev.alias is not None else '<No Alias>'} ({dev.host})")
        self._registry[dev.host] = dev

    def _require_device(self, host: str) -> Device:
        device = self._registry.get(host)
        if device is None:
            logger.error(f"No Kasa device found at {host}")
            raise KeyError(f"No Kasa device found at {host}")  # Raise KeyError to be handled by API layer
        return device

    async def _set_power_state(self, dev: Device, host: str, active: bool) -> None:
        if active:
            await dev.turn_on()
            logger.info(f"Turned on Kasa device at {host}")
        else:
            await dev.turn_off()
            logger.info(f"Turned off Kasa device at {host}")

    def _remove_device(self, host: str) -> None:
        self._registry.pop(host, None)  # Remove from registry if it becomes unresponsive

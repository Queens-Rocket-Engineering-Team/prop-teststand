import logging

from kasa import Device, Discover, KasaException


logger = logging.getLogger(__name__)
kasa_registry: dict[str, Device] = {}


async def discover_kasa_devices() -> None:
    try:
        logger.info("Sending kasa discovery request...")

        devices = await Discover.discover()
        for dev in devices.values():
            await dev.update()
            logger.info(f"Discovered Kasa device: {dev.alias if dev.alias is not None else '<No Alias>'} ({dev.host})")
            kasa_registry[dev.host] = dev

    except Exception as e:
        logger.error(f"Failed to discover Kasa devices: {e}")

async def set_kasa_device_state(host: str, active: bool) -> None:
    if host not in kasa_registry:
        logger.error(f"No Kasa device found at {host}")
        raise KeyError(f"No Kasa device found at {host}")  # Raise KeyError to be handled by API layer

    dev = kasa_registry[host]

    try:
        if active:
            await dev.turn_on()
            logger.info(f"Turned on Kasa device at {host}")
        else:
            await dev.turn_off()
            logger.info(f"Turned off Kasa device at {host}")

        await dev.update()  # Update device info after sending command

    except KasaException as ke:
        logger.error(f"Kasa error controlling device at {host}: {ke}")
        if host in kasa_registry:
            del kasa_registry[host]  # Remove from registry if it becomes unresponsive
        raise  # Re-raise to be handled by API layer
    except Exception as e:
        logger.error(f"Failed to control Kasa device at {host}: {e}")
        raise  # Re-raise to be handled by API layer

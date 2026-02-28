
from kasa import Device, Discover, KasaException

import libqretprop.mylogging as ml


kasaRegistry: dict[str, Device] = {}


async def discoverKasaDevices() -> None:
    try:
        ml.slog("Sending kasa discovery request...")

        devices = await Discover.discover()
        for dev in devices.values():
            await dev.update()
            ml.slog(f"Discovered Kasa device: {dev.alias if dev.alias is not None else '<No Alias>'} ({dev.host})")
            kasaRegistry[dev.host] = dev

    except Exception as e:
        ml.elog(f"Failed to discover Kasa devices: {e}")

async def setKasaDeviceState(host: str, active: bool) -> None:
    if host not in kasaRegistry:
        ml.elog(f"No Kasa device found at {host}")
        raise KeyError(f"No Kasa device found at {host}") # Raise KeyError to be handled by API layer

    dev = kasaRegistry[host]

    try:
        if active:
            await dev.turn_on()
            ml.slog(f"Turned on Kasa device at {host}")
        else:
            await dev.turn_off()
            ml.slog(f"Turned off Kasa device at {host}")

        await dev.update()  # Update device info after sending command

    except KasaException as ke:
        ml.elog(f"Kasa error controlling device at {host}: {ke}")
        if host in kasaRegistry:
            del kasaRegistry[host]  # Remove from registry if it becomes unresponsive
        raise  # Re-raise to be handled by API layer
    except Exception as e:
        ml.elog(f"Failed to control Kasa device at {host}: {e}")
        raise  # Re-raise to be handled by API layer
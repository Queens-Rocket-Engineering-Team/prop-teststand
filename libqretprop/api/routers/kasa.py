import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from libqretprop.api.deps import get_runtime
from libqretprop.runtime.services import RuntimeServices


logger = logging.getLogger(__name__)
router = APIRouter(tags=["kasa"])


class KasaDeviceInfo(BaseModel):
    alias: str
    host: str
    model: str
    active: bool


@router.get("/v1/kasa", summary="Get the list of discovered Kasa devices")
async def get_kasa_devices(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> list[KasaDeviceInfo]:
    try:
        return [
            KasaDeviceInfo(
                alias=dev.alias if dev.alias is not None else "",
                host=dev.host,
                model=dev.model,
                active=dev.is_on,
            )
            for dev in await rt.kasa_runtime.get_devices()
        ]
    except Exception as e:
        logger.error(f"Failed to get Kasa device info: {e}")
        raise HTTPException(500, "Failed to get Kasa device info")


@router.get("/v1/kasa/discover", summary="Discover Kasa devices on the network")
async def discover_kasa_devices(rt: Annotated[RuntimeServices, Depends(get_runtime)]) -> list[KasaDeviceInfo]:
    logger.info("User sent Kasa discover command")
    await rt.kasa_runtime.discover_kasa_devices()

    try:
        return [
            KasaDeviceInfo(
                alias=dev.alias if dev.alias is not None else "",
                host=dev.host,
                model=dev.model,
                active=dev.is_on,
            )
            for dev in await rt.kasa_runtime.get_devices()
        ]
    except Exception as e:
        logger.error(f"Failed to get Kasa device info: {e}")
        raise HTTPException(500, "Failed to get Kasa device info")


@router.post("/v1/kasa", summary="Control a Kasa device's power state")
async def control_kasa_device(
    rt: Annotated[RuntimeServices, Depends(get_runtime)],
    host: str,
    active: bool,
) -> KasaDeviceInfo:
    logger.info(f"User sent Kasa control command to {host}: active={active}")

    kasa_runtime = rt.kasa_runtime

    if kasa_runtime.get_device(host) is None:
        raise HTTPException(404, f"No Kasa device found at {host}")

    try:
        dev = await kasa_runtime.set_kasa_device_state(host, active)
        alias = dev.alias if dev.alias is not None else ""
        return KasaDeviceInfo(alias=alias, host=dev.host, model=dev.model, active=dev.is_on)
    except Exception as e:
        logger.error(f"Error while controlling Kasa device at {host} (active={active}): {repr(e)}")
        raise HTTPException(500, f"Failed to control Kasa device at {host}")

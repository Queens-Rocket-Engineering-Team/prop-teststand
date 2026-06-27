import logging

import aiohttp

import libqretprop.config_manager as config


logger = logging.getLogger(__name__)


def _parse_mediamtx_record_flag(data: dict) -> bool | None:
    record_flag = data.get("record")
    if isinstance(record_flag, bool):
        return record_flag

    item = data.get("item")
    if isinstance(item, dict):
        record_flag = item.get("record")
        if isinstance(record_flag, bool):
            return record_flag

    return None


class MediaMTXClient:
    def _base_url(self) -> str:
        mediamtx_config = config.server_config["services"]["mediamtx"]
        mediamtx_ip = mediamtx_config["ip"]
        mediamtx_port = mediamtx_config["api_port"]
        return f"http://{mediamtx_ip}:{mediamtx_port}"

    async def add_path(
        self,
        http_client: aiohttp.ClientSession,
        path_name: str,
        *,
        source: str,
        record_path: str,
    ) -> None:
        await http_client.post(f"{self._base_url()}/v3/config/paths/add/{path_name}", json={
            "source": source,
            "sourceOnDemand": False,  # Always pull stream even if no viewers to ensure recording works
            "recordPath": record_path,
            "recordSegmentDuration": "2h",  # 2 hours per recording file segment to accommodate long sessions
        }, timeout=aiohttp.ClientTimeout(10))

    async def set_recording(
        self,
        http_client: aiohttp.ClientSession,
        path_name: str,
        *,
        record: bool,
    ) -> aiohttp.ClientResponse:
        return await http_client.patch(f"{self._base_url()}/v3/config/paths/patch/{path_name}", json={
            "record": record,
        }, timeout=aiohttp.ClientTimeout(10))

    async def get_path_record_state(self, http_client: aiohttp.ClientSession, path_name: str) -> bool | None:
        try:
            response = await http_client.get(
                f"{self._base_url()}/v3/config/paths/get/{path_name}",
                timeout=aiohttp.ClientTimeout(10),
            )

            if response.status == 404:
                return None

            if response.status != 200:
                logger.error(f"Failed to read MediaMTX path config for {path_name}: status {response.status}")
                return None

            data = await response.json()
            if not isinstance(data, dict):
                return None

            return _parse_mediamtx_record_flag(data)
        except TimeoutError:
            logger.error(f"MediaMTX path config request timed out for {path_name}")
        except Exception as e:
            logger.error(f"Failed reading MediaMTX path config for {path_name}: {e}")

        return None

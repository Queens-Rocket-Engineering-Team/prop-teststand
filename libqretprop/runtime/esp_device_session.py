from __future__ import annotations
import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, ClassVar

from libqretprop.drivers.esp import ESPDriver, ESPDriverConnectionClosedError
from libqretprop.qlcp.config_parser import parse_config


logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    import socket

    from libqretprop.qlcp.config_models import ControlConfig, SensorConfig
    from libqretprop.runtime.command_types import CommandRecord
    from libqretprop.runtime.esp_connection_runtime import ESPConnectionRuntime


class ESPDeviceSession:
    """One active configured TCP session for a QLCP/ESP device."""

    RESYNC_INTERVAL_S: ClassVar[float] = 600.0
    COMMAND_ACK_TIMEOUT_S: ClassVar[float] = 10.0
    HEARTBEAT_INTERVAL_S: ClassVar[float] = 5.0
    HEARTBEAT_ACK_MISS_LIMIT: ClassVar[int] = 3

    def __init__(
        self,
        tcp_socket: socket.socket,
        address: str,
        config: dict[str, Any],
        *,
        connection_key: str,
    ) -> None:
        parsed_config = parse_config(config)

        self.socket: socket.socket | None = tcp_socket
        self.address = address
        self.connection_key = connection_key
        self.qlcp_config = parsed_config
        self.driver = ESPDriver(tcp_socket, address)

        self.last_sync_time: float | None = None
        self._resync_pending = False
        self.is_responsive = True
        self._missed_heartbeat_acks = 0

        self.monitor_task: asyncio.Task[Any] | None = None
        self.heartbeat_task: asyncio.Task[Any] | None = None

    def start(
        self,
        runtime: ESPConnectionRuntime,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        task_loop = asyncio.get_running_loop() if loop is None else loop
        self.monitor_task = task_loop.create_task(self.monitor(runtime))
        self.heartbeat_task = task_loop.create_task(self.heartbeat(runtime))

    @property
    def name(self) -> str:
        return self.qlcp_config.name

    @property
    def type(self) -> str:
        return self.qlcp_config.device_type

    @property
    def sensors(self) -> dict[str, SensorConfig]:
        return {
            sensor.name: sensor
            for sensor in self.qlcp_config.sensors_by_id.values()
        }

    @property
    def controls(self) -> dict[str, ControlConfig]:
        return {
            control.name.upper(): control
            for control in self.qlcp_config.controls_by_id.values()
        }

    def needs_resync(self) -> bool:
        """Return True when a TIMESYNC is due for this session."""
        return (
            not self._resync_pending
            and self.last_sync_time is not None
            and time.monotonic() - self.last_sync_time > self.RESYNC_INTERVAL_S
        )

    def mark_resync_sent(self) -> None:
        """Record that a TIMESYNC was sent and an ACK is pending."""
        self._resync_pending = True

    def mark_synced(self) -> None:
        """Record that the TIMESYNC ACK was received."""
        self._resync_pending = False

    def register_missed_heartbeat(self) -> bool:
        """Increment missed-heartbeat-ACK counter.

        Returns True when the miss limit is reached (session should be removed).
        """
        self._missed_heartbeat_acks += 1
        return self._missed_heartbeat_acks >= self.HEARTBEAT_ACK_MISS_LIMIT

    @property
    def missed_heartbeat_count(self) -> int:
        """Number of consecutive missed heartbeat ACKs since the last successful one."""
        return self._missed_heartbeat_acks

    def mark_unresponsive(self) -> None:
        """Mark this session as unresponsive after missing too many heartbeat ACKs."""
        self.is_responsive = False

    def reset_heartbeat_misses(self) -> None:
        """Reset heartbeat miss state after a successful heartbeat ACK."""
        self._missed_heartbeat_acks = 0
        self.is_responsive = True

    async def monitor(self, runtime: ESPConnectionRuntime) -> None:
        """Read TCP packets and delegate session side effects to the runtime."""
        try:
            while True:
                if self.socket is None:
                    logger.error(f"Device {self.name} has no socket.")
                    runtime.remove_device(self)
                    break

                try:
                    packet = await self.driver.read_packet()
                except ESPDriverConnectionClosedError:
                    logger.error(f"Device {self.name} disconnected.")
                    runtime.remove_device(self)
                    break

                logger.debug(f"Decoded {type(packet).__name__} from {self.name}")
                await runtime.handle_packet(self, packet)

                if self.needs_resync():
                    self.mark_resync_sent()
                    await runtime.send_timesync(self)

        except asyncio.CancelledError:
            logger.info(f"Stopped monitoring {self.name}")
            raise
        except Exception as e:
            logger.error(f"Error receiving response from {self.name}: {e}")
            runtime.remove_device(self)

    async def heartbeat(self, runtime: ESPConnectionRuntime) -> None:
        """Run this session's heartbeat loop while policy stays in runtime."""
        while True:
            if self.socket:
                if runtime.expire_command_timeouts(self):
                    break

                if not await runtime.send_heartbeat(self):
                    break

            await asyncio.sleep(self.HEARTBEAT_INTERVAL_S)

    def record_timesync_ack(self, command: CommandRecord | None) -> None:
        if command is None:
            return
        self.last_sync_time = time.monotonic()
        self.mark_synced()

    def record_heartbeat_ack(self, command: CommandRecord | None) -> None:
        if command is None:
            return

        self.reset_heartbeat_misses()

    def control_name_for_id(self, control_id: int | None) -> str | None:
        if control_id is None:
            return None

        control = self.qlcp_config.controls_by_id.get(control_id)
        if control is None:
            return None

        return control.name

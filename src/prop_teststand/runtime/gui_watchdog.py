"""Safety watchdog: ESTOP every control node when no GUI has been connected for too long.

The server has no command channel to the GUI and no GUI-level heartbeat; the only
liveness signal is whether anyone holds a ``/ws/state`` WebSocket open.  If nobody has
for ``timeout_s``, the operator is gone and the stand is left holding whatever actuator
states it was last commanded into, so we fan ESTOP out to every registered node.

The watchdog is armed at construction, so a server that boots and is never opened in a
GUI still safes itself.  Once tripped it latches: each control node receives exactly one
ESTOP per trip, keyed by ``connection_key``, so a node that connects (or reconnects on a
fresh TCP session) while the GUI is still absent is safed too, without re-spamming nodes
that already got the packet.  A GUI reconnecting re-arms the watchdog.

ESTOP is fire-and-forget (QLCP answers it with STATUS, not ACK), so a successful send
means only that the TCP write succeeded -- not that the node reached its safe state.
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Protocol

from prop_teststand.runtime.metrics import Metrics


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


logger = logging.getLogger(__name__)

# How long every /ws/state client may be absent before the stand is safed.
GUI_WATCHDOG_TIMEOUT_S = 600.0
# How often GUI liveness is sampled. Also the granularity at which a node connecting
# while the watchdog is already tripped gets its ESTOP.
GUI_WATCHDOG_POLL_INTERVAL_S = 5.0


class _ClientCounter(Protocol):
    @property
    def client_count(self) -> int: ...


class _EstopTarget(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def connection_key(self) -> str: ...


class _EstopSender(Protocol):
    def get_registered_devices(self) -> Mapping[str, _EstopTarget]: ...

    async def emergency_stop(self, session: Any) -> bool: ...


class GUIWatchdog:
    """Sends ESTOP to all registered control nodes after a sustained GUI outage."""

    def __init__(
        self,
        *,
        state_stream: _ClientCounter,
        esp_runtime: _EstopSender,
        metrics: Metrics | None = None,
        timeout_s: float = GUI_WATCHDOG_TIMEOUT_S,
        poll_interval_s: float = GUI_WATCHDOG_POLL_INTERVAL_S,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.state_stream = state_stream
        self.esp_runtime = esp_runtime
        self.metrics = metrics or Metrics()
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._time_fn = time_fn
        self._last_seen_s = time_fn()
        self._tripped = False
        self._estopped_keys: set[str] = set()

    @property
    def tripped(self) -> bool:
        """True once the watchdog has fired and while it stays latched."""
        return self._tripped

    async def run(self) -> None:
        """Poll GUI liveness forever, safing the stand when it stays absent past the timeout."""
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("GUI watchdog check failed")
            await asyncio.sleep(self.poll_interval_s)

    async def check(self) -> None:
        """Run one watchdog tick."""
        now = self._time_fn()

        if self.state_stream.client_count > 0:
            self._last_seen_s = now
            if self._tripped:
                logger.info("GUI reconnected after watchdog trip. Re-arming watchdog.")
                self._tripped = False
                self._estopped_keys.clear()
            return

        if self._tripped:
            # Still latched: only safe nodes that appeared since the trip.
            await self._estop_unsafed()
            return

        elapsed_s = now - self._last_seen_s
        if elapsed_s < self.timeout_s:
            return

        await self._trip(elapsed_s)

    async def _trip(self, elapsed_s: float) -> None:
        """Latch the watchdog and safe every currently registered control node."""
        # Latched before sending so a send that raises cannot re-trip on the next tick.
        self._tripped = True

        devices = self.esp_runtime.get_registered_devices()
        targeted = [device.name for device in devices.values()]
        if targeted:
            logger.error(
                "No GUI connected for %.0fs. Sending ESTOP to all control nodes: %s",
                elapsed_s,
                ", ".join(targeted),
            )
        else:
            logger.warning("No GUI connected for %.0fs, but no control nodes are registered to ESTOP.", elapsed_s)

        sent = await self._estop_unsafed()
        self.metrics.record_gui_watchdog_trip(elapsed_s=elapsed_s, targeted=len(targeted), sent=sent)

    async def _estop_unsafed(self) -> int:
        """ESTOP every registered node not already safed during this trip. Returns the number sent."""
        sent: list[str] = []
        failed: list[str] = []
        for device in self.esp_runtime.get_registered_devices().values():
            if device.connection_key in self._estopped_keys:
                continue
            # Recorded regardless of the outcome: a failed send drops the session from the
            # registry, and its replacement arrives with a fresh connection_key.
            self._estopped_keys.add(device.connection_key)
            if await self.esp_runtime.emergency_stop(device):
                sent.append(device.name)
            else:
                failed.append(device.name)

        if failed:
            logger.error("GUI watchdog ESTOP failed to send to: %s", ", ".join(failed))
        if sent:
            logger.warning("GUI watchdog ESTOP sent to: %s", ", ".join(sent))
        return len(sent)

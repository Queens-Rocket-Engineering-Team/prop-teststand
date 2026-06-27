from __future__ import annotations
import asyncio
import contextlib
import re
from queue import Empty, Full, Queue

from libqretprop.runtime.metrics import Metrics
from libqretprop.runtime.ws_fanout import BoundedWebSocketFanout, JsonMessage


LOG_STREAM_METRIC_LABEL = "logs"
MAX_LOG_QUEUE_SIZE = 50000
PIPELINE_BATCH_SIZE = 256
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(value: str) -> str:
    return ANSI_ESCAPE.sub("", value)


class LogStream(BoundedWebSocketFanout):
    """Thread-safe ingress plus async WebSocket fan-out for server logs."""

    def __init__(
        self,
        *,
        max_ingress_queue: int = MAX_LOG_QUEUE_SIZE,
        max_client_queue: int = 1024,
        drain_batch_size: int = PIPELINE_BATCH_SIZE,
        metrics: Metrics | None = None,
    ) -> None:
        super().__init__(
            stream_metric_label=LOG_STREAM_METRIC_LABEL,
            max_queue=max_client_queue,
            metrics=metrics,
        )
        self._ingress: Queue[JsonMessage] = Queue(maxsize=max_ingress_queue)
        self._drain_batch_size = drain_batch_size
        self._dropped_ingress_records = 0

    @property
    def dropped_ingress_records(self) -> int:
        return self._dropped_ingress_records

    def enqueue(self, message: JsonMessage) -> None:
        try:
            self._ingress.put_nowait(message)
        except Full:
            self._dropped_ingress_records += 1
            with contextlib.suppress(Empty):
                self._ingress.get_nowait()
            with contextlib.suppress(Full):
                self._ingress.put_nowait(message)

    async def run(self) -> None:
        while True:
            messages = await asyncio.to_thread(self._next_batch)
            for message in messages:
                if self._clients:
                    self.publish_message(message)

    def _next_batch(self) -> list[JsonMessage]:
        try:
            first = self._ingress.get(timeout=0.5)
        except Empty:
            return []

        batch = [first]
        while len(batch) < self._drain_batch_size:
            try:
                batch.append(self._ingress.get_nowait())
            except Empty:
                break
        return batch

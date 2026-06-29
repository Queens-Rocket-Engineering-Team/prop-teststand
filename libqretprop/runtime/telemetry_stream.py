from __future__ import annotations
from typing import TYPE_CHECKING, Any

from libqretprop.runtime.metrics import Metrics
from libqretprop.runtime.ws_fanout import BoundedWebSocketFanout


if TYPE_CHECKING:
    from libqretprop.runtime.telemetry_ingest import TelemetryBatch


STREAM_METRIC_LABEL = "telemetry_raw"


class TelemetryStreamRuntime(BoundedWebSocketFanout):
    """Forwards full-rate telemetry batches to ``/ws/telemetry/raw`` clients.

    Each client gets a bounded queue and an independent send loop. ``publish_batch``
    is synchronous and non-blocking so it is safe to call directly from the UDP ingest
    loop: a slow or stalled client never blocks ingest, its batches are dropped instead.
    """

    def __init__(self, *, max_queue: int = 256, metrics: Metrics | None = None) -> None:
        super().__init__(
            stream_metric_label=STREAM_METRIC_LABEL,
            max_queue=max_queue,
            metrics=metrics or Metrics(),
        )

    def serialize_batch(self, batch: TelemetryBatch) -> dict[str, Any]:
        return {
            "type": "telemetry.raw_batch",
            "device_name": batch.device_name,
            "device_address": batch.device_address,
            "connection_key": batch.connection_key,
            "timestamp_s": batch.timestamp_s,
            "timestamp_source": batch.timestamp_source,
            "timestamp_synced": batch.timestamp_synced,
            "readings": [
                {
                    "sensor_id": reading.sensor_id,
                    "sensor_name": reading.sensor_name,
                    "value": reading.value,
                    "unit": reading.unit_name,
                    "sensor_type": reading.sensor_type,
                }
                for reading in batch.readings
            ],
        }

    def publish_batch(self, batch: TelemetryBatch) -> None:
        """Queue a serialized batch to every connected client without blocking ingest."""
        if not self._clients:
            return

        message = self.serialize_batch(batch)
        self.publish_message(message)

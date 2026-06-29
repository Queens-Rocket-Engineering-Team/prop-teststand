from __future__ import annotations
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


def _label(value: Any) -> str:
    name = getattr(value, "name", None)
    return str(name if name is not None else value)


@dataclass(slots=True)
class _LatencySummary:
    """A summary of latency observations for a specific request type.

    Includes count, total, min, max, and last observed latency.
    """
    count: int = 0
    total: float = 0.0
    min_value: float | None = None
    max_value: float | None = None
    last: float | None = None

    """Record a new latency observation."""
    def observe(self, value: float) -> None:
        """Record a new latency observation."""
        value = max(0.0, value)
        self.count += 1
        self.total += value
        self.min_value = value if self.min_value is None else min(self.min_value, value)
        self.max_value = value if self.max_value is None else max(self.max_value, value)
        self.last = value

    """Return a dict summary of the latency observations."""
    def to_dict(self) -> dict[str, float | int]:
        """Return a dict summary of the latency observations."""
        if self.count == 0:
            return {}
        return {
            "count": self.count,
            "avg": self.total / self.count,
            "min": self.min_value if self.min_value is not None else 0.0,
            "max": self.max_value if self.max_value is not None else 0.0,
            "last": self.last if self.last is not None else 0.0,
        }


class Metrics:
    """In-process launch metrics exposed as JSON diagnostics."""

    # Window size for calculating DATA packet throughput
    DATA_PACKET_RATE_WINDOW_S = 10.0

    def __init__(
        self,
        *,
        time_fn: Callable[[], float] | None = None,
        recent_event_limit: int = 100,
    ) -> None:
        if time_fn is None:
            self._wall_time_fn = time.time
            self._monotonic_fn = time.monotonic
        else:
            self._wall_time_fn = time_fn
            self._monotonic_fn = time_fn
        self._started_wall_s = self._wall_time_fn()
        self._started_monotonic_s = self._monotonic_fn()
        self._recent_events: deque[dict[str, object]] = deque(maxlen=recent_event_limit)

        self._telemetry_aggregate: defaultdict[str, float] = defaultdict(float)
        self._telemetry_by_device: defaultdict[str, defaultdict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        self._telemetry_data_packet_times: deque[float] = deque()
        self._telemetry_data_packet_times_by_device: defaultdict[str, deque[float]] = defaultdict(deque)
        self._telemetry_decode_errors: defaultdict[str, float] = defaultdict(float)
        self._telemetry_dropped_batches: defaultdict[str, float] = defaultdict(float)

        self._command_totals: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._command_rtt_seconds: defaultdict[str, _LatencySummary] = defaultdict(_LatencySummary)

        self._ws_clients: dict[str, int] = {}

        self._http_totals: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._http_duration_seconds: defaultdict[tuple[str, str], _LatencySummary] = defaultdict(_LatencySummary)

        self._device_connections_total = 0
        self._device_disconnections_total: defaultdict[str, int] = defaultdict(int)
        self._heartbeat_misses_total: defaultdict[str, int] = defaultdict(int)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot of the current metrics."""
        now_s = self._now()
        now_wall_s = self._wall_now()
        return {
            "generated_unix_ms": self._unix_ms(now_wall_s),
            "server": {
                "started_unix_ms": self._unix_ms(self._started_wall_s),
                "uptime_s": max(0.0, now_s - self._started_monotonic_s),
            },
            "telemetry": {
                "ingest": {
                    "aggregate": self._telemetry_metric_snapshot(
                        self._telemetry_aggregate,
                        data_packet_times=self._telemetry_data_packet_times,
                        now_s=now_s,
                    ),
                    "by_device": {
                        device: self._telemetry_metric_snapshot(
                            counters,
                            data_packet_times=self._telemetry_data_packet_times_by_device.get(device),
                            now_s=now_s,
                        )
                        for device, counters in sorted(self._telemetry_by_device.items())
                    },
                },
                "decode_errors": {
                    "total": self._counter_totals(self._telemetry_decode_errors),
                },
                "streams": {
                    "dropped_batches_total": self._counter_totals(self._telemetry_dropped_batches),
                },
            },
            "commands": {
                "outcomes_total": self._nested_command_totals(),
                "rtt_seconds": {
                    packet_type: summary.to_dict()
                    for packet_type, summary in sorted(self._command_rtt_seconds.items())
                    if summary.count > 0
                },
            },
            "websockets": {
                "clients": dict(sorted(self._ws_clients.items())),
            },
            "http": {
                "requests_total": self._nested_http_totals(),
                "duration_seconds": self._nested_http_durations(),
            },
            "device_lifecycle": {
                "connections_total": self._device_connections_total,
                "disconnections_total": dict(sorted(self._device_disconnections_total.items())),
                "heartbeat_misses_total": dict(sorted(self._heartbeat_misses_total.items())),
            },
            "recent_events": list(self._recent_events),
        }

    def record_telemetry_datagram(self, byte_count: int, *, device: str | None = None) -> None:
        """Record the receipt of a telemetry UDP datagram."""
        self._telemetry_aggregate["udp_bytes"] += byte_count
        if device is not None:
            self._telemetry_by_device[device]["udp_bytes"] += byte_count

    def record_telemetry_data_packet(self, device: str) -> None:
        """Record the receipt of a telemetry DATA packet."""
        now_s = self._now()
        self._telemetry_aggregate["data_packets"] += 1
        self._telemetry_by_device[device]["data_packets"] += 1
        self._record_data_packet_sample(self._telemetry_data_packet_times, now_s)
        self._record_data_packet_sample(self._telemetry_data_packet_times_by_device[device], now_s)

    def record_telemetry_readings(self, device: str, count: int) -> None:
        """Record the number of sensor readings received in a telemetry DATA packet."""
        if count <= 0:
            return
        self._telemetry_aggregate["readings"] += count
        self._telemetry_by_device[device]["readings"] += count

    def record_telemetry_decode_error(self, kind: str) -> None:
        """Record a telemetry decode error of the given kind."""
        self._telemetry_decode_errors[kind] += 1
        self._add_event("telemetry.decode_error", "warning", f"Telemetry decode error: {kind}", error_kind=kind)

    def record_telemetry_dropped_batch(self, stream: str) -> None:
        """Record a telemetry batch dropped due to a slow client."""
        self._telemetry_dropped_batches[stream] += 1
        self._add_event(
            "telemetry.dropped_batch",
            "warning",
            f"{stream} dropped a telemetry batch for a slow client",
            stream=stream,
        )

    def set_ws_clients(self, stream: str, count: int) -> None:
        """Set the current number of connected WebSocket clients for a given stream."""
        self._ws_clients[stream] = count

    def record_command_acked(self, packet_type: Any, rtt_seconds: float) -> None:
        """Record that a command was ACKed and its round-trip time."""
        label = _label(packet_type)
        self._command_totals[(label, "acked")] += 1
        self._command_rtt_seconds[label].observe(rtt_seconds)

    def record_command_nacked(
        self,
        packet_type: Any,
        *,
        device: str | None = None,
        error_code: Any | None = None,
    ) -> None:
        """Record that a command was NACKed, optionally with an error code."""
        label = _label(packet_type)
        self._command_totals[(label, "nacked")] += 1
        self._add_event(
            "command.nacked",
            "warning",
            f"{label} command NACKed",
            packet_type=label,
            device=device,
            error_code=_label(error_code) if error_code is not None else None,
        )

    def record_command_timed_out(self, packet_type: Any, *, device: str | None = None) -> None:
        """Record that a command timed out waiting for an ACK or NACK."""
        label = _label(packet_type)
        self._command_totals[(label, "timed_out")] += 1
        self._add_event("command.timed_out", "warning", f"{label} command timed out", packet_type=label, device=device)

    def record_command_connection_failed(
        self,
        packet_type: Any,
        *,
        device: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Record that a command failed because the connection closed before an ACK or NACK."""
        label = _label(packet_type)
        self._command_totals[(label, "connection_failed")] += 1
        self._add_event(
            "command.connection_failed",
            "error",
            f"{label} command failed because the connection closed",
            packet_type=label,
            device=device,
            reason=reason,
        )

    def record_device_connection(self, *, device: str | None = None) -> None:
        """Record that a device has connected."""
        self._device_connections_total += 1
        self._add_event("device.connected", "info", "Device connection registered", device=device)

    def record_device_disconnection(self, reason: str, *, device: str | None = None) -> None:
        """Record that a device has disconnected, with a reason."""
        self._device_disconnections_total[reason] += 1
        self._add_event("device.disconnected", "warning", f"Device disconnected: {reason}", device=device, reason=reason)

    def record_heartbeat_miss(self, device: str) -> None:
        """Record that a device has missed a HEARTBEAT ACK."""
        self._heartbeat_misses_total[device] += 1
        self._add_event("heartbeat.missed", "warning", f"{device} missed a HEARTBEAT ACK", device=device)

    def observe_http_request(self, method: str, path: str, status: int | str, duration_seconds: float) -> None:
        """Record an HTTP request and its duration, optionally logging errors."""
        status_label = str(status)
        key = (method, status_label)
        self._http_totals[key] += 1
        self._http_duration_seconds[key].observe(duration_seconds)
        status_int = int(status_label)
        if status_int >= 400:
            self._add_event(
                "http.error",
                "error" if status_int >= 500 else "warning",
                f"{method} {path} returned {status_label}",
                method=method,
                path=path,
                status=status_label,
            )

    def _now(self) -> float:
        """Return the current monotonic time in seconds."""
        return self._monotonic_fn()

    def _wall_now(self) -> float:
        """Return the current wall-clock time in seconds since the epoch."""
        return self._wall_time_fn()

    @staticmethod
    def _unix_ms(timestamp_s: float) -> int:
        """Convert a timestamp in seconds to milliseconds."""
        return int(timestamp_s * 1000)

    def _record_data_packet_sample(self, samples: deque[float], now_s: float) -> None:
        """Record a sample of a telemetry DATA packet arrival time and prune old samples."""
        samples.append(now_s)
        self._prune_data_packet_samples(samples, now_s)

    def _prune_data_packet_samples(self, samples: deque[float], now_s: float) -> None:
        """Prune telemetry DATA packet samples older than the rate window."""
        cutoff_s = now_s - self.DATA_PACKET_RATE_WINDOW_S
        while samples and samples[0] <= cutoff_s:
            samples.popleft()

    def _add_event(self, kind: str, severity: str, message: str, **context: object | None) -> None:
        """Add a recent event to the metrics, with optional context."""
        event: dict[str, object] = {
            "at_unix_ms": self._unix_ms(self._wall_now()),
            "kind": kind,
            "severity": severity,
            "message": message,
        }
        event.update({k: v for k, v in context.items() if v is not None})
        self._recent_events.append(event)

    def _telemetry_metric_snapshot(
        self,
        counters: dict[str, float],
        *,
        data_packet_times: deque[float] | None = None,
        now_s: float | None = None,
    ) -> dict[str, object]:
        """Return a snapshot of telemetry metrics, including data packet rate if samples are provided."""
        snapshot: dict[str, object] = {
            f"{metric}_total": self._number(total)
            for metric, total in sorted(counters.items())
        }
        if data_packet_times is not None and "data_packets" in counters:
            self._prune_data_packet_samples(data_packet_times, now_s or self._now())
            snapshot["data_packets_per_s"] = len(data_packet_times) / self.DATA_PACKET_RATE_WINDOW_S
        return snapshot

    @staticmethod
    def _counter_totals(counters: dict[str, float]) -> dict[str, int | float]:
        """Return a snapshot of counter totals."""
        return {key: Metrics._number(total) for key, total in sorted(counters.items())}

    def _nested_command_totals(self) -> dict[str, dict[str, int]]:
        """Return a nested dict of command outcome totals by packet type and outcome."""
        nested: dict[str, dict[str, int]] = {}
        for (packet_type, outcome), count in sorted(self._command_totals.items()):
            nested.setdefault(packet_type, {})[outcome] = count
        return nested

    def _nested_http_totals(self) -> dict[str, dict[str, int]]:
        """Return a nested dict of HTTP request totals by method and status code."""
        nested: dict[str, dict[str, int]] = {}
        for (method, status), count in sorted(self._http_totals.items()):
            nested.setdefault(method, {})[status] = count
        return nested

    def _nested_http_durations(self) -> dict[str, dict[str, dict[str, float | int]]]:
        """Return a nested dict of HTTP request duration summaries by method and status code."""
        nested: dict[str, dict[str, dict[str, float | int]]] = {}
        for (method, status), summary in sorted(self._http_duration_seconds.items()):
            nested.setdefault(method, {})[status] = summary.to_dict()
        return nested

    @staticmethod
    def _number(value: float) -> int | float:
        """Return an int if the value is an integer, otherwise return the float."""
        return int(value) if value.is_integer() else value

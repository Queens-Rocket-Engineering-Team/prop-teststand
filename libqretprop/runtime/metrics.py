from __future__ import annotations
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


RATE_WINDOWS_S = (5, 15, 60)
MAX_RATE_WINDOW_S = max(RATE_WINDOWS_S)


def _label(value: Any) -> str:
    name = getattr(value, "name", None)
    return str(name if name is not None else value)


def _clean_context(context: dict[str, object | None]) -> dict[str, object]:
    return {
        key: value
        for key, value in context.items()
        if value is not None
    }


@dataclass(slots=True)
class _RollingCounter:
    total: float = 0.0
    buckets: dict[int, float] = field(default_factory=dict)
    first_seen_s: float | None = None

    def add(self, amount: float, now_s: float) -> None:
        if amount <= 0:
            return
        if self.first_seen_s is None:
            self.first_seen_s = now_s
        self.total += amount
        bucket = int(now_s)
        self.buckets[bucket] = self.buckets.get(bucket, 0.0) + amount
        self.prune(now_s)

    def rates(self, now_s: float) -> dict[str, float]:
        self.prune(now_s)
        if self.first_seen_s is None:
            return {f"{window}s": 0.0 for window in RATE_WINDOWS_S}

        rates: dict[str, float] = {}
        for window in RATE_WINDOWS_S:
            start_bucket = int(now_s - window) + 1
            amount = sum(
                value
                for bucket, value in self.buckets.items()
                if bucket >= start_bucket
            )
            elapsed = max(1.0, min(float(window), now_s - self.first_seen_s))
            rates[f"{window}s"] = amount / elapsed
        return rates

    def prune(self, now_s: float) -> None:
        oldest_bucket = int(now_s - MAX_RATE_WINDOW_S)
        for bucket in tuple(self.buckets):
            if bucket < oldest_bucket:
                self.buckets.pop(bucket)


@dataclass(slots=True)
class _LatencySummary:
    count: int = 0
    total: float = 0.0
    min_value: float | None = None
    max_value: float | None = None
    last: float | None = None
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=256))

    def observe(self, value: float) -> None:
        value = max(0.0, value)
        self.count += 1
        self.total += value
        self.min_value = value if self.min_value is None else min(self.min_value, value)
        self.max_value = value if self.max_value is None else max(self.max_value, value)
        self.last = value
        self.samples.append(value)

    def to_dict(self) -> dict[str, float | int]:
        if self.count == 0:
            return {}
        return {
            "count": self.count,
            "avg": self.total / self.count,
            "min": self.min_value if self.min_value is not None else 0.0,
            "max": self.max_value if self.max_value is not None else 0.0,
            "last": self.last if self.last is not None else 0.0,
            "recent_p95": self._recent_percentile(0.95),
        }

    def _recent_percentile(self, percentile: float) -> float:
        if not self.samples:
            return 0.0
        sorted_samples = sorted(self.samples)
        index = max(0, math.ceil(percentile * len(sorted_samples)) - 1)
        return sorted_samples[index]


class Metrics:
    """In-process launch metrics exposed as JSON diagnostics."""

    def __init__(
        self,
        *,
        time_fn: Callable[[], float] | None = None,
        recent_event_limit: int = 100,
        enabled: bool = True,
    ) -> None:
        if time_fn is None:
            self._wall_time_fn = time.time
            self._monotonic_fn = time.monotonic
        else:
            self._wall_time_fn = time_fn
            self._monotonic_fn = time_fn
        self._started_wall_s = self._wall_time_fn()
        self._started_monotonic_s = self._monotonic_fn()
        self._enabled = enabled
        self._recent_events: deque[dict[str, object]] = deque(maxlen=recent_event_limit)

        self._telemetry_aggregate: dict[str, _RollingCounter] = {}
        self._telemetry_by_device: dict[str, dict[str, _RollingCounter]] = {}
        self._telemetry_decode_errors: dict[str, _RollingCounter] = {}
        self._telemetry_dropped_batches: dict[str, _RollingCounter] = {}

        self._command_totals: dict[tuple[str, str], int] = {}
        self._command_rtt_seconds: dict[str, _LatencySummary] = {}

        self._ws_clients: dict[str, int] = {}

        self._http_totals: dict[tuple[str, str], int] = {}
        self._http_duration_seconds: dict[tuple[str, str], _LatencySummary] = {}

        self._device_connections_total = 0
        self._device_disconnections_total: dict[str, int] = {}
        self._heartbeat_misses_total: dict[str, int] = {}

    def to_dict(self) -> dict[str, object]:
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
                    "aggregate": self._telemetry_metric_snapshot(self._telemetry_aggregate, now_s),
                    "by_device": {
                        device: self._telemetry_metric_snapshot(counters, now_s)
                        for device, counters in sorted(self._telemetry_by_device.items())
                    },
                },
                "decode_errors": {
                    "total": self._counter_totals(self._telemetry_decode_errors),
                    "per_s": self._counter_rates(self._telemetry_decode_errors, now_s),
                },
                "streams": {
                    "dropped_batches_total": self._counter_totals(self._telemetry_dropped_batches),
                    "dropped_batches_per_s": self._counter_rates(self._telemetry_dropped_batches, now_s),
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
        if not self._enabled:
            return
        now_s = self._now()
        self._counter(self._telemetry_aggregate, "udp_bytes").add(byte_count, now_s)
        if device is not None:
            self._device_counter(device, "udp_bytes").add(byte_count, now_s)

    def record_telemetry_data_packet(self, device: str) -> None:
        if not self._enabled:
            return
        now_s = self._now()
        self._counter(self._telemetry_aggregate, "data_packets").add(1, now_s)
        self._device_counter(device, "data_packets").add(1, now_s)

    def record_telemetry_readings(self, device: str, count: int) -> None:
        if not self._enabled or count <= 0:
            return
        now_s = self._now()
        self._counter(self._telemetry_aggregate, "readings").add(count, now_s)
        self._device_counter(device, "readings").add(count, now_s)

    def record_telemetry_decode_error(self, kind: str) -> None:
        if not self._enabled:
            return
        self._counter(self._telemetry_decode_errors, kind).add(1, self._now())
        self._add_event(
            "telemetry.decode_error",
            "warning",
            f"Telemetry decode error: {kind}",
            error_kind=kind,
        )

    def record_telemetry_dropped_batch(self, stream: str) -> None:
        if not self._enabled:
            return
        self._counter(self._telemetry_dropped_batches, stream).add(1, self._now())
        self._add_event(
            "telemetry.dropped_batch",
            "warning",
            f"{stream} dropped a telemetry batch for a slow client",
            stream=stream,
        )

    def set_ws_clients(self, stream: str, count: int) -> None:
        if self._enabled:
            self._ws_clients[stream] = count

    def record_command_acked(self, packet_type: Any, rtt_seconds: float) -> None:
        if not self._enabled:
            return
        packet_type_label = _label(packet_type)
        self._increment_command(packet_type_label, "acked")
        self._latency(self._command_rtt_seconds, packet_type_label).observe(rtt_seconds)

    def record_command_nacked(
        self,
        packet_type: Any,
        *,
        device: str | None = None,
        error_code: Any | None = None,
    ) -> None:
        if not self._enabled:
            return
        packet_type_label = _label(packet_type)
        error_code_label = _label(error_code) if error_code is not None else None
        self._increment_command(packet_type_label, "nacked")
        self._add_event(
            "command.nacked",
            "warning",
            f"{packet_type_label} command NACKed",
            packet_type=packet_type_label,
            device=device,
            error_code=error_code_label,
        )

    def record_command_timed_out(self, packet_type: Any, *, device: str | None = None) -> None:
        if not self._enabled:
            return
        packet_type_label = _label(packet_type)
        self._increment_command(packet_type_label, "timed_out")
        self._add_event(
            "command.timed_out",
            "warning",
            f"{packet_type_label} command timed out",
            packet_type=packet_type_label,
            device=device,
        )

    def record_command_connection_failed(
        self,
        packet_type: Any,
        *,
        device: str | None = None,
        reason: str | None = None,
    ) -> None:
        if not self._enabled:
            return
        packet_type_label = _label(packet_type)
        self._increment_command(packet_type_label, "connection_failed")
        self._add_event(
            "command.connection_failed",
            "error",
            f"{packet_type_label} command failed because the connection closed",
            packet_type=packet_type_label,
            device=device,
            reason=reason,
        )

    def record_device_connection(self, *, device: str | None = None) -> None:
        if not self._enabled:
            return
        self._device_connections_total += 1
        self._add_event(
            "device.connected",
            "info",
            "Device connection registered",
            device=device,
        )

    def record_device_disconnection(self, reason: str, *, device: str | None = None) -> None:
        if not self._enabled:
            return
        self._device_disconnections_total[reason] = self._device_disconnections_total.get(reason, 0) + 1
        self._add_event(
            "device.disconnected",
            "warning",
            f"Device disconnected: {reason}",
            device=device,
            reason=reason,
        )

    def record_heartbeat_miss(self, device: str) -> None:
        if not self._enabled:
            return
        self._heartbeat_misses_total[device] = self._heartbeat_misses_total.get(device, 0) + 1
        self._add_event(
            "heartbeat.missed",
            "warning",
            f"{device} missed a HEARTBEAT ACK",
            device=device,
        )

    def observe_http_request(self, method: str, path: str, status: int | str, duration_seconds: float) -> None:
        if not self._enabled:
            return
        status_label = str(status)
        key = (method, status_label)
        self._http_totals[key] = self._http_totals.get(key, 0) + 1
        self._latency(self._http_duration_seconds, key).observe(duration_seconds)
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
        return self._monotonic_fn()

    def _wall_now(self) -> float:
        return self._wall_time_fn()

    @staticmethod
    def _unix_ms(timestamp_s: float) -> int:
        return int(timestamp_s * 1000)

    def _counter(self, counters: dict[str, _RollingCounter], key: str) -> _RollingCounter:
        counter = counters.get(key)
        if counter is None:
            counter = _RollingCounter()
            counters[key] = counter
        return counter

    def _device_counter(self, device: str, metric: str) -> _RollingCounter:
        counters = self._telemetry_by_device.get(device)
        if counters is None:
            counters = {}
            self._telemetry_by_device[device] = counters
        return self._counter(counters, metric)

    @staticmethod
    def _latency(
        summaries: dict[Any, _LatencySummary],
        key: Any,
    ) -> _LatencySummary:
        summary = summaries.get(key)
        if summary is None:
            summary = _LatencySummary()
            summaries[key] = summary
        return summary

    def _add_event(
        self,
        kind: str,
        severity: str,
        message: str,
        **context: object | None,
    ) -> None:
        now_s = self._wall_now()
        event: dict[str, object] = {
            "at_unix_ms": self._unix_ms(now_s),
            "kind": kind,
            "severity": severity,
            "message": message,
        }
        event.update(_clean_context(context))
        self._recent_events.append(event)

    def _increment_command(self, packet_type: str, outcome: str) -> None:
        key = (packet_type, outcome)
        self._command_totals[key] = self._command_totals.get(key, 0) + 1

    def _telemetry_metric_snapshot(
        self,
        counters: dict[str, _RollingCounter],
        now_s: float,
    ) -> dict[str, object]:
        snapshot: dict[str, object] = {}
        for metric, counter in sorted(counters.items()):
            snapshot[f"{metric}_total"] = self._number(counter.total)
            snapshot[f"{metric}_per_s"] = counter.rates(now_s)
        return snapshot

    @staticmethod
    def _counter_totals(counters: dict[str, _RollingCounter]) -> dict[str, int | float]:
        return {
            key: Metrics._number(counter.total)
            for key, counter in sorted(counters.items())
        }

    @staticmethod
    def _counter_rates(counters: dict[str, _RollingCounter], now_s: float) -> dict[str, dict[str, float]]:
        return {
            key: counter.rates(now_s)
            for key, counter in sorted(counters.items())
        }

    def _nested_command_totals(self) -> dict[str, dict[str, int]]:
        nested: dict[str, dict[str, int]] = {}
        for (packet_type, outcome), count in sorted(self._command_totals.items()):
            nested.setdefault(packet_type, {})[outcome] = count
        return nested

    def _nested_http_totals(self) -> dict[str, dict[str, int]]:
        nested: dict[str, dict[str, int]] = {}
        for (method, status), count in sorted(self._http_totals.items()):
            nested.setdefault(method, {})[status] = count
        return nested

    def _nested_http_durations(self) -> dict[str, dict[str, dict[str, float | int]]]:
        nested: dict[str, dict[str, dict[str, float | int]]] = {}
        for (method, status), summary in sorted(self._http_duration_seconds.items()):
            nested.setdefault(method, {})[status] = summary.to_dict()
        return nested

    @staticmethod
    def _number(value: float) -> int | float:
        return int(value) if value.is_integer() else value


NULL_METRICS = Metrics(enabled=False)

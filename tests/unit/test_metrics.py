from typing import Any, cast

from libqretprop.qlcp.enums import ErrorCode, PacketType
from libqretprop.runtime.metrics import Metrics


def _snapshot(metrics: Metrics) -> dict[str, Any]:
    return cast("dict[str, Any]", metrics.to_dict())


class Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_metrics_snapshot_records_live_telemetry_totals() -> None:
    clock = Clock()
    metrics = Metrics(time_fn=clock)

    metrics.record_telemetry_datagram(100, device="PANDA")
    metrics.record_telemetry_data_packet("PANDA")
    metrics.record_telemetry_readings("PANDA", count=2)
    clock.advance(2.0)
    metrics.record_telemetry_datagram(200, device="PANDA")
    metrics.record_telemetry_data_packet("PANDA")
    metrics.record_telemetry_readings("PANDA", count=3)

    telemetry = _snapshot(metrics)["telemetry"]
    aggregate = telemetry["ingest"]["aggregate"]
    by_device = telemetry["ingest"]["by_device"]["PANDA"]

    assert aggregate["udp_bytes_total"] == 300
    assert "udp_bytes_per_s" not in aggregate
    assert aggregate["data_packets_total"] == 2
    assert "batches_total" not in aggregate
    assert aggregate["readings_total"] == 5
    assert by_device == aggregate


def test_metrics_snapshot_records_errors_streams_and_events() -> None:
    clock = Clock()
    metrics = Metrics(time_fn=clock, recent_event_limit=3)

    metrics.record_telemetry_decode_error("decode")
    metrics.record_telemetry_dropped_batch("telemetry_raw")
    metrics.record_heartbeat_miss("PANDA")
    metrics.record_device_disconnection("connection_cleanup", device="PANDA")

    snapshot = _snapshot(metrics)
    telemetry = snapshot["telemetry"]

    assert telemetry["decode_errors"]["total"]["decode"] == 1
    assert "per_s" not in telemetry["decode_errors"]
    assert "loss" not in telemetry
    assert telemetry["streams"]["dropped_batches_total"]["telemetry_raw"] == 1
    assert "dropped_batches_per_s" not in telemetry["streams"]
    assert snapshot["device_lifecycle"]["heartbeat_misses_total"]["PANDA"] == 1
    assert len(snapshot["recent_events"]) == 3
    assert snapshot["recent_events"][-1]["kind"] == "device.disconnected"


def test_metrics_snapshot_records_command_and_http_latency_summaries() -> None:
    clock = Clock()
    metrics = Metrics(time_fn=clock)

    metrics.record_command_acked(PacketType.CONTROL, 0.1)
    metrics.record_command_acked(PacketType.CONTROL, 0.5)
    metrics.record_command_nacked(PacketType.STREAM_START, device="PANDA", error_code=ErrorCode.INVALID_PARAM)
    metrics.record_command_timed_out(PacketType.HEARTBEAT, device="PANDA")
    metrics.observe_http_request("GET", "/v1/metrics", 200, 0.02)
    metrics.observe_http_request("POST", "/v1/command", 500, 0.5)

    snapshot = _snapshot(metrics)

    assert snapshot["commands"]["outcomes_total"]["CONTROL"]["acked"] == 2
    assert snapshot["commands"]["outcomes_total"]["STREAM_START"]["nacked"] == 1
    assert snapshot["commands"]["outcomes_total"]["HEARTBEAT"]["timed_out"] == 1
    assert snapshot["commands"]["rtt_seconds"]["CONTROL"] == {
        "count": 2,
        "avg": 0.3,
        "min": 0.1,
        "max": 0.5,
        "last": 0.5,
    }
    assert snapshot["http"]["requests_total"]["GET"]["200"] == 1
    assert snapshot["http"]["requests_total"]["POST"]["500"] == 1
    assert snapshot["http"]["duration_seconds"]["POST"]["500"]["last"] == 0.5
    assert snapshot["recent_events"][-1]["kind"] == "http.error"


def test_metrics_snapshot_does_not_duplicate_state_snapshot_fields() -> None:
    metrics = Metrics(time_fn=lambda: 100.0)

    snapshot = _snapshot(metrics)
    serialized = str(snapshot)

    assert "devices" not in snapshot
    assert "connected" not in snapshot["device_lifecycle"]
    assert "controls" not in serialized
    assert "configs" not in serialized
    assert "pending" not in serialized
    assert 'heartbeat": {"' not in serialized

from __future__ import annotations
import asyncio
import contextlib
from typing import Any, cast

from fastapi import WebSocket

from libqretprop.runtime.telemetry_ingest import TelemetryBatch, TelemetryReading
from libqretprop.runtime.telemetry_stream import TelemetryStreamRuntime


class FakeWebSocket:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.accepted = False
        self.closed = False
        self.sent: list[dict[str, Any]] = []
        self._fail_after = fail_after
        self._send_count = 0

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, Any]) -> None:
        if self._fail_after is not None and self._send_count >= self._fail_after:
            raise RuntimeError("websocket send failed")

        self._send_count += 1
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True


def _as_websocket(websocket: FakeWebSocket) -> WebSocket:
    return cast(WebSocket, websocket)


def _make_batch() -> TelemetryBatch:
    return TelemetryBatch(
        device_name="MockDevice",
        device_address="10.0.0.184",
        connection_key="esp-1",
        timestamp_s=98269.746,
        readings=(
            TelemetryReading(
                sensor_id=0,
                sensor_name="PT101",
                value=123.4,
                unit_name="PSI",
                sensor_type="pressure_transducer",
            ),
        ),
    )


def test_serialize_batch_matches_wire_format() -> None:
    runtime = TelemetryStreamRuntime()

    message = runtime.serialize_batch(_make_batch())

    assert message == {
        "type": "telemetry.raw_batch",
        "device_name": "MockDevice",
        "device_address": "10.0.0.184",
        "connection_key": "esp-1",
        "timestamp_s": 98269.746,
        "readings": [
            {
                "sensor_id": 0,
                "sensor_name": "PT101",
                "value": 123.4,
                "unit": "PSI",
                "sensor_type": "pressure_transducer",
            },
        ],
    }


def test_connect_client_accepts_and_registers() -> None:
    async def run() -> None:
        runtime = TelemetryStreamRuntime()
        websocket = FakeWebSocket()

        await runtime.connect_client(_as_websocket(websocket))

        assert websocket.accepted is True
        assert runtime.client_count == 1

    asyncio.run(run())


def test_publish_batch_queues_serialized_message_to_each_client() -> None:
    async def run() -> None:
        runtime = TelemetryStreamRuntime()
        first = FakeWebSocket()
        second = FakeWebSocket()
        await runtime.connect_client(_as_websocket(first))
        await runtime.connect_client(_as_websocket(second))

        runtime.publish_batch(_make_batch())

        expected = runtime.serialize_batch(_make_batch())
        for websocket in (first, second):
            queue = runtime._clients[_as_websocket(websocket)]
            assert queue.qsize() == 1
            assert queue.get_nowait() == expected

    asyncio.run(run())


def test_full_queue_does_not_block_and_increments_dropped_batches() -> None:
    async def run() -> None:
        runtime = TelemetryStreamRuntime(max_queue=1)
        websocket = FakeWebSocket()
        await runtime.connect_client(_as_websocket(websocket))

        runtime.publish_batch(_make_batch())  # fills the queue
        runtime.publish_batch(_make_batch())  # dropped, must not block or raise

        assert runtime.dropped_batches == 1
        assert runtime._clients[_as_websocket(websocket)].qsize() == 1

    asyncio.run(run())


def test_publish_batch_with_no_clients_is_a_noop() -> None:
    runtime = TelemetryStreamRuntime()

    runtime.publish_batch(_make_batch())

    assert runtime.dropped_batches == 0


def test_handle_client_sends_published_batch() -> None:
    async def run() -> None:
        runtime = TelemetryStreamRuntime()
        websocket = FakeWebSocket()
        task = asyncio.create_task(runtime.handle_client(_as_websocket(websocket)))
        await asyncio.sleep(0)  # let connect_client run and reach queue.get()

        runtime.publish_batch(_make_batch())
        await asyncio.sleep(0)  # let the send loop drain one message

        assert websocket.sent[0]["type"] == "telemetry.raw_batch"
        assert websocket.sent[0]["device_name"] == "MockDevice"

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_handle_client_removes_client_on_send_failure() -> None:
    async def run() -> None:
        runtime = TelemetryStreamRuntime()
        websocket = FakeWebSocket(fail_after=0)
        task = asyncio.create_task(runtime.handle_client(_as_websocket(websocket)))
        await asyncio.sleep(0)
        assert runtime.client_count == 1

        runtime.publish_batch(_make_batch())  # send loop wakes, send fails, client cleaned up
        await task

        assert runtime.client_count == 0
        assert websocket.closed is True

    asyncio.run(run())

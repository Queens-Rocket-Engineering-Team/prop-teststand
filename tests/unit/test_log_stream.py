from __future__ import annotations
import asyncio
import logging
from collections.abc import Generator
from typing import Any, cast

import pytest
from fastapi import WebSocket

import libqretprop.runtime.logging as log_config
from libqretprop.runtime.log_stream import LogStream


pytestmark = pytest.mark.real_logging


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed = False
        self.sent: list[dict[str, Any]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True


def _as_websocket(websocket: FakeWebSocket) -> WebSocket:
    return cast(WebSocket, websocket)


def _reset_logging() -> None:
    logger = logging.getLogger(log_config.LOGGER_NAME)
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False


@pytest.fixture(autouse=True)
def _clean_logging() -> Generator[None]:
    _reset_logging()
    yield
    _reset_logging()


def test_log_stream_ingress_queue_keeps_newest_message_when_full() -> None:
    stream = LogStream(max_ingress_queue=1)

    stream.enqueue({"level": "INFO", "data": "old", "timestamp_ws": "00:00:00"})
    stream.enqueue({"level": "INFO", "data": "new", "timestamp_ws": "00:00:01"})

    assert stream.dropped_ingress_records == 1
    assert stream._next_batch()[0]["data"] == "new"


def test_websocket_wire_format_uses_levels_and_has_no_ansi() -> None:
    stream = LogStream()
    handler = log_config.WebSocketLogHandler(stream)
    handler.setFormatter(log_config.LogFormatter(color=True))

    records = [
        logging.LogRecord(
            name=log_config.LOGGER_NAME,
            level=level,
            pathname=__file__,
            lineno=1,
            msg=f"{logging.getLevelName(level).lower()} message",
            args=(),
            exc_info=None,
        )
        for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL)
    ]

    for record in records:
        handler.emit(record)

    messages = stream._next_batch()
    assert {message["level"] for message in messages} == {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    for message in messages:
        assert set(message) == {"level", "data", "timestamp_ws"}
        assert "\033" not in message["data"]
        assert message["data"].endswith(f" {message['level'].lower()} message")


def test_dual_sink_sends_system_and_error_logs_to_stdout_and_websocket(capsys: pytest.CaptureFixture[str]) -> None:
    stream = LogStream()
    log_config.configure_logging(stream)
    logger = logging.getLogger(f"{log_config.LOGGER_NAME}.test")

    logger.info("system online")
    logger.error("error online")

    stdout = capsys.readouterr().out
    messages = stream._next_batch()

    assert "system online" in stdout
    assert "error online" in stdout
    assert {message["level"] for message in messages} == {"INFO", "ERROR"}
    assert {message["data"].rsplit(" ", 1)[-1] for message in messages} == {"online"}
    assert all("\033" not in message["data"] for message in messages)


def test_debug_logs_skip_info_stdout_but_reach_websocket(capsys: pytest.CaptureFixture[str]) -> None:
    stream = LogStream()
    log_config.configure_logging(stream, stdout_level=logging.INFO)
    logger = logging.getLogger(f"{log_config.LOGGER_NAME}.test")

    logger.debug("debug details")
    logger.debug("packet details")

    stdout = capsys.readouterr().out
    messages = stream._next_batch()

    assert stdout == ""
    assert [message["level"] for message in messages] == ["DEBUG", "DEBUG"]
    assert {tuple(message["data"].rsplit(" ", 2)[-2:]) for message in messages} == {
        ("debug", "details"),
        ("packet", "details"),
    }


def test_log_messages_publish_to_connected_client_queue() -> None:
    async def run() -> None:
        stream = LogStream(max_client_queue=4)
        websocket = FakeWebSocket()
        await stream.connect_client(_as_websocket(websocket))

        stream.enqueue({"level": "DEBUG", "data": "[00:00:00] hello", "timestamp_ws": "00:00:00"})
        for message in stream._next_batch():
            stream.publish_message(message)

        queued = stream._clients[_as_websocket(websocket)].get_nowait()
        assert queued["level"] == "DEBUG"
        assert queued["data"].endswith(" hello")

    asyncio.run(run())

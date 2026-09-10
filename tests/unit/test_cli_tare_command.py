from __future__ import annotations
import asyncio
from typing import Any, cast
from unittest.mock import MagicMock

from vector.daemons.cli_terminal import handle_server_command
from vector.runtime.command_tracker import CommandTracker
from vector.runtime.services import RuntimeServices
from vector.runtime.telemetry_ingest import TareCaptureError
from vector.state.system_state import SystemState


def _make_runtime(capture: Any = None) -> tuple[RuntimeServices, SystemState, list[dict]]:
    """Build a runtime with a real SystemState and a telemetry_runtime whose capture is stubbed."""
    system_state = SystemState(command_tracker=CommandTracker())
    published: list[dict] = []

    runtime = MagicMock(spec=RuntimeServices)
    runtime.system_state = system_state
    runtime.state_stream = MagicMock()
    runtime.state_stream.publish.side_effect = published.append
    runtime.telemetry_runtime = MagicMock()
    if capture is not None:
        runtime.telemetry_runtime.capture_tare_offset.side_effect = capture
    return cast("RuntimeServices", runtime), system_state, published


def _run(runtime: RuntimeServices, *args: str) -> None:
    asyncio.run(handle_server_command(runtime, "TARE", list(args)))


def test_tare_captures_an_offset_and_publishes_it() -> None:
    runtime, system_state, published = _make_runtime(capture=lambda *_a, **_kw: (15.0, "PANDA", 16))

    _run(runtime, "PT101")

    assert system_state.tare_for("PT101") == 15.0
    assert published == [{"type": "tare.updated", "state_version": 1, "sensor_name": "PT101", "offset": 15.0}]


def test_tare_passes_an_explicit_sample_count() -> None:
    runtime, _system_state, _published = _make_runtime(capture=lambda *_a, **_kw: (1.0, "PANDA", 200))

    _run(runtime, "PT101", "200")

    runtime.telemetry_runtime.capture_tare_offset.assert_called_once_with("PT101", samples=200)


def test_tare_clear_removes_the_offset() -> None:
    runtime, system_state, published = _make_runtime()
    system_state.set_tare("PT101", 15.0)
    published.clear()

    _run(runtime, "PT101", "clear")

    assert system_state.tare_for("PT101") == 0.0
    assert published == [{"type": "tare.cleared", "state_version": 2, "sensor_name": "PT101"}]


def test_tare_clear_on_an_untared_sensor_publishes_nothing() -> None:
    runtime, _system_state, published = _make_runtime()

    _run(runtime, "PT101", "clear")

    assert published == []


def test_tare_capture_failure_leaves_state_untouched() -> None:
    def _fail(*_args: object, **_kwargs: object) -> tuple[float, str, int]:
        raise TareCaptureError("no samples")

    runtime, system_state, published = _make_runtime(capture=_fail)

    _run(runtime, "PT101")

    assert system_state.tare_for("PT101") == 0.0
    assert published == []


def test_tare_rejects_a_non_numeric_sample_count() -> None:
    runtime, _system_state, published = _make_runtime()

    _run(runtime, "PT101", "lots")

    runtime.telemetry_runtime.capture_tare_offset.assert_not_called()
    assert published == []


def test_tare_rejects_a_sample_count_larger_than_the_buffer() -> None:
    runtime, _system_state, published = _make_runtime()

    _run(runtime, "PT101", "9999")

    runtime.telemetry_runtime.capture_tare_offset.assert_not_called()
    assert published == []


def test_bare_tare_lists_applied_offsets_without_changing_state() -> None:
    runtime, system_state, published = _make_runtime()
    system_state.set_tare("PT101", 15.0)
    published.clear()

    _run(runtime)

    assert system_state.snapshot()["tares"] == {"PT101": 15.0}
    assert published == []

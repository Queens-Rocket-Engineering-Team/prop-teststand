import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from libqretprop.DeviceControllers import deviceTools


@pytest.fixture(autouse=True)
def _mute_device_tool_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deviceTools.ml, "slog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deviceTools.ml, "plog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deviceTools.ml, "elog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deviceTools.ml, "log", lambda *_args, **_kwargs: None)


def _make_device(
    *,
    address: str = "10.0.0.2",
    connection_key: str = "conn-a",
    name: str = "PANDA",
) -> Any:
    return SimpleNamespace(
        address=address,
        connection_key=connection_key,
        name=name,
        socket=None,
        controls={"VALVE": object()},
    )


def test_stale_device_removal_does_not_remove_current_connection() -> None:
    old_device = _make_device(connection_key="conn-a")
    current_device = _make_device(connection_key="conn-b")

    deviceTools.deviceRegistry.clear()
    deviceTools.deviceRegistry[current_device.address] = current_device
    try:
        deviceTools.removeDevice(old_device)

        assert deviceTools.deviceRegistry[current_device.address] is current_device
    finally:
        deviceTools.deviceRegistry.clear()


def test_current_device_removal_removes_registry_entry() -> None:
    device = _make_device()

    deviceTools.deviceRegistry.clear()
    deviceTools.deviceRegistry[device.address] = device
    try:
        deviceTools.removeDevice(device)

        assert device.address not in deviceTools.deviceRegistry
    finally:
        deviceTools.deviceRegistry.clear()


def test_reconnecting_same_device_name_disconnects_old_address() -> None:
    old_device = _make_device(address="10.0.0.2", connection_key="conn-a", name="PANDA")
    other_device = _make_device(address="10.0.0.4", connection_key="conn-other", name="OTTER")

    deviceTools.deviceRegistry.clear()
    deviceTools.deviceRegistry[old_device.address] = old_device
    deviceTools.deviceRegistry[other_device.address] = other_device
    try:
        deviceTools._disconnectRegisteredDevicesWithName("PANDA")

        assert old_device.address not in deviceTools.deviceRegistry
        assert deviceTools.deviceRegistry[other_device.address] is other_device
    finally:
        deviceTools.deviceRegistry.clear()


def test_control_with_no_socket_removes_device() -> None:
    device = _make_device()

    deviceTools.deviceRegistry.clear()
    deviceTools.deviceRegistry[device.address] = device
    try:
        asyncio.run(deviceTools.setControl(device, "VALVE", "OPEN"))

        assert device.address not in deviceTools.deviceRegistry
    finally:
        deviceTools.deviceRegistry.clear()


def test_estop_with_no_socket_removes_device() -> None:
    device = _make_device()

    deviceTools.deviceRegistry.clear()
    deviceTools.deviceRegistry[device.address] = device
    try:
        asyncio.run(deviceTools.emergencyStop(device))

        assert device.address not in deviceTools.deviceRegistry
    finally:
        deviceTools.deviceRegistry.clear()


def test_legacy_esp_log_sink_keeps_gui_parse_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr(deviceTools.ml, "log", messages.append)
    device = _make_device(name="PANDA")
    sink = deviceTools._LegacyESPLogSink()

    sink.device_connected(device)
    sink.control_status(device, "VALVE", "OPEN")
    sink.device_disconnected(device)

    assert messages == [
        "PANDA CONNECTED",
        "PANDA STATUS VALVE OPEN",
        "PANDA DISCONNECTED",
    ]

import asyncio
import contextlib
from unittest.mock import MagicMock, patch

import pytest

from libqretprop.DeviceControllers import deviceTools
from libqretprop.Devices.ESPDevice import ESPDevice


MINIMAL_CONFIG = {
    "deviceName": "TestDevice",
    "deviceType": "Sensor Monitor",
}

@pytest.fixture
def mock_socket():
    sock = MagicMock()
    sock.fileno.return_value = 5  # asyncio needs a real-looking fd
    return sock

@pytest.fixture
def device(mock_socket):
    with patch("libqretprop.Devices.ESPDevice.asyncio.create_task"):
        d = ESPDevice(mock_socket, "192.168.1.10", MINIMAL_CONFIG)
    return d

def test_handle_heartbeat_ack_resets_state(device):
    device._heartbeat_ack_pending = True
    device._last_heartbeat_sequence = 42
    device._missed_heartbeat_acks = 2
    device.is_responsive = False

    # Call with matching sequence
    device.handleHeartbeatAck(42)

    assert not device._heartbeat_ack_pending
    assert device._missed_heartbeat_acks == 0
    assert device.is_responsive

def test_handle_heartbeat_ack_sequence_mismatch(device):
    device._heartbeat_ack_pending = True
    device._last_heartbeat_sequence = 42
    device._missed_heartbeat_acks = 2
    device.is_responsive = False

    with patch("libqretprop.mylogging.plog") as mock_plog:
        # Call with non-matching sequence
        device.handleHeartbeatAck(99)

        assert not device._heartbeat_ack_pending
        assert device._missed_heartbeat_acks == 0  # ACK still counts as received even if sequence mismatches
        assert device.is_responsive

        mock_plog.assert_called_with(
            f"{device.name} HEARTBEAT ACK sequence mismatch: expected 42, got 99"
        )

@pytest.mark.asyncio
async def test_heartbeat_marks_unresponsive_after_miss_limit(mock_socket):
    with patch("libqretprop.Devices.ESPDevice.asyncio.create_task"):
        d = ESPDevice(mock_socket, "192.168.1.10", MINIMAL_CONFIG)

    d._heartbeat_ack_pending = True
    d._missed_heartbeat_acks = ESPDevice.HEARTBEAT_ACK_MISS_LIMIT - 1

    with patch.object(deviceTools, "removeDevice") as mock_remove, \
         patch("libqretprop.mylogging.elog") as mock_elog:
        task = asyncio.create_task(d.heartbeat())
        await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert d.is_responsive is False
    mock_remove.assert_called_once_with(d)
    mock_elog.assert_called_once_with(f"{d.name} marked unresponsive: missed {ESPDevice.HEARTBEAT_ACK_MISS_LIMIT} HEARTBEAT ACKs")

@pytest.mark.asyncio
async def test_heartbeat_removes_device_on_send_failure(mock_socket):
    with patch("libqretprop.Devices.ESPDevice.asyncio.create_task"):
        d = ESPDevice(mock_socket, "192.168.1.10", MINIMAL_CONFIG)

    d._heartbeat_ack_pending = False

    with patch.object(deviceTools, "removeDevice") as mock_remove, \
         patch.object(asyncio.get_running_loop(), "sock_sendall",
                      side_effect=BrokenPipeError("connection lost")), \
         patch("libqretprop.mylogging.elog") as mock_elog:
        task = asyncio.create_task(d.heartbeat())
        await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    mock_remove.assert_called_once_with(d)
    mock_elog.assert_called_once_with(f"{d.name} heartbeat send failed: connection lost")
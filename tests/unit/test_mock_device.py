from itertools import pairwise

import pytest

from libqretprop.qlcp.config_models import SensorConfig
from libqretprop.qlcp.enums import Unit
from qretproptools.cli.mock_device.mock_device import (
    MOCK_SIGNAL_AMPLITUDE,
    MOCK_SIGNAL_FREQUENCY_HZ,
    sensor_signal_center_amplitude,
    sensor_signal_value,
)


def test_mock_signal_is_time_based_not_sample_based() -> None:
    sensor = SensorConfig(id=0, name="TC101", type="thermocouple", unit=Unit.CELSIUS)

    slow_sample_period_s = 1.0 / 120.0
    fast_sample_period_s = 1.0 / 240.0

    slow_samples = [
        sensor_signal_value(sensor, i * slow_sample_period_s, sensor.id)
        for i in range(5)
    ]
    fast_samples = [
        sensor_signal_value(sensor, i * fast_sample_period_s, sensor.id)
        for i in range(9)
    ]

    assert fast_samples[::2] == pytest.approx(slow_samples)
    assert len(fast_samples) == (2 * len(slow_samples)) - 1
    assert any(abs(sample - previous) > 0.001 for previous, sample in pairwise(fast_samples))


def test_mock_signal_frequency_is_fixed_at_0_25_hz_with_20_unit_amplitude() -> None:
    sensor = SensorConfig(id=0, name="TC101", type="thermocouple", unit=Unit.CELSIUS)
    period_s = 1.0 / MOCK_SIGNAL_FREQUENCY_HZ
    center, amplitude = sensor_signal_center_amplitude(sensor)

    assert MOCK_SIGNAL_FREQUENCY_HZ == 0.25
    assert amplitude == MOCK_SIGNAL_AMPLITUDE == 20.0
    assert sensor_signal_value(sensor, 0.0, sensor.id) == pytest.approx(center)
    assert sensor_signal_value(sensor, period_s / 4.0, sensor.id) == pytest.approx(center + amplitude)
    assert sensor_signal_value(sensor, period_s, sensor.id) == pytest.approx(center)

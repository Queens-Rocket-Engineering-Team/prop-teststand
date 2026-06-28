import pytest

from libqretprop.qlcp.config_models import SensorConfig
from libqretprop.qlcp.config_parser import QLCPConfigError, parse_config
from libqretprop.qlcp.enums import ControlState, Unit


TEST_CONFIG_VALID_FULL = {
    "device_name": "TEST-DEVICE-1",
    "device_type": "Sensor Monitor",
    "sensor_info": {
        "thermocouple": {
            "TC1": {
                "sensor_index": "TC1",
                "type": "K",
                "unit": "C",
            },
        },
        "pressure_transducer": {
            "PT1": {
                "sensor_index": "PT1",
                "resistor_ohms": 350,
                "max_pressure_PSI": 500,
                "unit": "PSI",
            },
            "PT2": {
                "sensor_index": "PT2",
                "resistor_ohms": 400,
                "max_pressure_PSI": 600,
                "unit": "PSI",
            },
        },
        "load_cell": {
            "LC1": {
                "sensor_index": "LC1",
                "load_rating_N": 1000,
                "excitation_V": 5.0,
                "sensitivity_vV": 2.0,
                "unit": "N",
            },
        },
        "resistance_sensor": {
            "RS1": {
                "sensor_index": "RS1",
                "injected_current_uA": 1000,
                "r_short": 50,
                "unit": "ohms",
            },
        },
        "current_sensor": {
            "CS1": {
                "sensor_index": "CS1",
                "shunt_resistor_ohms": 0.1,
                "csa_gain": 50,
                "unit": "A",
            },
        },
    },
    "controls": {
        "VALVE1": {
            "control_index": "VALVE1",
            "type": "solenoid",
            "default_state": "OPEN",
        },
        "VALVE2": {
            "control_index": "VALVE2",
            "type": "solenoid",
            "default_state": "CLOSED",
        },
    },
}


def test_parse_config_valid_full() -> None:
    result = parse_config(TEST_CONFIG_VALID_FULL)

    assert result.name == "TEST-DEVICE-1"
    assert result.device_type == "Sensor Monitor"

    assert len(result.sensors_by_id) == 6

    s = result.sensors_by_id[0]
    assert isinstance(s, SensorConfig)
    assert s.name == "TC1"
    assert s.type == "thermocouple"
    assert s.unit == Unit.CELSIUS

    s = result.sensors_by_id[1]
    assert isinstance(s, SensorConfig)
    assert s.name == "PT1"
    assert s.type == "pressure_transducer"
    assert s.unit == Unit.PSI

    s = result.sensors_by_id[2]
    assert isinstance(s, SensorConfig)
    assert s.name == "PT2"
    assert s.type == "pressure_transducer"
    assert s.unit == Unit.PSI

    s = result.sensors_by_id[3]
    assert isinstance(s, SensorConfig)
    assert s.name == "LC1"
    assert s.type == "load_cell"
    assert s.unit == Unit.NEWTONS

    s = result.sensors_by_id[4]
    assert isinstance(s, SensorConfig)
    assert s.name == "RS1"
    assert s.type == "resistance_sensor"
    assert s.unit == Unit.OHMS

    s = result.sensors_by_id[5]
    assert isinstance(s, SensorConfig)
    assert s.name == "CS1"
    assert s.type == "current_sensor"
    assert s.unit == Unit.AMPS

    assert len(result.controls_by_id) == 2
    assert result.controls_by_id[0].name == "VALVE1"
    assert result.controls_by_id[0].default == ControlState.OPEN
    assert result.controls_by_id[0].control_type == "solenoid"
    assert result.controls_by_id[1].name == "VALVE2"
    assert result.controls_by_id[1].default == ControlState.CLOSED
    assert result.controls_by_id[1].control_type == "solenoid"


TEST_CONFIG_SENSORS_ONLY = {
    "device_name": "TEST-DEVICE-2",
    "device_type": "Sensor Monitor",
    "sensor_info": {
        "thermocouple": {
            "TC1": {
                "sensor_index": "TC1",
                "unit": "C",
            },
        },
        "pressure_transducer": {
            "PT1": {
                "sensor_index": "PT1",
                "unit": "PSI",
            },
        },
    },
}


def test_parse_config_sensors_only() -> None:
    result = parse_config(TEST_CONFIG_SENSORS_ONLY)

    assert result.name == "TEST-DEVICE-2"
    assert result.device_type == "Sensor Monitor"

    assert len(result.sensors_by_id) == 2

    s = result.sensors_by_id[0]
    assert isinstance(s, SensorConfig)
    assert s.name == "TC1"
    assert s.type == "thermocouple"
    assert s.unit == Unit.CELSIUS

    s = result.sensors_by_id[1]
    assert isinstance(s, SensorConfig)
    assert s.name == "PT1"
    assert s.type == "pressure_transducer"
    assert s.unit == Unit.PSI

    assert len(result.controls_by_id) == 0


def test_parse_config_known_sensor_types_only_require_common_fields() -> None:
    config = {
        "device_name": "TEST-DEVICE-MINIMAL",
        "device_type": "Sensor Monitor",
        "sensor_info": {
            "thermocouple": {"TC1": {"unit": "C"}},
            "pressure_transducer": {"PT1": {"unit": "PSI"}},
            "load_cell": {"LC1": {"unit": "N"}},
            "resistance_sensor": {"RS1": {"unit": "ohms"}},
            "current_sensor": {"CS1": {"unit": "A"}},
        },
    }

    result = parse_config(config)

    assert [type(sensor) for sensor in result.sensors_by_id.values()] == [SensorConfig] * 5
    assert [sensor.type for sensor in result.sensors_by_id.values()] == [
        "thermocouple",
        "pressure_transducer",
        "load_cell",
        "resistance_sensor",
        "current_sensor",
    ]


TEST_CONFIG_CONTROLS_ONLY = {
    "device_name": "TEST-DEVICE-3",
    "device_type": "Sensor Monitor",
    "controls": {
        "VALVE1": {"control_index": "PT202", "type": "solenoid", "default_state": "OPEN"},
    },
}


def test_parse_config_controls_only() -> None:
    result = parse_config(TEST_CONFIG_CONTROLS_ONLY)

    assert result.name == "TEST-DEVICE-3"
    assert result.device_type == "Sensor Monitor"

    assert len(result.sensors_by_id) == 0

    assert len(result.controls_by_id) == 1
    assert result.controls_by_id[0].name == "VALVE1"
    assert result.controls_by_id[0].default == ControlState.OPEN
    assert result.controls_by_id[0].control_type == "solenoid"


TETS_CONFIG_DUPLICATE_SENSOR_NAMES = {
    "device_name": "TEST-DEVICE-4",
    "device_type": "Sensor Monitor",
    "sensor_info": {
        "thermocouple": {
            "SENSOR1": {"sensor_index": "TC101", "type": "K", "unit": "C"},
        },
        "pressure_transducer": {
            "SENSOR1": {
                "sensor_index": "PT101",
                "resistor_ohms": 350,
                "max_pressure_PSI": 500,
                "unit": "PSI",
            },
        },
    },
}


def test_parse_config_duplicate_sensor_names() -> None:
    result = parse_config(TETS_CONFIG_DUPLICATE_SENSOR_NAMES)

    assert result.name == "TEST-DEVICE-4"
    assert result.device_type == "Sensor Monitor"

    assert len(result.sensors_by_id) == 2
    assert result.sensors_by_id[0].name == "SENSOR1"
    assert result.sensors_by_id[0].type == "thermocouple"
    assert result.sensors_by_id[0].unit == Unit.CELSIUS

    assert result.sensors_by_id[1].name == "SENSOR1"
    assert result.sensors_by_id[1].type == "pressure_transducer"
    assert result.sensors_by_id[1].unit == Unit.PSI

    assert len(result.controls_by_id) == 0


TEST_CONFIG_UNKNOWN_SENSOR = {
    "device_name": "TEST-DEVICE-5",
    "device_type": "Sensor Monitor",
    "sensor_info": {
        "future_sensor": {
            "FS1": {"sensor_index": "FS1", "unit": "unitless"},
        },
    },
}


def test_parse_config_unknown_sensor_falls_back_to_generic_config() -> None:
    result = parse_config(TEST_CONFIG_UNKNOWN_SENSOR)

    assert len(result.sensors_by_id) == 1
    assert type(result.sensors_by_id[0]) is SensorConfig
    assert result.sensors_by_id[0].name == "FS1"
    assert result.sensors_by_id[0].type == "future_sensor"
    assert result.sensors_by_id[0].unit == Unit.UNITLESS


def test_parse_config_invalid_unit_raises() -> None:
    config = {
        "device_name": "TEST-DEVICE-6",
        "device_type": "Sensor Monitor",
        "sensor_info": {
            "thermocouple": {
                "TC1": {
                    "sensor_index": "TC1",
                    "type": "K",
                    "unit": "widgets",
                },
            },
        },
    }

    with pytest.raises(QLCPConfigError, match="Invalid unit: widgets"):
        parse_config(config)


@pytest.mark.parametrize("missing_field", ["type", "default_state"])
def test_parse_config_missing_control_field_raises(missing_field: str) -> None:
    control_details = {
        "type": "solenoid",
        "default_state": "CLOSED",
    }
    del control_details[missing_field]

    config = {
        "device_name": "TEST-DEVICE-6",
        "device_type": "Sensor Monitor",
        "controls": {"VALVE1": control_details},
    }

    with pytest.raises(
        QLCPConfigError,
        match=f"control 'VALVE1' missing required field: {missing_field}",
    ):
        parse_config(config)


@pytest.mark.parametrize("missing_field", ["unit"])
def test_parse_config_missing_sensor_field_raises(missing_field: str) -> None:
    sensor_details = {
        "type": "K",
        "unit": "C",
    }
    del sensor_details[missing_field]

    config = {
        "device_name": "TEST-DEVICE-7",
        "device_type": "Sensor Monitor",
        "sensor_info": {"thermocouple": {"TC1": sensor_details}},
    }

    with pytest.raises(
        QLCPConfigError,
        match=f"thermocouple sensor 'TC1' missing required field: {missing_field}",
    ):
        parse_config(config)

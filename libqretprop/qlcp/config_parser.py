from typing import Any

from libqretprop.qlcp.config_models import (
    ControlConfig,
    CurrentSensorConfig,
    DeviceConfig,
    LoadCellConfig,
    PressureTransducerConfig,
    ResistanceSensorConfig,
    SensorConfig,
    ThermocoupleConfig,
)
from libqretprop.qlcp.enums import ControlState, Unit


class QLCPConfigError(ValueError):
    """Raised when a device CONFIG payload is missing or has invalid fields."""


def parse_config(config: dict[str, Any]) -> DeviceConfig:
    name = require_string_field(config, "device_name", "config")
    device_type = require_string_field(config, "device_type", "config")

    current_sensor_id = 0
    sensors_by_id: dict[int, SensorConfig] = {}

    for sensor_type, sensors in config.get("sensor_info", {}).items():
        for sensor_name, details in sensors.items():
            sensors_by_id[current_sensor_id] = parse_sensor_config(
                sensor_id=current_sensor_id,
                sensor_type=sensor_type,
                sensor_name=sensor_name,
                details=details,
            )
            current_sensor_id += 1

    controls_by_id: dict[int, ControlConfig] = {}

    for control_id, (control_name, details) in enumerate(config.get("controls", {}).items()):
        controls_by_id[control_id] = parse_control_config(
            control_id=control_id,
            control_name=control_name,
            details=details,
        )

    return DeviceConfig(
        name=name,
        device_type=device_type,
        sensors_by_id=sensors_by_id,
        controls_by_id=controls_by_id,
    )


def parse_sensor_config(
    sensor_id: int,
    sensor_type: str,
    sensor_name: str,
    details: dict[str, Any],
) -> SensorConfig:
    context = f"{sensor_type} sensor {sensor_name!r}"
    unit = cast_unit(require_string_field(details, "unit", context))
    base: dict[str, Any] = {
        "id": sensor_id,
        "name": sensor_name,
        "type": sensor_type,
        "unit": unit,
    }

    if sensor_type == "thermocouple":
        return ThermocoupleConfig(
            **base,
            thermo_type=require_string_field(details, "type", context),
        )

    if sensor_type == "pressure_transducer":
        return PressureTransducerConfig(
            **base,
            resistor_ohms=require_field(details, "resistor_ohms", context),
            max_pressure_psi=require_field(details, "max_pressure_PSI", context),
        )

    if sensor_type == "load_cell":
        return LoadCellConfig(
            **base,
            load_rating_n=require_field(details, "load_rating_N", context),
            excitation_v=require_field(details, "excitation_V", context),
            sensitivity_vv=require_field(details, "sensitivity_vV", context),
        )

    if sensor_type == "resistance_sensor":
        return ResistanceSensorConfig(
            **base,
            injected_current_ua=require_field(details, "injected_current_uA", context),
            r_short=require_field(details, "r_short", context),
        )

    if sensor_type == "current_sensor":
        return CurrentSensorConfig(
            **base,
            shunt_resistor_ohms=require_field(details, "shunt_resistor_ohms", context),
            csa_gain=require_field(details, "csa_gain", context),
        )

    return SensorConfig(**base)


def parse_control_config(
    control_id: int,
    control_name: str,
    details: dict[str, Any],
) -> ControlConfig:
    context = f"control {control_name!r}"

    return ControlConfig(
        id=control_id,
        name=control_name,
        default=cast_control_state(require_string_field(details, "default_state", context)),
        control_type=require_string_field(details, "type", context),
    )


def require_field(details: dict[str, Any], field: str, context: str) -> Any:
    try:
        value = details[field]
    except KeyError as err:
        raise QLCPConfigError(f"{context} missing required field: {field}") from err

    if value is None or value == "":
        raise QLCPConfigError(f"{context} missing required field: {field}")

    return value


def require_string_field(details: dict[str, Any], field: str, context: str) -> str:
    value = require_field(details, field, context)

    if not isinstance(value, str):
        raise QLCPConfigError(f"{context} field must be a string: {field}")

    return value


def cast_unit(unit_str: str) -> Unit:
    units = {
        "v": Unit.VOLTS,
        "volt": Unit.VOLTS,
        "volts": Unit.VOLTS,
        "a": Unit.AMPS,
        "amp": Unit.AMPS,
        "amps": Unit.AMPS,
        "c": Unit.CELSIUS,
        "celsius": Unit.CELSIUS,
        "f": Unit.FAHRENHEIT,
        "fahrenheit": Unit.FAHRENHEIT,
        "k": Unit.KELVIN,
        "kelvin": Unit.KELVIN,
        "psi": Unit.PSI,
        "bar": Unit.BAR,
        "pa": Unit.PASCAL,
        "pascal": Unit.PASCAL,
        "pascals": Unit.PASCAL,
        "g": Unit.GRAMS,
        "gram": Unit.GRAMS,
        "grams": Unit.GRAMS,
        "kg": Unit.KILOGRAMS,
        "kilogram": Unit.KILOGRAMS,
        "kilograms": Unit.KILOGRAMS,
        "lb": Unit.POUNDS,
        "lbs": Unit.POUNDS,
        "pound": Unit.POUNDS,
        "pounds": Unit.POUNDS,
        "n": Unit.NEWTONS,
        "newton": Unit.NEWTONS,
        "newtons": Unit.NEWTONS,
        "s": Unit.SECONDS,
        "sec": Unit.SECONDS,
        "second": Unit.SECONDS,
        "seconds": Unit.SECONDS,
        "ms": Unit.MILLISECONDS,
        "millisecond": Unit.MILLISECONDS,
        "milliseconds": Unit.MILLISECONDS,
        "hz": Unit.HERTZ,
        "hertz": Unit.HERTZ,
        "ohm": Unit.OHMS,
        "ohms": Unit.OHMS,
        "unitless": Unit.UNITLESS,
    }

    try:
        return units[unit_str.strip().lower()]
    except KeyError as err:
        message = f"Invalid unit: {unit_str}"
        raise QLCPConfigError(message) from err


def cast_control_state(state_str: str) -> ControlState:
    try:
        return ControlState[state_str.upper()]
    except KeyError as err:
        message = f"Invalid control state: {state_str}"
        raise QLCPConfigError(message) from err

from typing import Any

from prop_teststand.qlcp.config_models import (
    ControlConfig,
    DeviceConfig,
    SensorConfig,
)
from prop_teststand.qlcp.enums import ControlState, ControlType


class QLCPConfigError(ValueError):
    """Raised when a device CONFIG payload is missing or has invalid fields."""


def parse_config(config: dict[str, Any]) -> DeviceConfig:
    name = require_string_field(config, "device_name", "config")

    current_sensor_id = 0
    sensors_by_id: dict[int, SensorConfig] = {}

    for sensor_group, sensors in config.get("sensors", {}).items():
        for sensor_name, details in sensors.items():
            sensors_by_id[current_sensor_id] = parse_sensor_config(
                sensor_id=current_sensor_id,
                sensor_group=sensor_group,
                sensor_name=sensor_name,
                details=details,
            )
            current_sensor_id += 1

    current_control_id = 0
    controls_by_id: dict[int, ControlConfig] = {}

    for control_group, controls in config.get("controls", {}).items():
        for control_name, details in controls.items():
            controls_by_id[current_control_id] = parse_control_config(
                control_id=current_control_id,
                control_name=control_name,
                control_group=control_group,
                details=details,
            )
            current_control_id += 1

    return DeviceConfig(
        name=name,
        sensors_by_id=sensors_by_id,
        controls_by_id=controls_by_id,
    )


def parse_sensor_config(
    sensor_id: int,
    sensor_group: str,
    sensor_name: str,
    details: dict[str, Any],
) -> SensorConfig:
    context = f"{sensor_group} sensor {sensor_name!r}"
    return SensorConfig(
        id=sensor_id,
        name=sensor_name,
        group=sensor_group,
        unit=require_string_field(details, "unit", context),
    )


def parse_control_config(
    control_id: int,
    control_name: str,
    control_group: str,
    details: dict[str, Any],
) -> ControlConfig:
    context = f"control {control_name!r}"

    control_type = cast_control_type(require_string_field(details, "type", context))
    default_state = cast_control_state(control_type, require_string_field(details, "default_state", context))
    unit = details.get("unit") # unit is optional

    return ControlConfig(
        id=control_id,
        name=control_name,
        group=control_group,
        default=default_state,
        type=control_type,
        unit=unit,
    )


def require_field(details: dict[str, Any], field: str, context: str) -> Any:
    try:
        value = details[field]
    except KeyError as err:
        message = f"{context} missing required field: {field}"
        raise QLCPConfigError(message) from err

    if value is None or value == "":
        message = f"{context} missing required field: {field}"
        raise QLCPConfigError(message)

    return value


def require_string_field(details: dict[str, Any], field: str, context: str) -> str:
    value = require_field(details, field, context)

    if not isinstance(value, str):
        message = f"{context} field must be a string: {field}"
        raise QLCPConfigError(message)

    return value

def cast_control_type(type_str: str) -> ControlType:
    try:
        return ControlType[type_str.upper()]
    except KeyError as err:
        message = f"Invalid control type: {type_str}"
        raise QLCPConfigError(message) from err

def cast_control_state(control_type: ControlType, state_str: str) -> ControlState | int | float:
    try:
        match control_type:
            case ControlType.BOOL:
                return ControlState[state_str.upper()]
            case ControlType.UINT32 | ControlType.INT32:
                return int(state_str)
            case ControlType.FLOAT32:
                return float(state_str)
    except (KeyError, ValueError) as err:
        message = f"Invalid control state: {state_str}"
        raise QLCPConfigError(message) from err

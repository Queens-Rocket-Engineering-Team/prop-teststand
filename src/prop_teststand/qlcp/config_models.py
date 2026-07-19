from dataclasses import dataclass

from prop_teststand.qlcp.enums import ControlState, ControlType


@dataclass(slots=True, frozen=True, kw_only=True)
class SensorConfig:
    id: int
    name: str
    group: str
    unit: str

@dataclass(slots=True, frozen=True, kw_only=True)
class ControlConfig:
    id: int
    name: str
    group: str
    default: ControlState | int | float
    type: ControlType


@dataclass(slots=True, frozen=True)
class DeviceConfig:
    name: str
    sensors_by_id: dict[int, SensorConfig]
    controls_by_id: dict[int, ControlConfig]

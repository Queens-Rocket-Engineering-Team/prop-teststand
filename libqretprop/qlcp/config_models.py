from dataclasses import dataclass

from libqretprop.qlcp.enums import ControlState, Unit


@dataclass(slots=True, frozen=True, kw_only=True)
class SensorConfig:
    id: int
    name: str
    type: str
    unit: Unit


@dataclass(slots=True, frozen=True, kw_only=True)
class ControlConfig:
    id: int
    name: str
    default: ControlState
    control_type: str


@dataclass(slots=True, frozen=True)
class DeviceConfig:
    name: str
    device_type: str
    sensors_by_id: dict[int, SensorConfig]
    controls_by_id: dict[int, ControlConfig]

from dataclasses import dataclass
from typing import Any

from libqretprop.qlcp.enums import ControlState, Unit


@dataclass(slots=True, frozen=True, kw_only=True)
class SensorConfig:
    id: int
    name: str
    type: str
    sensor_index: str
    unit: Unit
    raw: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ThermocoupleConfig(SensorConfig):
    thermo_type: str


@dataclass(slots=True, frozen=True, kw_only=True)
class PressureTransducerConfig(SensorConfig):
    resistor_ohms: float
    max_pressure_psi: int


@dataclass(slots=True, frozen=True, kw_only=True)
class LoadCellConfig(SensorConfig):
    load_rating_n: float
    excitation_v: float
    sensitivity_vv: float

    @property
    def full_scale_voltage(self) -> float:
        return self.excitation_v * (self.sensitivity_vv / 1000)


@dataclass(slots=True, frozen=True, kw_only=True)
class ResistanceSensorConfig(SensorConfig):
    injected_current_ua: int
    r_short: float


@dataclass(slots=True, frozen=True, kw_only=True)
class CurrentSensorConfig(SensorConfig):
    shunt_resistor_ohms: float
    csa_gain: int


@dataclass(slots=True, frozen=True, kw_only=True)
class ControlConfig:
    id: int
    name: str
    default: ControlState
    control_index: str
    control_type: str
    raw: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class DeviceConfig:
    name: str
    device_type: str
    sensors_by_id: dict[int, SensorConfig]
    controls_by_id: dict[int, ControlConfig]
    raw_config: dict[str, Any]

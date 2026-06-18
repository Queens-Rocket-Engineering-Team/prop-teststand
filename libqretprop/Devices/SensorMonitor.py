import socket
import time
from typing import Any

from libqretprop.Devices.ESPDevice import ESPDevice
from libqretprop.qlcp.config_models import (
    ControlConfig,
    DeviceConfig,
    SensorConfig,
)
from libqretprop.qlcp.config_parser import parse_config


class SensorMonitor(ESPDevice):
    """Class of device which is an ESP32 that reads sensor data.

    An object will self define itself from a json file structure config file
    received from the device. Deserialization takes place at the parent class
    level, this class works on the JSON object level.

    """

    def __init__(self, socket: socket.socket, address: str, config: dict[str, Any]) -> None:
        parsed_config = parse_config(config)
        super().__init__(socket, address, config)

        # Storing the default information inherited from the parent class
        self.socket = socket
        self.address = address
        self.jsonConfig: dict[str, Any] = config
        self.qlcp_config = parsed_config

        self.name = parsed_config.name
        self.type = parsed_config.device_type

        self.startTime = time.monotonic()  # Start time for the device, used for uptime tracking
        self.times: list[float] = []
        self.sensors, self.controls = self._initializeFromConfig(parsed_config)
        self.sensor_data: dict[str, list[float]] = {sensor_name: [] for sensor_name in self.sensors}
        self.control_states: dict[str, str] = {
            control_name: control.default.name for control_name, control in self.controls.items()
        }
        self.sensor_names: list[str] = list(self.sensors.keys()) # Cache sensor names to avoid rebuilding list

    def _initializeFromConfig(
        self,
        config: DeviceConfig,
    ) -> tuple[dict[str, SensorConfig], dict[str, ControlConfig]]:
        """Initialize all devices and sensors from the config file."""

        sensors: dict[str, SensorConfig] = {}
        controls: dict[str, ControlConfig] = {}

        for sensor_config in config.sensors_by_id.values():
            sensors[sensor_config.name] = sensor_config

        for control_config in config.controls_by_id.values():
            controls[control_config.name.upper()] = control_config

        return sensors, controls

    def addDataPoints(self, vals: dict[str, float]) -> None:
        """Take a dict of sensor:value pairs and appends them to the corresponding sensor.

        Logs the time of the data point as well.

        """

        for sensorName, sensor in self.sensors.items():
            if sensorName in vals:
                self.sensor_data[sensor.name].append(vals[sensorName])

        self.times.append(time.monotonic() - self.startTime)

    def addDataPoint(self, sensorName: str, value: float) -> None:
        self.sensor_data[sensorName].append(value)

    def setControlState(self, controlName: str, state: str) -> None:
        self.control_states[controlName.upper()] = state

    async def openValve(self, valveName: str) -> None:  # FIXME Open loop for now. Add check against redis log later.
        """Open the valve based on its default state."""
        from libqretprop.DeviceControllers import deviceTools  # noqa: PLC0415

        await deviceTools.setControl(self, valveName, "OPEN")

    async def closeValve(self, valveName: str) -> None:  # FIXME Open loop for now. Add check against redis log later.
        from libqretprop.DeviceControllers import deviceTools  # noqa: PLC0415

        await deviceTools.setControl(self, valveName, "CLOSE")

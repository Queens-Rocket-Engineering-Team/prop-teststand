import socket
import time
from typing import Any

from libqretprop.DeviceControllers import deviceTools
from libqretprop.Devices.Control import Control
from libqretprop.Devices.ESPDevice import ESPDevice
from libqretprop.Devices.sensors.Current import Current
from libqretprop.Devices.sensors.LoadCell import LoadCell
from libqretprop.Devices.sensors.PressureTransducer import PressureTransducer
from libqretprop.Devices.sensors.Resistance import Resistance
from libqretprop.Devices.sensors.Thermocouple import Thermocouple


class SensorMonitor(ESPDevice):
    """Class of device which is an ESP32 that reads sensor data.

    An object will self define itself from a json file structure config file
    received from the device. Deserialization takes place at the parent class
    level, this class works on the JSON object level.

    """

    def __init__(self, socket: socket.socket, address: str, config: dict[str, Any]) -> None:
        super().__init__(socket, address, config)

        # Storing the default information inherited from the parent class
        self.socket = socket
        self.address = address
        self.jsonConfig: dict[str, str] = config

        self.name: str = config.get("device_name")
        self.type = config.get("device_type")

        self.startTime = time.monotonic()  # Start time for the device, used for uptime tracking
        self.times: list[float] = []
        self.sensors, self.controls = self._initializeFromConfig(config)

    # JSON.loads returns a dictionary where attributes are defined with string titles and can contain whatever as values.
    def _initializeFromConfig(
        self, config: dict[str, Any]
    ) -> tuple[dict[str, Thermocouple | LoadCell | PressureTransducer | Current | Resistance], dict[str, Control]]:
        """Initialize all devices and sensors from the config file."""

        sensors: dict[str, Thermocouple | LoadCell | PressureTransducer | Current | Resistance] = {}
        controls: dict[str, Control] = {}

        sensorInfo = config.get("sensor_info", {})

        for name, details in sensorInfo.get("thermocouple", {}).items():
            sensors[name] = Thermocouple(
                name=name,
                sensor_index=details.get("sensor_index"),
                thermoType=details.get("type", "K"),
                unit=details.get("unit", "C"),
            )

        for name, details in sensorInfo.get("pressure_transducer", {}).items():
            sensors[name] = PressureTransducer(
                name=name,
                sensor_index=details.get("sensor_index"),
                resistorOhms=details.get("resistor_ohms", 350),
                maxPressurePSI=details.get("max_pressure_PSI", 500),
                unit=details.get("unit", "PSI"),
            )

        for name, details in sensorInfo.get("load_cell", {}).items():
            sensors[name] = LoadCell(
                name=name,
                sensor_index=details.get("sensor_index"),
                loadRatingN=details.get("load_rating_N", 1000),
                excitationV=details.get("excitation_V", 5.0),
                sensitivityvV=details.get("sensitivity_vV", 2.0),
                unit=details.get("unit", "N"),
            )

        for name, details in sensorInfo.get("current_sensor", {}).items():
            sensors[name] = Current(
                name=name,
                sensor_index=details.get("sensor_index"),
                shuntResistorOhms=details.get("shunt_resistor_ohms", 0.1),
                csaGain=details.get("csa_gain", 50),
                unit=details.get("unit", "A"),
            )

        for name, details in sensorInfo.get("resistance_sensor", {}).items():
            sensors[name] = Resistance(
                name=name,
                sensor_index=details.get("sensor_index"),
                injectedCurrentuA=details.get("injected_current_uA", 1000),
                rShort=details.get("r_short", 50),
                unit=details.get("unit", "ohms"),
            )

        # Register valves
        for name, details in config.get("controls", {}).items():
            control_index = details.get("control_index", None)
            controlType = details.get("type")
            defaultState = details.get("defaultState")

            controls[name.upper()] = Control(
                name=name.upper(),
                controlType=controlType,
                control_index=control_index,
                defaultState=defaultState,
            )

        return sensors, controls

    def addDataPoints(self, vals: dict[str, float]) -> None:
        """Take a dict of sensor:value pairs and appends them to the corresponding sensor.

        Logs the time of the data point as well.

        """

        for sensorName, sensor in self.sensors.items():
            if sensorName in vals:
                sensor.data.append(vals[sensorName])

        self.times.append(time.monotonic() - self.startTime)

    def openValve(self, valveName: str) -> None:  # FIXME Open loop for now. Add check against redis log later.
        """Open the valve based on its default state."""
        deviceTools.setControl(self, valveName, "OPEN")

    def closeValve(self, valveName: str) -> None:  # FIXME Open loop for now. Add check against redis log later.
        deviceTools.setControl(self, valveName, "CLOSE")

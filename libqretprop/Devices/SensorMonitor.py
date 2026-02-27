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

        self.name: str = config.get("deviceName")
        self.type = config.get("deviceType")

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

        sensorInfo = config.get("sensorInfo", {})

        for name, details in sensorInfo.get("thermocouples", {}).items():
            sensors[name] = Thermocouple(
                name=name,
                ADCIndex=details["ADCIndex"],
                highPin=details["highPin"],
                lowPin=details["lowPin"],
                thermoType=details.get("type", "K"),
                units=details.get("units", "C"),
            )

        for name, details in sensorInfo.get("pressureTransducers", {}).items():
            sensors[name] = PressureTransducer(
                name=name,
                ADCIndex=details["ADCIndex"],
                pinNumber=details["pin"],
                maxPressure_PSI=details.get("maxPressure_PSI", 500),
                units=details.get("units", "PSI"),
            )

        for name, details in sensorInfo.get("loadCells", {}).items():
            sensors[name] = LoadCell(
                name=name,
                ADCIndex=details["ADCIndex"],
                highPin=details["highPin"],
                lowPin=details["lowPin"],
                loadRating_N=details.get("loadRating_N", 1000),
                excitation_V=details.get("excitation_V", 5.0),
                sensitivity_vV=details.get("sensitivity_vV", 2.0),
                units=details.get("units", "N"),
            )

        for name, details in sensorInfo.get("current", {}).items():
            sensors[name] = Current(
                name=name,
                ADCIndex=details["ADCIndex"],
                pinNumber=details["pin"],
                shuntResistor_Ohms=details.get("shuntResistor_Ohms", 0.1),
                csaGain=details.get("csaGain", 50),
                units=details.get("units", "A"),
            )

        for name, details in sensorInfo.get("resistance", {}).items():
            sensors[name] = Resistance(
                name=name,
                ADCIndex=details["ADCIndex"],
                pinNumber=details["pin"],
                injectedCurrent=details["injectedCurrent"],
                units=details.get("units", "Ohms"),
            )

        # Register valves
        for name, details in config.get("controls", {}).items():
            pin = details.get("pin", None)
            controlType = details.get("type")
            defaultState = details.get("defaultState")

            controls[name.upper()] = Control(
                name=name.upper(),
                controlType=controlType,
                pin=pin,
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

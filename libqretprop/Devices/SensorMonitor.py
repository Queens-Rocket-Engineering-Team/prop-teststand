import socket
import time
from typing import Any

from libqretprop.DeviceControllers import deviceTools
from libqretprop.Devices.ESPDevice import ESPDevice
from libqretprop.Devices.sensors.LoadCell import LoadCell
from libqretprop.Devices.sensors.PressureTransducer import PressureTransducer
from libqretprop.Devices.sensors.Thermocouple import Thermocouple
from libqretprop.Devices.Valve import Valve


class SensorMonitor(ESPDevice):
    """Class of device which is an ESP32 that reads sensor data.

    An object will self define itself from a json file structure config file
    received from the device. Deserialization takes place at the parent class
    level, this class works on the JSON object level.

    """

    def __init__(self,
                 socket: socket.socket,
                 address: str,
                 config: dict[str, Any]) -> None:
        super().__init__(socket, address, config)

        # Storing the default information inherited from the parent class
        self.socket = socket
        self.address = address
        self.jsonConfig = config

        self.name = config.get("deviceName")
        self.type = config.get("deviceType")

        self.times : list[float] = []
        self.sensors, self.valves = self._initializeFromConfig(config)

    # JSON.loads returns a dictionary where attributes are defined with string titles and can contain whatever as values.
    def _initializeFromConfig(self, config: dict[str, Any]) -> tuple[list[Thermocouple | LoadCell | PressureTransducer],
                                                                     dict[str, Valve]]:
        """Initialize all devices and sensors from the config file."""

        sensors: list[Thermocouple | LoadCell | PressureTransducer] = []
        valves: dict[str, Valve] = {}

        sensorInfo = config.get("sensorInfo", {})

        for name, details in sensorInfo.get("thermocouples", {}).items():
            sensors.append(Thermocouple(name=name,
                                        ADCIndex=details["ADCIndex"],
                                        highPin=details["highPin"],
                                        lowPin=details["lowPin"],
                                        thermoType=details["type"],
                                        units=details["units"],
                                        ))

        for name, details in sensorInfo.get("pressureTransducers", {}).items():
            sensors.append(PressureTransducer(name=name,
                                            ADCIndex=details["ADCIndex"],
                                            pinNumber=details["pin"],
                                            maxPressure_PSI=details["maxPressure_PSI"],
                                            units=details["units"],
                                            ))

        for name, details in sensorInfo.get("loadCells", {}).items():
            sensors.append(LoadCell(name=name,
                                    ADCIndex=details["ADCIndex"],
                                    highPin=details["highPin"],
                                    lowPin=details["lowPin"],
                                    loadRating_N=details["loadRating_N"],
                                    excitation_V=details["excitation_V"],
                                    sensitivity_vV=details["sensitivity_vV"],
                                    units=details["units"],
                                    ))

        # Register valves
        for name, details in config.get("valves", {}).items():
                pin = details.get("pin", None)
                defaultState = details.get("defaultState")

                valves[name.upper()] = (Valve(name=name.upper(),
                                              pin=pin,
                                              defaultState=defaultState,
                                              ))

        return sensors, valves

    def addDataPoints(self, vals: list[float]) -> None:
        """Take an array of values in order of definition and appends them to the corresponding sensor.

        Logs the time of the data point as well.

        """

        for i, sensor in enumerate(self.sensors):
            sensor.data.append(vals[i])

        self.times.append(time.monotonic())

    def openValve(self, valveName: str) -> None: # FIXME Open loop for now. Add check against redis log later.
        """Open the valve based on its default state."""
        deviceTools.setValve(self, [valveName, "OPEN"])

    def closeValve(self, valveName: str) -> None: # FIXME Open loop for now. Add check against redis log later.
        deviceTools.setValve(self, [valveName, "CLOSE"])

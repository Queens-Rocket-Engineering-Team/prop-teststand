from libqretprop.esp32interface.ESPDevice.ESPDevice import ESPDevice
from libqretprop.esp32interface.sensors.LoadCell import LoadCell
from libqretprop.esp32interface.sensors.PressureTransducer import PressureTransducer
from libqretprop.esp32interface.sensors.Thermocouple import Thermocouple


class SensorMonitor(ESPDevice):
    """Class of device which is an ESP32 that reads sensor data.

    An object will self define itself from the byte form of a file received from the device.

    """

    def __init__(self, config: dict, address: str) -> None:
        super().__init__(config, address)

        self.config = config
        self.sensors = self.initializeFromConfig(config)

    def initializeFromConfig(self, config: dict) -> list:
        """Initialize all devices and sensors from the config file."""

        sensors: list[Thermocouple | LoadCell | PressureTransducer] = []

        print(f"Initializing device: {config.get('deviceName', 'Unknown Device')}")

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

        return sensors

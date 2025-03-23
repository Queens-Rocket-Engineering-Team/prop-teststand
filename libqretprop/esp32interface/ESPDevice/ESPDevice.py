import json
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from libqretprop.esp32interface.SensorMonitor.SensorMonitor import SensorMonitor

class ESPDevice:
    def __init__(self,
                 jsonConfig: dict,
                 address: str,
                 ) -> None:

        self.jsonConfig = jsonConfig

        self.name = jsonConfig["deviceName"]
        self.type = jsonConfig["deviceType"]
        self.address = address


    @staticmethod
    def fromConfigBytes(configBytes: bytes, address: str) -> "SensorMonitor": # Delay evaluation of SensorMonitor to avoid circular imports
        configJson = json.loads(configBytes.decode("utf-8"))
        deviceType = configJson["deviceType"]

        if deviceType in {"Sensor Monitor", "Simulated Sensor Monitor"}:
            # To avoid circular imports, import the SensorMonitor class only within the scope of this function
            from libqretprop.esp32interface.SensorMonitor.SensorMonitor import SensorMonitor

            return SensorMonitor(configJson, address)
        else:
            err = f"Device type {deviceType} not recognized."
            raise ValueError(err)

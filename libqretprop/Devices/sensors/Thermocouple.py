class Thermocouple:
    """Class for storing thermocouple data from an ESP32.

    Method to read data from sensor is not implemented yet

    """

    def __init__ (self,
                  name: str,
                  sensor_index: str,
                  thermoType: str,
                  unit: str,
                  ):

        self.name = name
        self.sensor_index = sensor_index
        self.type = thermoType
        self.unit = unit

        self.data: list[float] = []

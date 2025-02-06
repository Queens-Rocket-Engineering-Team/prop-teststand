class Thermocouple:
    """Class for storing thermocouple data from an ESP32.

    Method to read data from sensor is not implemented yet

    """

    def __init__ (self,
                  name: str,
                  ADCIndex: int,
                  highPin: int,
                  lowPin: int,
                  thermoType: str,
                  units: str,
                  ):

        self.name = name
        self.ADCIndex = ADCIndex
        self.highPin = highPin
        self.lowPin = lowPin
        self.type = thermoType
        self.units = units

        self.data = []

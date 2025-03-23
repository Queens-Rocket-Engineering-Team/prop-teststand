class Thermocouple:
    """Class for reading thermocouple data from an ADC.

    UNFINISHED. Don't use this yet. Need to see how the circuitry works out

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
        self.highPin = highPin #ADC(Pin(highPin, Pin.IN))
        self.lowPin = lowPin #ADC(Pin(lowPin, Pin.IN))
        self.type = thermoType
        self.units = units

    def takeData (self) -> float: # Currently returns differential voltage reading
        diffReading = -1 # Not Implemented -- self.highPin.read() - self.lowPin.read()
        return diffReading

# noqa: INP001 -- Implicit namespace doesn't matter here
from machine import ADC, Pin  # type: ignore # These are micropython libraries


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
        self.highPin = ADC(Pin(highPin, Pin.IN))
        self.lowPin = ADC(Pin(lowPin, Pin.IN))
        self.type = thermoType
        self.units = units

    def takeData (self) -> float: # Currently returns differential voltage reading
        diffReading = self.highPin.read() - self.lowPin.read()
        return diffReading

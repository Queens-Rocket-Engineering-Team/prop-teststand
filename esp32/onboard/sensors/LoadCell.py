# noqa: INP001 -- Implicit namespace doesn't matter here
from machine import ADC, Pin  # type: ignore # These are micropython libraries


class LoadCell:

    def __init__ (self,
                  name: str,
                  ADCIndex: int,
                  highPin: int,
                  lowPin: int,
                  loadRating_N: float,
                  excitation_V: float,
                  sensitivity_vV: float,
                  units: str,
                  ):

        self.name = name
        self.ADCIndex = ADCIndex
        self.highPin = ADC(Pin(highPin, Pin.IN))
        self.lowPin = ADC(Pin(lowPin, Pin.IN))
        self.maxWeight = loadRating_N
        self.units = units

        self.fullScaleVoltage = excitation_V * (sensitivity_vV/1000) # input sensitivity in units of mv/V in the config file


    def takeData (self) -> float | int: # If no units are specified, return voltage reading
        vReading: int = self.highPin.read() - self.lowPin.read() # Differential voltage reading
        if self.units == "kg":
            return (vReading/self.fullScaleVoltage)*(self.maxWeight/9.805)
        if self.units == "N":
            return (vReading/self.fullScaleVoltage)*(self.maxWeight)

        return vReading

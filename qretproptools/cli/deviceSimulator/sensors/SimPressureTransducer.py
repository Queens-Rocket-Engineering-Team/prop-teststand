class PressureTransducer:

    def __init__ (self,
                  name: str,
                  ADCIndex: int,
                  pinNumber: int,
                  maxPressure_PSI: int,
                  units: str,
                  ):

        self.name = name
        self.ADCIndex = ADCIndex
        if self.ADCIndex == 0:
            self.pin = pinNumber # Not a real device! ADC(Pin(pinNumber, Pin.IN))
        self.maxPressure_PSI = maxPressure_PSI
        self.units = units


    def takeData (self) -> float | int: # If no units are specified, return voltage reading
        vReading: int = -1 # Not Implemented! self.pin.read() # Sensor voltage reading
        if self.units == "psi":
            return ((vReading-1)/4)*(self.maxPressure_PSI) # output is 4-20mA across a 250R resistor so we have a 4V range (1-5V).
                                                           # Subtracting 1 because 1 is the minimum voltage output and we need to set the floor

        return vReading

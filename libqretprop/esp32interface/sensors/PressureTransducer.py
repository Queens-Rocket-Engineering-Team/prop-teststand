class PressureTransducer:
    """Store information and data from a pressure transducer.

    Method to read data from sensor is not implemented yet

    """

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
            self.pin = pinNumber
        self.maxPressure_PSI = maxPressure_PSI
        self.units = units

        self.data = []
        
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
        self.highPin = highPin
        self.lowPin = lowPin
        self.maxWeight = loadRating_N
        self.units = units

        self.fullScaleVoltage = excitation_V * (sensitivity_vV/1000) # input sensitivity in units of mv/V in the config file

        self.data = []

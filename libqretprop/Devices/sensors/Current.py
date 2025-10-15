class Current:
    """Store information and data from a current sensor (CSA + ADC)."""

    def __init__(self,
                 name: str,
                 ADCIndex: int,
                 pinNumber: int,
                 shuntResistor_Ohms: float,
                 csaGain: int,
                 units: str = "A",
                 ):

        self.name = name
        self.ADCIndex = ADCIndex
        self.pin = pinNumber
        self.shuntResistor_Ohms = shuntResistor_Ohms
        self.csaGain = csaGain
        self.units = units

        self.data: list[float] = []

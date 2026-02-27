class Resistance:
    """Store information and data from a resistance sensor (IDAC + ADC)."""

    def __init__(
        self,
        name: str,
        ADCIndex: int,
        pinNumber: int,
        injectedCurrent: int,
        units: str = "Ohms",
    ):

        self.name = name
        self.ADCIndex = ADCIndex
        self.pin = pinNumber
        self.injectedCurrent = injectedCurrent
        self.units = units

        self.data: list[float] = []

class Resistance:
    """Store information and data from a resistance sensor (IDAC + ADC)."""

    def __init__(
        self,
        name: str,
        sensor_index: str,
        injectedCurrentuA: int,
        rShort: float,
        unit: str = "ohms",
    ):

        self.name = name
        self.sensor_index = sensor_index
        self.injectedCurrentuA = injectedCurrentuA
        self.rShort = rShort
        self.unit = unit

        self.data: list[float] = []

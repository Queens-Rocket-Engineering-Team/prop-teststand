class Current:
    """Store information and data from a current sensor (CSA + ADC)."""

    def __init__(self,
                 name: str,
                 sensor_index: str,
                 shuntResistorOhms: float,
                 csaGain: int,
                 unit: str = "A",
                 ):

        self.name = name
        self.sensor_index = sensor_index
        self.shuntResistorOhms = shuntResistorOhms
        self.csaGain = csaGain
        self.unit = unit

        self.data: list[float] = []

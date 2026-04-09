class PressureTransducer:
    """Store information and data from a pressure transducer.

    Method to read data from sensor is not implemented yet

    """

    def __init__ (self,
                  name: str,
                  sensor_index: str,
                  resistorOhms: float,
                  maxPressurePSI: int,
                  unit: str,
                  ):

        self.name = name
        self.sensor_index = sensor_index
        self.resistorOhms = resistorOhms
        self.maxPressurePSI = maxPressurePSI
        self.unit = unit

        self.data : list[float] = []

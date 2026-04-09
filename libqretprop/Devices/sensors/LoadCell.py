class LoadCell:

    def __init__ (self,
                  name: str,
                  sensor_index: str,
                  loadRatingN: float,
                  excitationV: float,
                  sensitivityvV: float,
                  unit: str,
                  ):

        self.name = name
        self.sensor_index = sensor_index
        self.maxWeight = loadRatingN
        self.unit = unit

        self.fullScaleVoltage = excitationV * (sensitivityvV/1000) # input sensitivity in units of mv/V in the config file

        self.data : list[float] = []

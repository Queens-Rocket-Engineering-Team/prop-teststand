from labjack import ljm  #type:ignore  # Labjack is not typed


class PressureTransducer:

    data_V: list[float]
    data_PSI: list[float]

    def __init__ (self, handle: int, pin: str, pressureRange: float):
        self.handle = handle
        self.address = ljm.nameToAddress(pin)
        self.pinName = pin
        self.pressureMax = pressureRange

        # Creating data storage array
        self.data_V = []
        self.data_PSI = []

    def takeData (self) -> None:
        vReading = ljm.eReadName(self.handle, self.pinName)
        pReading = ((vReading-1)/4)*(self.pressureMax) # PSI conversion based on linear voltage output -- multiply by pressure range
        self.data_V.append(vReading)
        self.data_PSI.append(pReading)

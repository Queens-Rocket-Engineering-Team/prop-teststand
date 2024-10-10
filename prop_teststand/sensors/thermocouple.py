from labjack import ljm  #type:ignore  # Labjack is not typed


class Thermocouple:
    """Class to define a thermocouple type sensor.

    The class definition should be passed a numbered analog pin (i.e. AIN0) and it will
    automatically define the relative measurement to be made to the adjacent ground pin. The Labjack
    has built in cold junction compensation and will also output a flat temperature value without
    any additional or interpretation required on the coding end. The takeData method takes advantage
    of this feature. The red wire is the negative wire and should be connected to the ground
    terminal, the yellow wire (color coded for thermocouple type. Yellow is type K) is connected to
    the specified terminal. (MAY ONLY WORK ON EVEN TERMINALS)
    """

    data_C: list[float]

    def __init__ (self, handle: int, pin: str, offset: float):
        self.handle = handle
        self.pin = pin
        self.offset = offset
        self.address = ljm.nameToAddress(pin)

        # Creating data storage array
        self.data_C = []

        # Register Setup
        self.equationRegister = pin + "_EF_INDEX"
        self.unitRegister = pin + "_EF_CONFIG_A"
        self.tempOutputRegister = pin + "_EF_READ_A"  # Measured TC temperature (CJC temp -- EF_READ_C, "calculated" temp -- EF_READ_A)

        ljm.eWriteName(handle, self.equationRegister, 22) # Set the equation to apply to the pin to be the one to handle K-type thermocouples
        ljm.eWriteName(handle, self.unitRegister, 1) # To set the temperature units. 0 = K, 1 = C, 2 = F.
        ljm.eWriteName(handle, pin+"_EF_CONFIG_B", 60052)  # Set CJC Modbus addr to default
        ljm.eWriteName(handle, pin+"_EF_CONFIG_D", 1.0)  # Slope in K/V
        ljm.eWriteName(handle, pin+"_EF_CONFIG_E", -3.5)  # Sensor offset in K  (-3.5 degrees seems right based on vibes)

    def takeData (self) -> None:
        ljm.eReadName(self.handle, self.pin + "_EF_READ_A")  # only reading reg A triggers a new measurement..
        self.data_C.append(ljm.eReadName(self.handle, self.tempOutputRegister) + self.offset)

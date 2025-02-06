from labjack import ljm  #type:ignore  # Labjack is not typed


class LoadCell:
    """Class definition for a Load Cell sensor type. Defined within the context of a LabJack T7.

    This load cell definition assumes a 10V excitation and a 2mV/V sensitivity. This means the
    maximum possible output from the cell is 20mV. All response is linear so to make a measurement
    we take the fraction of 20mV that is being read, and multiple it by the max weight capability of
    the cell. The green (+ve) and white (-ve) wires on the load cell should be connected to even and
    odd adjacent analog pins. THIS IS VERY IMPORTANT.
    """

    data_V: list[float]
    data_kg: list[float]

    def __init__ (self,
                  handle: int,
                  highPin: str,
                  lowPin: str,
                  maxWeight: float,
                  excitation_V: float,
                  sensitivity_vV: float):

        self.handle = handle
        self.highName = highPin
        self.lowName = lowPin
        self.maxWeight = maxWeight
        self.fullScaleVoltage = excitation_V * (sensitivity_vV/1000) # input sensitivity in units of mv/V in the config file

        # Creating string to be able to write to the register that defines the relative pin for the high pin
        self.negChannelRegister = self.highName + "_NEGATIVE_CH"
        print(self.negChannelRegister)

        # Assembling names for registers to control gain on the input pins
        self.highChannelRangeRegister = self.highName + "_RANGE"
        self.lowChannelRangeRegister = self.lowName + "_RANGE"
        print(self.highChannelRangeRegister, self.lowChannelRangeRegister)

        ljm.eWriteName(self.handle, self.highChannelRangeRegister, 0.1)
        ljm.eWriteName(self.handle, self.lowChannelRangeRegister, 0.1)

        # Setting up negative channel
        lowPinInt = int("".join(filter(str.isdigit, lowPin))) # Parsing negative pin input to get the integer value of the pin
        print(lowPinInt)
        ljm.eWriteName(self.handle, self.negChannelRegister, lowPinInt) # Writing integer value of relative pin to neg channel register

        # Creating data storage array
        self.data_V  = []
        self.data_kg = []

    def takeData (self) -> None:
            vReading = ljm.eReadName(self.handle, self.highName)
            kgReading = (vReading/self.fullScaleVoltage)*(self.maxWeight/9.805) # Local gravity in kingston according to wolfram alpha

            self.data_V.append(vReading)
            self.data_kg.append(kgReading)

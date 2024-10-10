from labjack import ljm  #type:ignore


class Valve:
    """Class definition for a valve type object.

    VERY IMPORTANT THAT WHEN DEALING WITH VALVES: Open means gas is allowed to flow. Closed means no gas is allowed to flow.
    Note that this is the opposite of electric circuits.
    """

    def __init__ (self, handle: int, controlPin: str, defaultState: int, valveName: str):
        self.handle = handle
        self.valveName = valveName
        self.pinName = controlPin
        self.pinAddress = ljm.nameToAddress(controlPin)
        self.defaultState = defaultState # 0 for default pneumatically closed (GAS NOT FLOWING), 1 for default pneumatically open (GAS FLOWING)

        ljm.eWriteName(self.handle, self.pinName, 0)
        self.currentState = self.defaultState

    def openValve(self) -> None:
        if self.currentState == 1: print(f"{self.valveName} is already open!")
        else:
            if self.defaultState == 0: # Default Closed
                ljm.eWriteName(self.handle, self.pinName, 1) # Apply power to open valve
            if self.defaultState == 1: #Default Open
                ljm.eWriteName(self.handle, self.pinName, 0) # Remove power to open valve
            print(f"Opening {self.valveName}")
            self.currentState = 1

    def closeValve(self) -> None:
        if self.currentState == 0: print(f"{self.valveName} is already closed!")
        else:
            if self.defaultState == 0: # Default Closed
                ljm.eWriteName(self.handle, self.pinName, 0) # Remove power to close valve
            if self.defaultState == 1: # Default Open
                ljm.eWriteName(self.handle, self.pinName, 1) # Apply power to close valve
            print(f"Closing {self.valveName}")
            self.currentState = 0


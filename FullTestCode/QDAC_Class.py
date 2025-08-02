#.==================================.#
#| QRET Data Aquisition and Control |#
#| Class Dctionary                  |#
# \================================/ #
from labjack import ljm
import numpy as np
import time

class thermocouple:
    '''
    Class to define a thermocouple type sensor. The class definition should be passed a numbered analog pin (i.e. AIN0),
    bias voltage, gain, and Seebeck coefficient. Temperature is calculated as: (volt - bias) / (gain * seebeck_coeff)
    where volt is the raw voltage reading from the pin.
    '''
 
    def __init__(self, handle, pin, bias, gain, seebeck_coeff):
        self.handle = handle        
        self.pin = pin
        self.bias = bias
        self.gain = gain
        self.seebeck_coeff = seebeck_coeff
        self.address = ljm.nameToAddress(pin)

        # Creating data storage arrays
        self.data_voltage = []
        self.data_celsius = []

    def takeData(self):
        volt = ljm.eReadName(self.handle, self.pin)
        temp_celsius = (volt - self.bias) / (self.gain * self.seebeck_coeff)
        
        self.data_voltage.append(volt)
        self.data_celsius.append(temp_celsius)
        
        return temp_celsius

class pressureTransducer:

    def __init__ (self, handle, pin, pressureRange):
        self.handle = handle
        self.address = ljm.nameToAddress(pin)
        self.pinName = pin
        self.pressureMax = pressureRange

        # Creating data storage array
        self.data_V = []
        self.data_PSI = []

    def takeData (self):
        vReading = ljm.eReadName(self.handle, self.pinName)
        pReading = ((vReading-1)/4)*(self.pressureMax) # PSI conversion based on linear voltage output -- multiply by pressure range
        self.data_V.append(vReading)
        self.data_PSI.append(pReading)
    


class loadCell: 
    '''
    This load cell definition assumes a 10V excitation and a 2mV/V sensitivity. This means the maximum possible output from
    the cell is 20mV. All response is linear so to make a measurement we take the fraction of 20mV that is being read, and
    multiple it by the max weight capability of the cell. The green (+ve) and white (-ve) wires on the load cell should be 
    connected to even and odd adjacent analog pins. THIS IS VERY IMPORTANT.
    '''
    
    def __init__ (self, handle, highPin, lowPin, maxWeight, excitation_V, sensitivity_vV):
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
        lowPinInt = int(''.join(filter(str.isdigit, lowPin))) # Parsing negative pin input to get the integer value of the pin
        print(lowPinInt)
        ljm.eWriteName(self.handle, self.negChannelRegister, lowPinInt) # Writing integer value of relative pin to neg channel register

        # Creating data storage array
        self.data_V = []
        self.data_kg = []

    def takeData (self):
            vReading = ljm.eReadName(self.handle, self.highName)
            kgReading = (vReading/self.fullScaleVoltage)*(self.maxWeight/9.805) # Local gravity in kingston according to wolfram alpha
            
            self.data_V.append(vReading)
            self.data_kg.append(kgReading)

class valve:
    '''
    VERY IMPORTANT THAT WHEN DEALING WITH VALVES: Open means gas is allowed to flow. Closed means no gas is allowed to flow.
    This is the opposite of electric circuits.
    '''

    def __init__ (self, handle, controlPin, defaultState, valveName):
        self.handle = handle
        self.valveName = valveName
        self.pinName = controlPin
        self.pinAddress = ljm.nameToAddress(controlPin)
        self.defaultState = defaultState # 0 for default pneumatically closed (GAS NOT FLOWING), 1 for default pneumatically open (GAS FLOWING)

        ljm.eWriteName(self.handle, self.pinName, 0)
        self.currentState = self.defaultState
    
    def openValve(self):
        if self.currentState == 1: print(f"{self.valveName} is already open!")
        else:
            if self.defaultState == 0: # Default Closed
                ljm.eWriteName(self.handle, self.pinName, 1) # Apply power to open valve
            if self.defaultState == 1: #Default Open
                ljm.eWriteName(self.handle, self.pinName, 0) # Remove power to open valve
            print(f"Opening {self.valveName}")
            self.currentState = 1

    def closeValve(self):
        if self.currentState == 0: print(f"{self.valveName} is already closed!")
        else:
            if self.defaultState == 0: # Default Closed
                ljm.eWriteName(self.handle, self.pinName, 0) # Remove power to close valve
            if self.defaultState == 1: # Default Open
                ljm.eWriteName(self.handle, self.pinName, 1) # Apply power to close valve
            print(f"Closing {self.valveName}")
            self.currentState = 0


# BASE MICROPYTHON BOOT.PY-----------------------------------------------|  # noqa: INP001
# # This is all micropython code to be executed on the esp32 system level and doesn't require a __init__.py file

# This file is executed on every boot (including wake-boot from deep sleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()
#------------------------------------------------------------------------|


import ujson  # type:ignore # noqa: I001# ujson and machine are micropython libraries

import wifi_tools as wt
from AsyncManager import AsyncManager
from TCPHandler import TCPHandler
from UDPListener import UDPListener
from machine import Pin  # type: ignore # machine is a micropython library
from machine import I2C  # type: ignore # machine is a micropython library

from sensors.Thermocouple import Thermocouple # type: ignore # don't need __init__ for micropython
from sensors.PressureTransducer import PressureTransducer # type: ignore
from sensors.LoadCell import LoadCell # type: ignore

CONFIG_FILE = "ESPConfig.json"

def readConfig(filePath: str):  # type: ignore  # noqa: ANN201
    try:
        with open(filePath, "r") as file:
            config = ujson.load(file)
            return config
    except Exception as e:
        print(f"Failed to read config file: {e}")
        return {}

def initializeFromConfig(config) -> list[Thermocouple | LoadCell | PressureTransducer]: # type: ignore  # noqa: ANN001 # Typing for the JSON object is impossible without the full Typing library
    """Initialize all devices and sensors from the config file.

    ADC index 0 indicates the sensor is connected directly to the ESP32. Any other index indicates
    connection to an external ADC.
    """
    sensors: list[Thermocouple | LoadCell | PressureTransducer] = []

    print(f"Initializing device: {config.get('deviceName', 'Unknown Device')}")
    deviceType = config.get("deviceType", "Unknown")

    if deviceType == "Sensor Monitor": # Sensor monitor is what I'm calling an ESP32 that reads sensors
        sensorInfo = config.get("sensorInfo", {})

        for name, details in sensorInfo.get("thermocouples", {}).items():
            sensors.append(Thermocouple(name=name,
                                        ADCIndex=details["ADCIndex"],
                                        highPin=details["highPin"],
                                        lowPin=details["lowPin"],
                                        thermoType=details["type"],
                                        units=details["units"],
                                        ))

        for name, details in sensorInfo.get("pressureTransducers", {}).items():
            sensors.append(PressureTransducer(name=name,
                                              ADCIndex=details["ADCIndex"],
                                              pinNumber=details["pin"],
                                              maxPressure_PSI=details["maxPressure_PSI"],
                                              units=details["units"],
                                              ))

        for name, details in sensorInfo.get("loadCells", {}).items():
            sensors.append(LoadCell(name=name,
                                    ADCIndex=details["ADCIndex"],
                                    highPin=details["highPin"],
                                    lowPin=details["lowPin"],
                                    loadRating_N=details["loadRating_N"],
                                    excitation_V=details["excitation_V"],
                                    sensitivity_vV=details["sensitivity_vV"],
                                    units=details["units"],
                                    ))

        return sensors

    if deviceType == "Unknown":
        raise ValueError("Device type not specified in config file")

    return []

def readRegister(i2cBus: I2C, address: int, register: int) -> bytes:
    """Read a 8-bit register from the ADS112C04.

    There are two parts to this call. The first part is the address of the device to read from, and the second part is the register to read from.
    The address section doesn't natively work with the I
    Address format:


    RREG format is as follows:
    [7:4] Base RREG command (0b0010)
    [3:2] Register number (0b00 for MUX_GAIN_PGA, 0b01 for DR_MODE_CM_VREF_TS, 0b10 for DRDY_DCNT_CRC_BCS_IDAC, 0b11 for IDAC1_IDAC2)
    [1:0] Reserved bits (should be 0)

    EXAMPLE for calling register 2:
    RREG = 0b0010
    Reg# = 0b10
    cmd = 0b0010 << 4 | 0b0010 << 2
    cmd = 0b00100000 | 0b1000
    cmd = 0b00101000 - Final command to request register 2s bits
    """


    rregCommand = 0b0010 # Read register command as defined in datasheet.

    # Shift the command to the left by 4 bits to put it in the first 4 bits of the write command
    # Shift the register number to the left by 2 bits to put it in the register number bits for the rreg call
    fullCmd = rregCommand << 4 | register << 2 # combine the command and register number with bw OR operator

    # Now write the command to the specified device address to query the register contents
    i2cBus.writeto(address, bytes([fullCmd]), stop=False)
    data = i2cBus.readfrom(address, 1)

    # The ADS1112 will respond with the contents of the 8 bit register so we read 1 byte.

    # The data is returned as a byte object, so we need to convert it to an integer. Use big scheme because MSB is first transmitted.
    return data

UDPRequests = ("SEARCH", # Message received when server is searching for client sensors
               )

TCPRequests = ("SREAD", # Reads a single value from all sensors
               "CREAD", # Continuously reads data from all sensors until STOP received
               "STOP", # Stops continuous reading
               "STAT", # Returns number of sensors and types
               )

wlan = wt.connectWifi("propnet", "propteambestteam")

config = readConfig(CONFIG_FILE)
sensors = initializeFromConfig(config)

udpListener = UDPListener(port=40000)
tcpListener = TCPHandler(port=50000)
server = AsyncManager(udpListener, tcpListener, config)

## I2C Setup
# The Pins NEED to be set to OUT. For some reason the I2C bus doesn't automatically set this on initialization of the bus.
sclPin = Pin(6, Pin.OUT) # SCL pin is GPIO 6 on the ESP32. This connects to pin 16 on the ADC
sdaPin = Pin(7, Pin.OUT) # SDA pin is GPIO 7 on the ESP32. This connects to pin 15 on the ADC

# I2C bus 1, SCL pin 6, SDA pin 7, frequency 100kHz
i2cBus = I2C(1, scl=sclPin, sda=sdaPin, freq=100000)

devices = i2cBus.scan() # Scan the I2C bus for devices. This will return a list of addresses of devices on the bus.
print("I2C devices found at following addresses:", [hex(device) for device in devices]) # Print the addresses of the devices found on the bus


# Current state is that you must enter mpremote and run the main() function to start the server.
def main() -> None:
    server.run()

import csv
import json
import msvcrt
import sys
import time
from typing import cast

import colorama
from labjack import ljm  #type:ignore  # Labjack is not typed

from libqretprop.LJM_sensors.loadCell import LoadCell
from libqretprop.LJM_sensors.pressureTransducer import PressureTransducer
from libqretprop.LJM_sensors.thermocouple import Thermocouple
from libqretprop.LJM_valves.valve import Valve


startTime_s = time.monotonic()

openValveMap = {
    "1": "AVFill",  # FIO0
    "2": "AVDump",  # FIO1
    "3": "AVRun",  # FIO2
    "4": "AVN2Purge1",  # FIO3
    "5": "AVN2Purge2",  # FIO4
}

closeValveMap = {
    "q": "AVFill",  # FIO0
    "w": "AVDump",  # FIO1
    "e": "AVRun",  # FIO2
    "r": "AVN2Purge1",  # FIO3
    "t": "AVN2Purge2",  # FIO4
}

getStateMap = {
    "a": "AVFill",  # FIO0
    "s": "AVDump",  # FIO1
    "d": "AVRun",  # FIO2
    "f": "AVN2Purge1",  # FIO3
    "g": "AVN2Purge2",  # FIO4
}

#######
## FUNCTIONS
####

def print(*_args: str, **_kwargs: str) -> None:  # noqa: A001 # builtin-variable-shadowing
    raise RuntimeError("STOP! DO NOT USE PRINT! USE log() INSTEAD!")

def log(msg: str) -> None:
    elapsed_s = time.monotonic() - startTime_s
    sys.stdout.write(f"[{elapsed_s:<6.3f}] {msg}\n")

def jsonDefineIO(handle: int, configFilename: str) -> tuple[dict[str, Thermocouple | PressureTransducer | LoadCell],
                                                            dict[str, Valve],
                                                            str,
                                                            str]:
    """Create objects for the desired test setup from a JSON file.

    The JSON file should follow the format specified in /configFramework.json.
    """

    with open(configFilename, "r") as f:
        config = json.load(f)

    sensorsObjects: dict[str, Thermocouple | PressureTransducer | LoadCell] = {}
    valveObjects: dict[str, Valve] = {}

    for sensorType, sensors in config["sensors"].items():
        for sensorName, sensorInfo in sensors.items():
            if sensorType == "thermocouple":
                pin = sensorInfo["pin"]
                offset = sensorInfo["offset"]
                sensorsObjects[sensorName] = Thermocouple(handle, pin, offset)

            elif sensorType == "pressureTransducer":
                pin = sensorInfo["pin"]
                pressureRange = sensorInfo["maxPressure_PSI"]
                sensorsObjects[sensorName] = PressureTransducer(handle, pin, pressureRange)

            elif sensorType == "loadCell":
                negPin = sensorInfo["oddNegPin"]
                posPin = sensorInfo["evenPosPin"]
                maxWeight = sensorInfo["loadRating_N"]
                excitation = sensorInfo["excitation_V"]
                sensitivity = sensorInfo["sensitivity_vV"]
                sensorsObjects[sensorName] = LoadCell(handle, posPin, negPin, maxWeight, excitation, sensitivity)

    for valveName, valveInfo in config["valves"].items():
        pin = valveInfo["controlPin"]
        default = valveInfo["defaultState"]
        valveObjects[valveName] = Valve(handle, pin, default, valveName)

    return sensorsObjects, valveObjects, config["configName"], config["filePath"]

def takeAllData(sensors: dict[str, Thermocouple | PressureTransducer | LoadCell]) -> None:
    """Take data for all sensors."""
    for sensor in sensors.values(): sensor.takeData()

def exportTestDataCSV(timeStamps: list[float],
                      sensors: dict[str, Thermocouple | PressureTransducer | LoadCell],
                      dataDir: str,
                      configName: str,
                      configPath: str) -> None:

    # Setting CSV filename
    localTime = time.strftime("%Y-%m-%d %H-%M-%S", time.localtime(time.time()))
    csvFilename = configName + "---" + localTime

    # Setting CSV headers
    csvData: list[list[str] | list[float]] # Header row is strings, data rows are floats
    csvData = [["Time", *list(sensors.keys())]]

    # Assembling CSV rows for each sensor
    for i in range(len(timeStamps)):
        row = [timeStamps[i]]
        for sensor in sensors.values():
            if type(sensor) == Thermocouple:
                row.append(sensor.data_C[i])
            if type(sensor) == PressureTransducer:
                row.append(sensor.data_PSI[i])
            if type(sensor) == LoadCell:
                row.append(sensor.data_kg[i])
        csvData.append(row)

    # Writing data to csv
    with open(dataDir + csvFilename, "w", newline="") as csvFile:
        writer = csv.writer(csvFile)
        writer.writerow(["Config File Name:", configName])
        writer.writerow(["Config File Path:", configPath])
        writer.writerow(["Test Time:", localTime])
        writer.writerows(csvData)

    log(f"Data saved to {dataDir + csvFilename}")

def errExit(_msg: str, exitCode: int = 1) -> None:
    sys.stderr.write(f"{colorama.Fore.RED}Labjack libraries are not installed! Install Kipling first!{colorama.Style.RESET_ALL}")
    sys.exit(exitCode)

def ensureLabjackPresence() -> None:
    if ljm.ljm._staticLib is None:  # noqa: SLF001
        errExit("Labjack libraries are not installed! Install Kipling first!")

def main (_argv: list[str]) -> None:
    ensureLabjackPresence()

    handle = ljm.openS("T7","ANY","ANY")
    configPath = r"C:\\Users\\Nikhil\\OneDrive\\5th year\\QRET\\DAQcontrol\\firstControlTest.json"
    ljm.eWriteName(handle, "FIO_DIRECTION", 1) # Set the pin as an output


    # Initializing sensors and getting key information from config
    log("Initializing Sensors from config file...")
    sensors, valves, configName, dataDirectory = jsonDefineIO(handle, configPath)
    log("Sensors Initialized.")

    sampleSpacing_s = 0.01
    times = []
    count = 0

    print("Enter control keys now:")
    lastTime = time.monotonic()
    while(True):
        currentTime = time.monotonic()


        if (currentTime - lastTime) > sampleSpacing_s:
            takeAllData(sensors)
            times.append(currentTime - startTime_s)
            count += 1
            if count % 10 == 0:
                log(
                    "NitrousFillKG: " + f"{cast(LoadCell, sensors['LCNitrousFill']).data_kg[count-1]-0.6:3.1f}" +
                    ", ThrustKG: "    + f"{cast(LoadCell, sensors['LCThrust']).data_kg[count-1]:3.1f}" +
                    ", PTRunPSI: "    + f"{cast(PressureTransducer, sensors['PTRun']).data_PSI[count-1]:3.1f}" +
                    ", PTEngine: "    + f"{cast(PressureTransducer, sensors['PTPreInjector']).data_PSI[count-1]:3.1f}" +
                    ", PTN2OSupply: " + f"{cast(PressureTransducer, sensors['PTN2OSupply']).data_PSI[count-1]:3.1f}" +
                    ", TCSupply: "    + f"{cast(Thermocouple, sensors['TCNitrousSupply']).data_C[count-1]:3.1f}" +
                    ", TCRun: "       + f"{cast(Thermocouple, sensors['TCNitrousRun']).data_C[count-1]:3.1f}",
                )


        if msvcrt.kbhit():
            key = msvcrt.getch().decode("utf-8")

            if key == "/":
                log("Closing...")
                ljm.close(handle)
                break

            if key in openValveMap: # Open Select Valve
                valveName = openValveMap[key]
                valves[valveName].openValve()

            if key in closeValveMap: # Close Select Valve
                valveName = closeValveMap[key]
                valves[valveName].closeValve()

            if key in getStateMap: # Get select state
                valveName = getStateMap[key]
                state = valves[valveName].currentState
                if state == 1: print(f"{valveName} is open")
                if state == 0: print(f"{valveName} is closed")

            if key == "c": # Close ALL valves
                for tempValve in valves.items():
                    tempValve[1].closeValve()

        log("Data collected.")

        log("Exporting Data to CSV...")
        exportTestDataCSV(times, sensors, dataDirectory, configName, configPath)

        log("Closing Connection...")
        # ljm.close(handle)
        log("End of test.")

if __name__ == "__main__":
    main(sys.argv[1:])

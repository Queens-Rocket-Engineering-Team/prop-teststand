from labjack import ljm
from QDAC_Class import *
import time
import matplotlib.pyplot as plt
import json
import csv
import msvcrt

def jsonDefineIO(handle, configFilename):
    '''
    This function creates the objects for the desired test setup from a JSON file following the format
    specified in /configFramework.json.
    '''

    with open(configFilename, 'r') as f:
        config = json.load(f)
    
    sensorsObjects = {}
    valveObjects = {}

    for sensorType, sensors in config["sensors"].items():
        for sensorName, sensorInfo in sensors.items():
            if sensorType == "thermocouple":
                pin = sensorInfo["pin"]
                offset = sensorInfo["offset"]
                sensorsObjects[sensorName] = thermocouple(handle, pin, offset)
            
            elif sensorType == "pressureTransducer":
                pin = sensorInfo["pin"]
                pressureRange = sensorInfo["maxPressure_PSI"]
                sensorsObjects[sensorName] = pressureTransducer(handle, pin, pressureRange)
            
            elif sensorType == "loadCell":
                negPin = sensorInfo["oddNegPin"]
                posPin = sensorInfo["evenPosPin"]
                maxWeight = sensorInfo["loadRating_N"]
                excitation = sensorInfo["excitation_V"]
                sensitivity = sensorInfo["sensitivity_vV"]
                sensorsObjects[sensorName] = loadCell(handle, posPin, negPin, maxWeight, excitation, sensitivity)

    for valveName, valveInfo in config["valves"].items():
        pin = valveInfo["controlPin"]
        default = valveInfo["defaultState"]
        valveObjects[valveName] = valve(handle, pin, default, valveName)

    return sensorsObjects, valveObjects, config["configName"], config["filePath"]

def takeAllData(sensors):
    ''' Takes sensor dictionary'''
    for sensor in sensors.values(): sensor.takeData()

def exportTestDataCSV(timeStamps, sensors, dataDir, configName, configPath):
    
    # Setting CSV filename
    localTime = time.strftime('%Y-%m-%d %H-%M-%S', time.localtime(time.time()))
    csvFilename = configName + '---' + localTime

    # Setting CSV headers
    csvData = [['Time'] + list(sensors.keys())]
    
    # Assembling CSV rows for each sensor
    for i in range(len(timeStamps)):
        row = [timeStamps[i]]
        for sensor in sensors.values():
            if type(sensor) == thermocouple:
                row.append(sensor.data_celsius[i]) 
            if type(sensor) == pressureTransducer:
                row.append(sensor.data_PSI[i]) 
            if type(sensor) == loadCell:
                row.append(sensor.data_kg[i]) 
        csvData.append(row)
    
    # Writing data to csv
    with open(dataDir + csvFilename, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Config File Name:", configName])
        writer.writerow(["Config File Path:", configPath])
        writer.writerow(["Test Time:", localTime])
        writer.writerows(csvData)
    
    print(f"Data saved to {dataDir + csvFilename}")


handle = ljm.openS("T7","ANY","ANY")
configPath = r"C:\\Users\\Teighin Nordholt\\Desktop\\QRET\\prop_oldtest_code\\prop-teststand\\TestingData\\teighinConfig.json"
ljm.eWriteName(handle, "FIO_DIRECTION", 1) # Set the pin as an output


# Initializing sensors and getting key information from config
print("Initalizing Sensors from config file...")
sensors, valves, configName, dataDirectory = jsonDefineIO(handle, configPath)
print("Sensors Initialized.")

openValveMap = {
    '1': "AVFill",  # FIO0
    '2': "AVDump",  # FIO1
    '3': "AVRun",  # FIO2
    '4': "AVN2Purge1",  # FIO3
    '5': "AVN2Purge2"   # FIO4
}

closeValveMap = {
    'q': "AVFill",  # FIO0
    'w': "AVDump",  # FIO1
    'e': "AVRun",  # FIO2
    'r': "AVN2Purge1",  # FIO3
    't': "AVN2Purge2"   # FIO4
}

getStateMap = {
    'a': "AVFill",  # FIO0
    's': "AVDump",  # FIO1
    'd': "AVRun",  # FIO2
    'f': "AVN2Purge1",  # FIO3
    'g': "AVN2Purge2"   # FIO4
}

startTime = time.time()
sampleSpacing_s = 0.01
times = []
count = 0

print("Enter control keys now:")
lastTime = time.time()
while(True):
    currentTime = time.time()
    

    if (currentTime - lastTime) > sampleSpacing_s:
        takeAllData(sensors)
        times.append(currentTime - startTime)
        count += 1
        if count % 10 == 0:
            print(f"PTN2Supply: {sensors['PTN2Supply'].data_PSI[count-1]:3.1f} | PTN2OSupply: {sensors['PTN2OSupply'].data_PSI[count-1]:3.1f} | PTRunPSI: {sensors['PTRun'].data_PSI[count-1]:3.1f} | PTEngine: {sensors['PTPreInjector'].data_PSI[count-1]:3.1f} | RunKG: {sensors['LCRun'].data_kg[count-1]:3.1f}")
        
        
    if msvcrt.kbhit():
        key = msvcrt.getch().decode('utf-8')

        if key == '/':
            print("Closing...")
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
        
        if key == 'c': # Close ALL valves
            for tempValve in valves.items():
                tempValve[1].closeValve()

print("Data collected.")

print("Exporting Data to CSV...")
exportTestDataCSV(times, sensors, dataDirectory, configName, configPath)

print("Closing Connection...")
# ljm.close(handle)
print("End of test.")

# %%
import csv
import os
from math import ceil, sqrt

import numpy as np
import plotly.graph_objects as go  #type:ignore  # Plotly no typing
from plotly.subplots import make_subplots  #type:ignore


def extractData(csvPath: str,
                ) -> tuple[list[float],
                           list[str],
                           dict[str, list[float]],
                           str,
                           str,
                           str,
                           ]:
    times = []
    sensorData: dict[str, list[float]] = {}
    sensorNames = []
    configName = ""
    configPath = ""
    testTime = ""

    with open(csvPath, "r") as csvFile:
        csvReader = csv.reader(csvFile)
        for row in csvReader:
            if row[0] == "Config File Name:": # Grabbing config file name
                configName = row[1]

            elif row[0] == "Config File Path:": # Acknowledging config path storage
                print(f"Config Path Found: {row[1]}")


            elif row[0] == "Test Time:": # Grabbing time of the test
                testTime = row[1]

            elif row[0] == "Time":
                for col in row:
                    if col == "Time": continue # Ignoring time header for sensor list
                    sensorData[col] = []
                    sensorNames.append(col)

                print(f"{len(sensorNames)} sensors found: {sensorNames}")

            else:
                times.append(float(row[0])) # Grabbing time values from first column
                for i, col in enumerate(sensorNames, start=1): # Using enumerate to address sensor names list as well as index the proper column
                    # Data values are stored at specific column indices, but we want to store them to a named dictionary
                    sensorData[col].append(float(row[i]))

    return times, sensorNames, sensorData, configName, configPath, testTime

# %%
workingDir = "C:\\code\\QRET\\Propulsion Test Stand DAQ\\FullTestCode"
os.chdir(workingDir)
print(f"Current Working Directory: {os.getcwd()}")
csvPath = r"..\\TestingData\\ControlTesting\\First Control Test---2024-05-26 22-28-47--SecondColdFlow--500gC02"
csvFileName = os.path.basename(csvPath)

dataTimes, sensorNames, dataVals, configName, configPath, testTime = extractData(csvPath)

print(f"Config Name: {configName}\nTest Time: {testTime}")

# Plotting nonsense
numCols = int(ceil(sqrt(len(dataVals))))  # Calculate number of columns
numRows = int(ceil(len(dataVals) / numCols))  # Calculate number of rows

# Plotly Code
fig = make_subplots(rows=3, cols=3,
                    subplot_titles=sensorNames,
                    )

for i, name in enumerate(sensorNames):
    # Logic to figure out which plot to use
    currentRow = (i // numCols) + 1
    currentCol = (i % numCols) + 1

    fig.add_trace(go.Scatter(x=dataTimes, y=dataVals[name]),
                  row=currentRow, col=currentCol,
                  )
    fig.update_yaxes(range=(0, 1.5*np.max(dataVals[name])), row=currentRow, col=currentCol)

for i in range(1, 4):  # Adjust according to the number of rows
    for j in range(1, 4):  # Adjust according to the number of columns
        fig.update_xaxes(matches="x", row=i, col=j)


fig.update_layout(height=600, width=800, title_text=f"Sensor Data from: {csvFileName}")
fig.show()


""" MATPLOTLIB CODE
fig, axs = plt.subplots(numRows, numCols, figsize=(9,6))

print("Showing data")
for i, name in enumerate(sensorNames):
    # Logic to figure out which plot to use
    row = i // numCols
    col = i % numCols


    axs[row, col].plot(dataTimes, dataVals[name])  # Plot the data corresponding to the current dataset
    axs[row, col].set_ylim(0, np.max(dataVals[name])*1.5)

    # Set title for the subplot
    axs[row, col].set_title(name)

plt.tight_layout()
"""








# %%

import csv


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

    try:
        with open(csvPath, "r") as csvFile:
            print(f"Opening file: {csvPath}")
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
    except FileNotFoundError:
        print(f"Specified file path could not be found: {csvPath}")

    return times, sensorNames, sensorData, configName, configPath, testTime

import csv
from typing import Any

from PySide6.QtCore import Qt  #type:ignore
from PySide6.QtGui import QFont  #type:ignore
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget  #type:ignore


class DataVisWidget(QWidget):
    def __init__(self,
                 *args: Any,
                 **kwargs: Any,
                 ) -> None:

        super().__init__(*args, **kwargs)

        self.mainLayout = QVBoxLayout()
        self.setLayout(self.mainLayout)
        self.mainLayout.setAlignment(Qt.AlignTop)  # type:ignore # QT not typed

        # Creating title widget
        self.titleLabel = QLabel("Data Visualization Dashboard")
        titleFont = QFont("Arial", 16, QFont.Weight.Bold)
        self.titleLabel.setFont(titleFont)
        self.mainLayout.addWidget(self.titleLabel)

        # Setting up the overall widget where the file will be loaded from and file path displayed
        self.fileLoadWidget = QWidget()
        self.fileLoadLayout = QHBoxLayout(self.fileLoadWidget)

        # Add a button to select a file to load
        self.loadFileButton = QPushButton("Choose File")
        self.loadFileButton.clicked.connect(self.openFileDialog)
        self.fileLoadLayout.addWidget(self.loadFileButton, 1)
        self.selectedFilePath = ""

        # Add a label to display the loaded file path
        self.filePathLabel = QLabel("No file loaded")
        self.fileLoadLayout.addWidget(self.filePathLabel, 7)

        # Add the file load widget to the main layout
        self.mainLayout.addWidget(self.fileLoadWidget)

        self.dataTimes: list[float] = []
        self.sensorNames: list[str] = []
        self.dataVals: dict[str, list[float]] = {}
        self.configName = ""
        self.configPath = ""
        self.testTime = ""

    def openFileDialog(self) -> None:
        # Open file dialog and get selected file path
        file_path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "All Files (*)")

        # Update the label with the selected file path
        if file_path:
            self.filePathLabel.setText(file_path)
            self.selectedFilePath = file_path
            self.dataTimes, self.sensorNames, self.dataVals, self.configName, self.configPath, self.testTime = self.extractData(self.selectedFilePath)

    def extractData(self,
                    csvPath: str,
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

import csv
from typing import Any

import pyqtgraph as pg  #type:ignore
from PySide6.QtCore import Qt  #type:ignore
from PySide6.QtGui import QFont  #type:ignore
from PySide6.QtWidgets import QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget  #type:ignore

from qretproptools.gui.full_Gui.BaseDashboard import BaseDashboard


class DataVisWidget(BaseDashboard):
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

        # Creating file loading widget where the file will be loaded from and file path displayed
        self.fileLoadWidget = QWidget()
        self.fileLoadLayout = QHBoxLayout(self.fileLoadWidget)

        # Add a button to select a file to load
        self.loadFileButton = QPushButton("Choose File")
        self.loadFileButton.clicked.connect(self.openFileDialog)
        self.fileLoadLayout.addWidget(self.loadFileButton, 1) # Scaling button to take up 1 part of the layout
        self.selectedFilePath = "" # Initializing selected file path

        # Add a label to display the loaded file path
        self.filePathLabel = QLabel("No file loaded")
        self.fileLoadLayout.addWidget(self.filePathLabel, 7) # Scaling label to take up 7 parts of the layout

        # Add the file load widget to the main layout
        self.mainLayout.addWidget(self.fileLoadWidget)

        # Initialize data storage variables
        self.dataTimes: list[float] = []
        self.sensorNames: list[str] = []
        self.dataVals: dict[str, list[float]] = {}
        self.configName = ""
        self.configPath = ""
        self.testTime = ""

        # Add a layout for the dataset buttons
        self.buttonLayout = QHBoxLayout()
        self.mainLayout.addLayout(self.buttonLayout)

        # Create a button group for the dataset buttons
        self.buttonGroup = QButtonGroup(self)
        self.buttonGroup.setExclusive(True)

        # Add a plot widget for displaying the graph
        self.plotWidget = pg.PlotWidget()
        self.mainLayout.addWidget(self.plotWidget)

    def openFileDialog(self) -> None:
        # Open file dialog and get selected file path
        file_path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "CSV Files (*.csv)")

        # Update the label with the selected file path
        if file_path:
            try:
                self.dataTimes, self.sensorNames, self.dataVals, self.configName, self.configPath, self.testTime = self.extractData(file_path)
                self.filePathLabel.setText(file_path)
                self.selectedFilePath = file_path
                self.updateButtons()
                self.updateGraph(sensorName=self.sensorNames[0])
                self.buttonGroup.buttons()[0].setChecked(True)
            except ValueError:
                self.openErrorWindow("Could not load data. Check file format adheres to the template.", "File Load Error")
            except Exception:
                self.openErrorWindow("Unexpected Error: Check file format and try again.")

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

    def updateButtons(self) -> None:
        # Clear existing buttons
        for i in reversed(range(self.buttonLayout.count())):
            self.buttonLayout.itemAt(i).widget().setParent(None) #type:ignore # QT not typed so doesn't like None parent

        # Add a button for each sensor name
        for sensorName in self.sensorNames:
            button = QPushButton(sensorName)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, name=sensorName: self.updateGraph(name))
            self.buttonLayout.addWidget(button)
            self.buttonGroup.addButton(button)

    def updateGraph(self, sensorName: str) -> None:
        self.plotWidget.clear()

        # Determine the y-axis label based on the sensor type
        if sensorName.startswith("PT"):
            y_label = "Pressure [PSI]"
        elif sensorName.startswith("TC"):
            y_label = "Temperature [C]"
        elif sensorName.startswith("LC"):
            y_label = "Load [KG]"
        else:
            y_label = "Value"

        # Set the x-axis and y-axis labels
        self.plotWidget.setLabel("bottom", "Time [t]")
        self.plotWidget.setLabel("left", y_label)

        self.plotWidget.plot(self.dataTimes, self.dataVals[sensorName], pen=pg.mkPen(width=1), name=sensorName)

from typing import Any

#import pyqtgraph as pg  #type:ignore
from PySide6.QtCore import Qt  #type:ignore
from PySide6.QtGui import QFont  #type:ignore
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget  #type:ignore

from libqretprop.extractData import extractData
from qretproptools.gui.full_Gui.BaseDashboard import BaseDashboard
from qretproptools.gui.full_Gui.SelectMultiPlotWidget import SelectMultiPlotWidget


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
        self.dataDict: dict[str, list[float]] = {}
        self.configName = ""
        self.configPath = ""
        self.testTime = ""

        # Add a plot widget for displaying the graph
        self.plotWidget: SelectMultiPlotWidget = None #type:ignore # Only to be generated in the open file dialog

        self.extractData = extractData

    def openFileDialog(self) -> None:
        # Open file dialog and get selected file path
        file_path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "CSV Files (*.csv)")

        # Update the label with the selected file path
        if file_path:
            try:
                # Extracting data from the selected file
                self.dataTimes, self.sensorNames, self.dataDict, self.configName, self.configPath, self.testTime = self.extractData(file_path)
                self.filePathLabel.setText(file_path)
                self.selectedFilePath = file_path

                if self.plotWidget is not None: # Delete the existing plot widget if it exists
                    self.plotWidget.deleteLater()
                    self.plotWidget = SelectMultiPlotWidget(self.dataDict, self.dataTimes)
                    self.mainLayout.addWidget(self.plotWidget)
                else:
                    self.plotWidget = SelectMultiPlotWidget(self.dataDict, self.dataTimes)
                    self.mainLayout.addWidget(self.plotWidget)

            except ValueError:
                self.openErrorWindow("Could not load data. Check file format adheres to the template.", "File Load Error")
            except Exception as e:
                print(e)
                self.openErrorWindow("Unexpected Error: Check file format and try again.")

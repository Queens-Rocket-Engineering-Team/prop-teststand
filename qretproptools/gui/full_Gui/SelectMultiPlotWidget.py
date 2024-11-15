import pyqtgraph as pg  #type:ignore
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QVBoxLayout, QWidget


class SelectMultiPlotWidget(QWidget):
    def __init__(self, data: dict[str, list[float]], dataTimes: list[float]) -> None:
        super().__init__()

        # Setting up data storage variables. Kinda hate this way of doing it but it'll work for now
        self.dataVals = list(data.values())
        self.sensorNames = list(data.keys())
        self.dataDict = data

        self.dataTimes = dataTimes

        self.mainLayout = QHBoxLayout()
        self.setLayout(self.mainLayout)

        # Add a layout for the dataset buttons
        self.buttonLayout = QVBoxLayout()
        self.mainLayout.addLayout(self.buttonLayout, stretch=1)
        self.buttonLayout.setAlignment(Qt.AlignTop)  # type:ignore # QT not typed

        # Create a button group for the dataset buttons
        self.buttonGroup = QButtonGroup()
        self.buttonGroup.setExclusive(False) # Allow multiple buttons to be selected

        # Add buttons to the button list
        self.updateButtons()

        # Add a layout for the plots
        self.plotLayout = QVBoxLayout()
        self.mainLayout.addLayout(self.plotLayout, stretch=7)

        # Creating the plot widgets
        self.plotWidgets: dict[str, pg.PlotWidget] = {}
        self.generateGraphs()

        # Add a placeholder widget to take up the right side of the screen when no datasets are selected
        self.placeholderWidget = QWidget()
        self.placeholderWidget.setStyleSheet("background-color: lightgray;")
        self.plotLayout.addWidget(self.placeholderWidget, stretch=1)

    def updateButtons(self) -> None:
        # Clear existing buttons
        for i in reversed(range(self.buttonLayout.count())): # Reverse function because removing from the front causes indexing issues
            self.buttonLayout.itemAt(i).widget().setParent(None) #type:ignore # QT not typed so doesn't like None parent

        # Add a button for each sensor name
        for sensorName in self.sensorNames:
            button = QPushButton(sensorName)
            button.setCheckable(True)
            button.toggled.connect(self.updateGraph)
            self.buttonLayout.addWidget(button)
            self.buttonGroup.addButton(button)

    def updateXRange(self, viewBox: pg.ViewBox) -> None:
        # Get the x-axis range from the viewBox that triggered the signal
        xMin, xMax = viewBox.viewRange()[0]

        # Update the x-axis range of all plots
        for plotWidget in list(self.plotWidgets.values()):
            if plotWidget.getViewBox() != viewBox:
                plotWidget.setXRange(xMin, xMax, padding=0) # type: ignore # Padding is a valid argument but not typed

    def generateGraphs(self) -> None:
        # Generate a plot for each sensor and store it in the dictionary
        for sensorName in self.sensorNames:
            plotWidget = pg.PlotWidget()
            plotWidget.plot(self.dataTimes, self.dataDict[sensorName], pen=pg.mkPen(width=1), name=sensorName)

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
            plotWidget.setLabel("bottom", "Time [t]")
            plotWidget.setLabel("left", y_label)

            # Set title for the subplot
            plotWidget.setTitle(sensorName)

            # Set the range limits for the x-axis
            plotWidget.setLimits(
                xMin=min(self.dataTimes), xMax=max(self.dataTimes),
            )

            self.plotWidgets[sensorName] = plotWidget

            # Connect the x-axis range change signal to the updateXRange function so all plots are in sync
            plotWidget.getViewBox().sigXRangeChanged.connect(self.updateXRange)
            # getViewBox returns the ViewBox of the plotWidget and feeds it to the updateXRange function

    def updateGraph(self) -> None:
        # Clear the plot layout
        for i in reversed(range(self.plotLayout.count())): # Reversed to avoid index issues
            item = self.plotLayout.itemAt(i)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)  # type:ignore # QT not typed

        # Get the list of selected sensors
        selectedSensors = [button.text() for button in self.buttonGroup.buttons() if button.isChecked()]

        # Add the selected plots to the plot layout
        if selectedSensors:
            self.placeholderWidget.hide()
            for sensorName in selectedSensors:
                self.plotLayout.addWidget(self.plotWidgets[sensorName], stretch=1)
        else:
            # If no sensors are selected, show the placeholder widget
            # Without re-adding it to the layout it opens in its own window. Seems weird to me but maybe intended code design
            self.plotLayout.addWidget(self.placeholderWidget, stretch=1)
            self.placeholderWidget.show()

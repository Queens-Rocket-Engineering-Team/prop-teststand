import sys

from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

from libqretprop.extractData import extractData
from qretproptools.gui.full_Gui.SelectMultiPlotWidget import SelectMultiPlotWidget


def main() -> None:
    try:
        path = "C:\\Users\\Noah\\Downloads\\FirstHotFire.csv"

        dataTimes, sensorNames, dataSets, configName, configPath, testTime = extractData(path)

        # Create the application
        app = QApplication(sys.argv)

        window = QMainWindow()
        window.resize(1000, 600)

        mainWidget = QWidget()
        window.setCentralWidget(mainWidget)

        mainLayout = QVBoxLayout(mainWidget)
        mainWidget.setLayout(mainLayout)

        # Create an instance of SelectMultiPlotWidget and add it to the main layout
        selectMultiPlotWidget = SelectMultiPlotWidget(dataSets, dataTimes)
        mainLayout.addWidget(selectMultiPlotWidget)

        window.show()

        sys.exit(app.exec())
    except Exception as e:
        print(f"An error has occurred: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()

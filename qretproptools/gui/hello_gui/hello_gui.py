#type:ignore

import sys

import numpy as np
import pyqtgraph as pg  #type:ignore  # no stubs
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget


# This is the most basic example of a PyQtGraph plot in a PyQt application. It creates a window
# with a plot of a sine wave and a button that toggles between a sine wave and a square wave.

class PlotWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()

        # Set up the main layout
        self._layout = QVBoxLayout()
        self.setLayout(self._layout)

        # Create a PyQtGraph plot widget
        self.plot_widget = pg.PlotWidget()
        self._layout.addWidget(self.plot_widget)

        # Add a button to toggle between sine and square wave
        self.toggle_button = QPushButton("Toggle Waveform")
        self._layout.addWidget(self.toggle_button)

        # Connect the button's click signal to the function that toggles the wave
        self.toggle_button.clicked.connect(self.toggle_waveform)

        # Initialize wave type and plot
        self.is_sine = True
        self.plot_waveform()

    def plot_waveform(self) -> None:
        # Generate x-axis data
        x = np.linspace(0, 2 * np.pi, 1000)

        # Generate sine or square wave data based on the current state
        if self.is_sine:
            y = np.sin(x)
        else:
            y = np.sign(np.sin(x))

        # Plot the data on the graph
        self.plot_widget.plot(x, y, clear=True)

    def toggle_waveform(self) -> None:
        # Toggle the wave type
        self.is_sine = not self.is_sine
        # Re-plot the waveform
        self.plot_waveform()

def main() -> None:

    # Create the application
    app = QApplication(sys.argv)

    # Create the main window and show it
    window = PlotWindow()
    window.setWindowTitle("PyQtGraph - Sine/Square Wave Toggle")
    window.show()

    # Run the event loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

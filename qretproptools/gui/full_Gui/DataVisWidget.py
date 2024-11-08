from typing import Any

from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget  #type:ignore


class DataVisWidget(QWidget):
    def __init__(self,
                 *args: Any,
                 **kwargs: Any,
                 ) -> None:

        super().__init__(*args, **kwargs)
        layout = QVBoxLayout()
        self.setLayout(layout)

        # Creating title widget
        layout.addWidget(QLabel("Data Visualization Dashboard"))

        # Setting up the overall widget where the file will be loaded from and file path displayed
        self.fileLoadArea = QWidget()
        self.fileLoadLayout = QVBoxLayout(self.fileLoadArea)

        # Add a button to select a file to load
        self.loadFile = QPushButton("Load File")
        layout.addWidget(self.loadFile)

        # Add a label to display the loaded file path


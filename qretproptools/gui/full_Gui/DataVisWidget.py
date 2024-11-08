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

        # Add a label to display the loaded file path
        self.filePathLabel = QLabel("No file loaded")
        self.fileLoadLayout.addWidget(self.filePathLabel, 7)

        # Add the file load widget to the main layout
        self.mainLayout.addWidget(self.fileLoadWidget)

    def openFileDialog(self) -> None:
        # Open file dialog and get selected file path
        file_path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "All Files (*)")

        # Update the label with the selected file path
        if file_path:
            self.filePathLabel.setText(file_path)

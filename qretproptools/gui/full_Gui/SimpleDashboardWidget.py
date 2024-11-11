from typing import Any

from PySide6.QtCore import Qt  #type:ignore
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout

from qretproptools.gui.full_Gui.BaseDashboard import BaseDashboard


class SimpleDashboardWidget(BaseDashboard):
    def __init__(self,
                 name: str,
                 *args: Any,
                 **kwargs: Any,
                 ) -> None:
        super().__init__(*args, **kwargs)

        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"This is the {name} dashboard"))
        self.setLayout(layout)

        self.errorButton = QPushButton("Trigger an error")
        self.errorButton.clicked.connect(lambda: self.openErrorWindow("This is an error message!",
                                                                      "Why did you click this. Dumbass."))
        layout.addWidget(self.errorButton)

        layout.setAlignment(Qt.AlignTop)  # type:ignore # QT not typed

from typing import Any

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget  #type:ignore


class BasicDashboardWidget(QWidget):
    def __init__(self,
                 name: str,
                 *args: Any,
                 **kwargs: Any,
                 ) -> None:
        super().__init__(*args, **kwargs)

        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"This is the {name} dashboard"))
        self.setLayout(layout)

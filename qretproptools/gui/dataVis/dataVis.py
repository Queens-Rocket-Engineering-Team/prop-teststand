import sys
from typing import Any

import pyqtgraph as pg  #type:ignore  # no stubs
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget  #type:ignore


class DashboardWidget(QWidget):
    def __init__(self,
                 name: str,
                 *args: Any,
                 **kwargs: Any,
                 ) -> None:
        super().__init__(*args, **kwargs)
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"This is the {name} dashboard"))
        self.setLayout(layout)

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        # Central widget setup
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        # Main layout
        main_layout = QHBoxLayout(self.central_widget)

        # Sidebar layout (for navigation buttons)
        self.sidebar = QVBoxLayout()


        #self.sidebar.setAlignment(Qt.AlignTop)  # Align buttons to the top

        # Create buttons for each dashboard
        self.dashboard_buttons = {
            "Dashboard 1": QPushButton("Dashboard 1"),
            "Dashboard 2": QPushButton("Dashboard 2"),
            "Dashboard 3": QPushButton("Dashboard 3"),
        }

        # Add buttons to the sidebar layout and connect them to a function
        for name, button in self.dashboard_buttons.items():
            button.clicked.connect(lambda _checked, name=name: self.load_dashboard(name))
            self.sidebar.addWidget(button) 

        # Content area where dashboards will load
        self.content_area = QWidget()
        self.content_layout = QVBoxLayout(self.content_area)

        # Add sidebar and content area to main layout
        main_layout.addLayout(self.sidebar, 1)  # Sidebar takes 1 part of the width
        main_layout.addWidget(self.content_area, 4)  # Content area takes 4 parts

        # Load the first dashboard by default
        self.load_dashboard("Dashboard 1")

    def load_dashboard(self,
                       dashboard_name: str,
                       ) -> None:
        # Clear current content layout
        for i in reversed(range(self.content_layout.count())):
            widget = self.content_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        # Add the selected dashboard to the content area
        dashboard = DashboardWidget(dashboard_name)
        self.content_layout.addWidget(dashboard)


def main() -> None:
    # Create the application
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

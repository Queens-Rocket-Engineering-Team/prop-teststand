from PySide6.QtCore import Qt  #type:ignore
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QPushButton, QVBoxLayout, QWidget  #type:ignore

from qretproptools.gui.full_Gui.BasicDashboardWidget import BasicDashboardWidget


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        # Setting initial window size
        self.resize(1000, 700)

        # Main widget setup
        self.mainWidget = QWidget()
        self.setCentralWidget(self.mainWidget)

        # Main layout
        mainLayout = QHBoxLayout(self.mainWidget)

        # Sidebar layout (for navigation buttons)
        self.sidebar = QVBoxLayout()
        self.sidebar.setAlignment(Qt.AlignTop)  # type:ignore # QT not typed

        # Create buttons for each dashboard
        self.dashboardButtons = {
            "DataVis"    : QPushButton("Data Visualization"),
            "Dashboard 2": QPushButton("Dashboard 2"),
            "Dashboard 3": QPushButton("Dashboard 3"),
        }

        # Add buttons to the sidebar layout and connect them to a function
        for name, button in self.dashboardButtons.items():
            button.clicked.connect(lambda _checked, name=name: self.loadDashboard(name))
            self.sidebar.addWidget(button)

        # Content area where dashboards will load
        self.contentArea = QWidget()
        self.contentLayout = QVBoxLayout(self.contentArea)

        # Add sidebar and content area to main layout
        mainLayout.addLayout(self.sidebar, 1)  # Sidebar takes 1 part of the width
        mainLayout.addWidget(self.contentArea, 4)  # Content area takes 4 parts

        # Load the first dashboard by default
        self.loadDashboard("DataVis")

    def loadDashboard(self,
                       dashboard_name: str,
                       ) -> None:
        # Clear current content layout
        for i in reversed(range(self.contentLayout.count())):
            widget = self.contentLayout.itemAt(i).widget()
            if widget:
                widget.setParent(None) #type:ignore # QT not typed

        # Add the selected dashboard to the content area
        dashboard = BasicDashboardWidget(dashboard_name)
        self.contentLayout.addWidget(dashboard)

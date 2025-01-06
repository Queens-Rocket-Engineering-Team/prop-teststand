from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QMainWindow, QPushButton, QVBoxLayout, QWidget  #type:ignore

from qretproptools.gui.full_Gui.controlPanelWidget import ControlPanelWidget
from qretproptools.gui.full_Gui.DataVisWidget import DataVisWidget
from qretproptools.gui.full_Gui.SimpleDashboardWidget import SimpleDashboardWidget


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.dashboardDict = {
            "Control Panel": ControlPanelWidget("qretproptools\\gui\\full_Gui\\teststand.png"),
            "Data Visualization": DataVisWidget(),
            "Simple Dashboard 1": SimpleDashboardWidget("Simple Dashboard 1"),
            "Simple Dashboard 2": SimpleDashboardWidget("Simple Dashboard 2"),
        }

        # Setting initial window size
        self.resize(1300, 700)

        # Main widget setup
        self.mainWidget = QWidget()
        self.setCentralWidget(self.mainWidget)
        mainLayout = QHBoxLayout(self.mainWidget)

        # Sidebar layout (for navigation buttons)
        self.sidebar = QVBoxLayout()
        self.sidebar.setAlignment(Qt.AlignTop)  # type:ignore # QT not typed

        # Create button group for sidebar buttons
        self.buttonGroup = QButtonGroup(self)
        self.buttonGroup.setExclusive(True) # Only one button can be selected at a time

        # Create buttons for each dashboard
        self.dashboardButtons = {name: QPushButton(name) for name in self.dashboardDict}

        # Add buttons to the sidebar layout and connect them each to loading their respective dashboard
        for name, button in self.dashboardButtons.items():
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, name=name: self.loadDashboard(self.dashboardDict[name]))
            buttonFont = QFont("Arial", 10)
            button.setFont(buttonFont)
            self.sidebar.addWidget(button)
            self.buttonGroup.addButton(button)


        # Content area where dashboards will load
        self.contentArea = QWidget()
        self.contentLayout = QVBoxLayout(self.contentArea)

        # Add sidebar and content area to main layout
        mainLayout.addLayout(self.sidebar, 1)  # Sidebar takes 1 part of the width
        mainLayout.addWidget(self.contentArea, 4)  # Content area takes 4 parts

        # Load the first dashboard by default
        first_dashboard_name = next(iter(self.dashboardDict))
        self.loadDashboard(self.dashboardDict[first_dashboard_name])
        self.dashboardButtons[first_dashboard_name].setChecked(True)

    def loadDashboard(self,
                       dashboardWidget: QWidget,
                       ) -> None:
        # Clear current content layout
        for i in reversed(range(self.contentLayout.count())):
            widget = self.contentLayout.itemAt(i).widget()
            if widget:
                widget.setParent(None) #type:ignore # QT not typed

        # Add the selected dashboard to the content area
        self.contentLayout.addWidget(dashboardWidget)

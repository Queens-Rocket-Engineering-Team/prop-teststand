from PySide6.QtCore import Qt  #type:ignore
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QPushButton, QVBoxLayout, QWidget  #type:ignore

from qretproptools.gui.full_Gui.BasicDashboardWidget import BasicDashboardWidget
from qretproptools.gui.full_Gui.DataVisWidget import DataVisWidget


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.dashboardDict = {
            "Data Visualization": DataVisWidget(),
            "Basic Dashboard 1": BasicDashboardWidget("Basic Dashboard 1"),
            "Basic Dashboard 2": BasicDashboardWidget("Basic Dashboard 2"),
        }

        # Setting initial window size
        self.resize(1000, 700)

        # Main widget setup
        self.mainWidget = QWidget()
        self.setCentralWidget(self.mainWidget)
        mainLayout = QHBoxLayout(self.mainWidget)


        # Sidebar layout (for navigation buttons)
        self.sidebar = QVBoxLayout()
        self.sidebar.setAlignment(Qt.AlignTop)  # type:ignore # QT not typed

        # Create buttons for each dashboard
        self.dashboardButtons = {name: QPushButton(name) for name in self.dashboardDict}

        # Add buttons to the sidebar layout and connect them each to loading their respective dashboard
        for name, button in self.dashboardButtons.items():
            button.clicked.connect(lambda _checked, name=name: self.loadDashboard(self.dashboardDict[name]))
            self.sidebar.addWidget(button)


        # Content area where dashboards will load
        self.contentArea = QWidget()
        self.contentLayout = QVBoxLayout(self.contentArea)

        # Add sidebar and content area to main layout
        mainLayout.addLayout(self.sidebar, 1)  # Sidebar takes 1 part of the width
        mainLayout.addWidget(self.contentArea, 4)  # Content area takes 4 parts

        # Load the first dashboard by default
        self.loadDashboard(next(iter(self.dashboardDict.values())))

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

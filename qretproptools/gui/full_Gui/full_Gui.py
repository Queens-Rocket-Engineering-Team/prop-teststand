import sys

from PySide6.QtWidgets import QApplication  #type:ignore

from qretproptools.gui.full_Gui.MainWindow import MainWindow


def main() -> None:

    # Create the application
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

from typing import Any

from PySide6.QtWidgets import QMessageBox, QWidget


class BaseDashboard(QWidget):
    def __init__(self,
                 *args: Any,
                 **kwargs: Any,
                 ) -> None:
        super().__init__(*args, **kwargs)


    def openErrorWindow(self, message: str, title: str = "Error") -> None:
        """Open an error dialog window.

        The message and title of the window can be specified.
        """

        error_dialog = QMessageBox()
        error_dialog.setIcon(QMessageBox.Icon.Critical) # The X error icon on the left of the error message
        error_dialog.setWindowTitle(title)
        error_dialog.setText(message)
        error_dialog.adjustSize() # Adjust size to fit content
        error_dialog.adjustPosition(self) # Open error window centered on widget raising the error
        error_dialog.exec()

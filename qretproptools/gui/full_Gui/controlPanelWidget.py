from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QLabel, QVBoxLayout, QWidget


class ControlPanelWidget(QGraphicsView):
    def __init__(self, photo_path: str) -> None:
        super().__init__()

        # Set up the scene
        self.photo_scene = QGraphicsScene(self)
        self.setScene(self.photo_scene)

        # Load and display the image
        pixmap = QPixmap(photo_path)
        self.photo_item = QGraphicsPixmapItem(pixmap)
        self.photo_scene.addItem(self.photo_item)

        # Enable mouse tracking
        self.setMouseTracking(True)

        # Enable dragging and zooming
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.zoom_factor = 1.0

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            print("Mouse click at:", event.pos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Capture mouse movement
        print("Mouse move at:", event.pos())
        super().mouseMoveEvent(event)

    # def wheelEvent(self, event):
    #     # Zoom in or out with the mouse wheel
    #     if event.angleDelta().y() > 0:
    #         self.zoom_factor *= 1.1
    #     else:
    #         self.zoom_factor *= 0.9
    #     self.setTransform(self.transform().scale(self.zoom_factor, self.zoom_factor))


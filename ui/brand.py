"""Render the author's supplied brand image in the bookroom's logo badge."""
from pathlib import Path
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QLabel


class BrandMark(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('brandMark')
        self.setFixedSize(42, 42)
        self.setAlignment(Qt.AlignCenter)
        self.setAccessibleName('写道标志')
        self.setToolTip('写道')
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
        self._logo = QPixmap(str(root / 'assets' / 'brand-logo.png'))
        if self._logo.isNull():
            self.setText('道')

    def paintEvent(self, event):
        if self._logo.isNull():
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 12, 12)
        painter.setClipPath(clip)
        painter.drawPixmap(bounds, self._logo, QRectF(self._logo.rect()))
        painter.end()

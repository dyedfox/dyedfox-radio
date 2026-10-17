"""Divider line drawn in a soft tint of the text color.

A plain QFrame line paints in the full text color under Breeze, and even the
muted Mid role stood out under Omarchy. Blending a little of the text color
into the background separates areas without drawing the eye, and follows any
palette (Breeze light/dark, live Omarchy theme switches) on its own.
"""
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QPainter, QPalette
from PyQt6.QtWidgets import QSizePolicy, QWidget

# How far from the background toward the text color the line sits.
_STRENGTH = 0.15
# Same 3px slot as a QFrame HLine/VLine, with the 1px line centered, so
# swapping one in doesn't shift the layout.
_SLOT = 3


class Divider(QWidget):
    def __init__(self, vertical: bool = False, parent=None):
        super().__init__(parent)
        self._vertical = vertical
        if vertical:
            self.setFixedWidth(_SLOT)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        else:
            self.setFixedHeight(_SLOT)
            self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        palette = self.palette()
        bg = palette.color(QPalette.ColorRole.Window)
        fg = palette.color(QPalette.ColorRole.WindowText)
        color = QColor.fromRgbF(
            bg.redF() + (fg.redF() - bg.redF()) * _STRENGTH,
            bg.greenF() + (fg.greenF() - bg.greenF()) * _STRENGTH,
            bg.blueF() + (fg.blueF() - bg.blueF()) * _STRENGTH,
        )
        r = self.rect()
        line = (QRect(r.width() // 2, 0, 1, r.height()) if self._vertical
                else QRect(0, r.height() // 2, r.width(), 1))
        painter = QPainter(self)
        painter.fillRect(line, color)
        painter.end()

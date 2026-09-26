from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QPalette

from ui import glyphs
from ui.omarchy_theme import on_theme_changed


class _ElidedLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        super().setText(text)

    def setText(self, text: str):
        self._full = text
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        w = self.width()
        if w <= 0:
            super().setText(self._full)
            return
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, w))


class NowPlayingBar(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self._station = ""
        self._song = ""
        self._clickable = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        self._icon = QLabel()
        self._icon.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._update_icon()
        on_theme_changed(self._update_icon)
        layout.addWidget(self._icon)

        self._label = _ElidedLabel(self.tr("Not playing"))
        layout.addWidget(self._label, 1)

    def _update_icon(self):
        # A QLabel pixmap is a snapshot, so on Omarchy it's redrawn whenever
        # the theme changes to pick up the new glyph color.
        icon = glyphs.icon("audio-x-generic")
        self._icon.setPixmap(icon.pixmap(QSize(20, 20), self.devicePixelRatioF()))

    def set_clickable(self, enabled: bool):
        self._clickable = enabled
        self.setCursor(Qt.CursorShape.PointingHandCursor if (self._station and enabled) else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._clickable:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_station(self, name: str):
        self._station = name
        self._song = ""
        self._update()

    def set_song(self, title: str):
        self._song = title
        self._update()

    def clear_song(self):
        self._song = ""
        self._update()

    def set_error(self):
        self._song = self.tr("Stream unavailable")
        self._update()

    def set_reconnecting(self, attempt: int):
        self._song = self.tr("Reconnecting…")
        self._update()

    def _update(self):
        if self._song:
            self._label.setText(f"{self._station}  —  {self._song}")
        else:
            self._label.setText(self._station or self.tr("Not playing"))

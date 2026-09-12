from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QSlider
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon

from ui.omarchy_theme import is_omarchy, on_theme_changed, tinted_icon


class ControlBar(QWidget):
    playback_toggled = pyqtSignal()
    volume_changed = pyqtSignal(int)
    mute_toggled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        self._play_stop_btn = QPushButton()
        self._play_stop_btn.setFlat(True)
        self._play_stop_btn.clicked.connect(self.playback_toggled)
        layout.addWidget(self._play_stop_btn)

        layout.addStretch()

        self._mute_btn = QPushButton()
        self._mute_btn.setFlat(True)
        self._mute_btn.setFixedSize(24, 24)
        self._mute_btn.setCheckable(True)
        self._mute_btn.setToolTip(self.tr("Mute"))
        self._mute_btn.clicked.connect(self.mute_toggled)
        layout.addWidget(self._mute_btn)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(80)
        self._slider.setFixedWidth(140)
        self._slider.valueChanged.connect(self.volume_changed)
        layout.addWidget(self._slider)

        self.set_playing(False)
        self._update_mute_icon(False)
        on_theme_changed(lambda: self._update_mute_icon(self._mute_btn.isChecked()))

    def set_playing(self, playing: bool):
        icon = QIcon.fromTheme("media-playback-stop" if playing else "media-playback-start")
        label = self.tr("Stop") if playing else self.tr("Play")
        if icon.isNull():
            # Not every icon theme ships these names (e.g. Yaru); fall back to
            # a glyph rather than leaving the button with no icon at all.
            label = f"{'⏹' if playing else '▶'} {label}"
        self._play_stop_btn.setIcon(icon)
        self._play_stop_btn.setText(label)

    def set_volume_slider(self, value: int):
        self._slider.blockSignals(True)
        self._slider.setValue(value)
        self._slider.blockSignals(False)

    def set_muted(self, muted: bool):
        self._mute_btn.blockSignals(True)
        self._mute_btn.setChecked(muted)
        self._mute_btn.blockSignals(False)
        self._update_mute_icon(muted)

    def _update_mute_icon(self, muted: bool):
        name = "audio-volume-muted" if muted else "audio-volume-medium"
        # This icon is plain monochrome with no meaningful color of its own,
        # so on Omarchy it's safe (and necessary — see tinted_icon) to
        # recolor it to match the theme instead of the system icon theme's
        # fixed, often near-invisible-on-dark-backgrounds gray.
        icon = tinted_icon(name) if is_omarchy() else QIcon.fromTheme(name)
        if icon.isNull():
            self._mute_btn.setText("🔇" if muted else "🔊")
            self._mute_btn.setIcon(QIcon())
        else:
            self._mute_btn.setText("")
            self._mute_btn.setIcon(icon)

    @property
    def volume(self) -> int:
        return self._slider.value()

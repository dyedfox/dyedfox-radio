from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QSlider, QMenu, QToolButton
from PyQt6.QtCore import Qt, QEvent, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import QIcon, QActionGroup, QColor, QPainter, QPen, QPixmap, QPalette

from ui.omarchy_theme import is_omarchy, on_theme_changed, tinted_icon
from ui import strings


class ControlBar(QWidget):
    playback_toggled = pyqtSignal()
    volume_changed = pyqtSignal(int)
    mute_toggled = pyqtSignal()
    output_menu_requested = pyqtSignal()   # menu is about to open; call set_output_devices()
    output_device_selected = pyqtSignal(str)  # "" = system default

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

        # Audio output picker: a small, unobtrusive button whose menu is filled
        # on demand, so hot-plugged devices (USB, Bluetooth) show up each time.
        self._output_btn = QToolButton()
        self._output_btn.setAutoRaise(True)
        self._output_btn.setFixedSize(24, 24)
        self._output_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._output_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self._output_btn.setToolTip(strings.audio_output())
        self._output_menu = QMenu(self._output_btn)
        self._output_menu.aboutToShow.connect(self.output_menu_requested)
        self._output_btn.setMenu(self._output_menu)
        layout.addWidget(self._output_btn)

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
        self._update_output_icon()
        on_theme_changed(lambda: self._update_mute_icon(self._mute_btn.isChecked()))
        on_theme_changed(self._update_output_icon)

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

    def set_output_devices(self, devices: list[tuple[str, str]], current: str):
        """Fill the output menu. devices is [(device_id, display_name)];
        current is the selected device_id ("" = system default)."""
        self._output_menu.clear()
        group = QActionGroup(self._output_menu)
        group.setExclusive(True)

        def add(device_id: str, label: str):
            action = self._output_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(device_id == current)
            action.setActionGroup(group)
            action.triggered.connect(lambda _=False, d=device_id: self.output_device_selected.emit(d))

        add("", strings.system_default())
        self._output_menu.addSeparator()
        for device_id, name in devices:
            add(device_id, name)
        if not devices:
            none = self._output_menu.addAction(self.tr("No other outputs found"))
            none.setEnabled(False)
        if current and current not in {d for d, _ in devices}:
            # Saved device is unplugged: show it so the choice isn't silently lost.
            add(current, strings.unavailable(current))

    def set_output_tooltip(self, name: str):
        self._output_btn.setToolTip(self.tr("Audio output: {0}").format(name))

    def changeEvent(self, event):
        if event.type() == QEvent.Type.PaletteChange:
            self._update_output_icon()
        super().changeEvent(event)

    def _update_output_icon(self):
        # Drawn here instead of taken from the icon theme: theme icons have
        # fixed colors that can vanish against a dark (or light) background,
        # and not every theme has one. Painting it in the palette's button text
        # color keeps it as readable as the button labels in any theme.
        color = self.palette().color(QPalette.ColorRole.ButtonText)
        self._output_btn.setIcon(_headphones_icon(color))
        self._output_btn.setIconSize(self._output_btn.size() * 0.75)


def _headphones_icon(color: QColor, size: int = 18) -> QIcon:
    icon = QIcon()
    for scale in (1, 2, 3):  # crisp on HiDPI screens too
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, size * 0.11)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        # Headband: top half of a circle, ending where the ear cups begin.
        m = size * 0.14
        band = QRectF(m, m, size - 2 * m, size - 2 * m)
        p.drawArc(band, 0, 180 * 16)
        for x in (band.left(), band.right()):
            p.drawLine(QPointF(x, band.center().y()), QPointF(x, size * 0.62))
        # Ear cups.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        cup_w, cup_h = size * 0.26, size * 0.38
        top = size - m * 0.6 - cup_h
        r = size * 0.07
        p.drawRoundedRect(QRectF(m * 0.55, top, cup_w, cup_h), r, r)
        p.drawRoundedRect(QRectF(size - m * 0.55 - cup_w, top, cup_w, cup_h), r, r)
        p.end()
        icon.addPixmap(pixmap)
    return icon

    @property
    def volume(self) -> int:
        return self._slider.value()

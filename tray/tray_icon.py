from pathlib import Path
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtGui import QIcon

from ui.omarchy_theme import is_omarchy

_ICON      = str(Path(__file__).parent.parent / "assets" / "icons" / "dyedfox-radio.png")
_ICON_TRAY = str(Path(__file__).parent.parent / "assets" / "icons" / "dyedfox-radio-tray.svg")


def _tray_icon() -> QIcon:
    # Omarchy's bar recolors tray icons named *-symbolic to its own foreground
    # (live, with theme switches), but only when the icon is sent by name, which
    # Qt does for theme icons. So on Omarchy use the installed line-art icon;
    # if it isn't installed (running from source), or elsewhere, keep the
    # bundled full-color one.
    if is_omarchy():
        icon = QIcon.fromTheme("dyedfox-radio-symbolic")
        if not icon.isNull():
            return icon
    return QIcon(_ICON_TRAY)


class SystemTrayIcon(QSystemTrayIcon):
    def __init__(self, window, backend, parent=None):
        super().__init__(_tray_icon(), parent)
        self._window = window
        self._backend = backend
        self._build_menu()
        self.activated.connect(self._on_activate)
        self.setToolTip("Dyedfox Radio")

        backend.playback_started.connect(lambda: self._set_playing(True))
        backend.playback_stopped.connect(lambda: self._set_playing(False))

    def _build_menu(self):
        menu = QMenu()
        self._action_show = menu.addAction(self.tr("Show / Hide"))
        self._action_show.triggered.connect(self._toggle_window)
        menu.addSeparator()
        self._action_playstop = menu.addAction(self.tr("Play"))
        self._action_playstop.triggered.connect(self._toggle_playback)
        self._action_mute = menu.addAction(self.tr("Mute"))
        self._action_mute.setCheckable(True)
        self._action_mute.triggered.connect(self._toggle_mute)
        menu.addSeparator()
        menu.addAction(self.tr("Quit")).triggered.connect(QApplication.quit)
        self.setContextMenu(menu)

    def _set_playing(self, playing: bool):
        self._action_playstop.setText(self.tr("Stop") if playing else self.tr("Play"))

    def _toggle_playback(self):
        if self._backend.is_playing:
            self._backend.stop()
        else:
            self._backend.play_last()

    def _toggle_mute(self):
        muted = not self._backend.is_muted
        self._backend.set_muted(muted)
        self._action_mute.setChecked(muted)
        if self._window:
            self._window.set_muted(muted)

    def set_muted(self, muted: bool):
        self._action_mute.setChecked(muted)

    def _on_activate(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self._toggle_playback()
        elif reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_window()

    def _toggle_window(self):
        if self._window.isVisible():
            self._window.hide()
        else:
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()

    def update_status(self, station: str, song: str):
        if song:
            self.setToolTip(f"Dyedfox Radio  —  {station}  —  {song}")
        else:
            self.setToolTip(f"Dyedfox Radio  —  {station}")

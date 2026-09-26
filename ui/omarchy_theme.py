import os
import tomllib
from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import QApplication, QProxyStyle, QStyle

_STATE_DIR = Path.home() / ".local" / "state" / "omarchy" / "current"
_COLORS_FILE = _STATE_DIR / "theme" / "colors.toml"

_DEFAULTS = {
    "background": "#2c2525",
    "foreground": "#e6d9db",
    "dark_background": "#211b1b",
    "darker_background": "#181414",
    "lighter_background": "#3d2f2a",
    "muted": "#72696a",
    "light_foreground": "#c3b7b8",
    "accent": "#f38d70",
    "red": "#e0686c",
}

_RELOAD_DEBOUNCE_MS = 400


def is_omarchy() -> bool:
    return bool(os.environ.get("OMARCHY_PATH"))


def _load_colors() -> dict | None:
    try:
        with open(_COLORS_FILE, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _contrast_text(color: QColor) -> QColor:
    """Black or white, whichever reads better on top of `color`."""
    luminance = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255
    return QColor("black") if luminance > 0.5 else QColor("white")


def _build_palette(colors: dict) -> QPalette:
    def c(key: str) -> QColor:
        return QColor(colors.get(key, _DEFAULTS.get(key)))

    background = c("background")
    foreground = c("foreground")
    base = c("dark_background")
    alt_base = c("lighter_background")
    tooltip_base = c("darker_background")
    muted = c("muted")
    light_foreground = c("light_foreground")
    accent = c("accent")
    highlighted_text = _contrast_text(accent)

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, background)
    pal.setColor(QPalette.ColorRole.Button, background)
    pal.setColor(QPalette.ColorRole.WindowText, foreground)
    pal.setColor(QPalette.ColorRole.Text, foreground)
    pal.setColor(QPalette.ColorRole.ButtonText, foreground)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, alt_base)
    pal.setColor(QPalette.ColorRole.ToolTipBase, tooltip_base)
    pal.setColor(QPalette.ColorRole.ToolTipText, foreground)
    # `muted` reads fine as a subtle border/divider tone (below), but as text
    # it's often near-invisible against `background` in stock themes (well
    # under WCAG's 4.5:1 minimum). The app uses PlaceholderText for real
    # secondary content too (station metadata, section headers), not just
    # input hints, so it needs to stay legible.
    pal.setColor(QPalette.ColorRole.PlaceholderText, light_foreground)
    pal.setColor(QPalette.ColorRole.Mid, muted)
    pal.setColor(QPalette.ColorRole.Highlight, accent)
    pal.setColor(QPalette.ColorRole.HighlightedText, highlighted_text)
    pal.setColor(QPalette.ColorRole.Link, accent)

    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        pal.setColor(QPalette.ColorGroup.Disabled, role, muted)

    return pal


class OmarchyThemeManager(QObject):
    theme_changed = pyqtSignal()

    def __init__(self, app: QApplication):
        super().__init__()
        self._app = app
        self._watcher = QFileSystemWatcher([str(_STATE_DIR)])
        self._watcher.directoryChanged.connect(self._on_changed)
        self._reload_timer = QTimer()
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._reload)
        self._reload()

    def _on_changed(self, _path: str):
        self._reload_timer.start(_RELOAD_DEBOUNCE_MS)
        # The watch can be dropped by some filesystems/tools when the watched
        # directory's contents are replaced; re-arm defensively.
        if str(_STATE_DIR) not in self._watcher.directories():
            self._watcher.addPath(str(_STATE_DIR))

    def _reload(self):
        colors = _load_colors()
        if colors is None:
            return
        self.colors = colors
        self._app.setPalette(_build_palette(colors))
        self._refresh_stylesheets()
        self.theme_changed.emit()

    def _refresh_stylesheets(self):
        # Qt resolves `palette(...)` refs inside a widget's own setStyleSheet()
        # string once and caches the result; QApplication.setPalette() alone
        # doesn't invalidate that cache. Re-assigning the same string forces
        # Qt to recompute it against the new palette. Widgets painted directly
        # from QPalette (the vast majority) don't need this and already just
        # repaint on their own.
        for widget in self._app.allWidgets():
            sheet = widget.styleSheet()
            if sheet:
                widget.setStyleSheet(sheet)


class _OmarchyStyle(QProxyStyle):
    """Fusion without stock icons on dialog buttons (OK, Cancel, Close...).
    Omarchy's own UI uses plain text buttons; Breeze on KDE keeps its icons."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons:
            return 0
        return super().styleHint(hint, option, widget, returnData)


_manager: OmarchyThemeManager | None = None


def apply(app: QApplication) -> OmarchyThemeManager | None:
    global _manager
    if not is_omarchy():
        return None
    app.setStyle(_OmarchyStyle("Fusion"))
    _manager = OmarchyThemeManager(app)
    return _manager


def on_theme_changed(slot) -> None:
    """Subscribe to live Omarchy palette reloads; no-op outside Omarchy."""
    if _manager is not None:
        _manager.theme_changed.connect(slot)


def theme_color(key: str) -> QColor:
    """A named color from the active theme's colors.toml (e.g. "red"), falling
    back to _DEFAULTS when the theme doesn't define it."""
    colors = getattr(_manager, "colors", None) or {}
    return QColor(colors.get(key, _DEFAULTS.get(key, "#808080")))


def tinted_icon(name: str, size: QSize = QSize(20, 20)) -> QIcon:
    """QIcon.fromTheme(), recolored to match the current palette's button text.

    Icon-theme assets carry fixed colors baked in for the system's own icon
    theme (often tuned for a light desktop) and don't adapt to an arbitrary
    app palette the way text does — some render as a barely-visible dark
    gray against a dark Omarchy background. Use this instead of
    QIcon.fromTheme() for plain monochrome icons where that's a problem;
    icons that carry meaningful color of their own (e.g. a red delete icon)
    shouldn't be flattened this way.
    """
    icon = QIcon.fromTheme(name)
    if icon.isNull():
        return icon
    color = QApplication.palette().color(QPalette.ColorRole.ButtonText)
    tinted = QPixmap(size)
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, icon.pixmap(size))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), color)
    painter.end()
    return QIcon(tinted)

"""Omarchy-style icons: Nerd Font glyphs drawn in the palette's text color.

Omarchy's own shell draws its icons as Nerd Font glyphs (Material Design Icons)
rather than icon-theme images. On Omarchy, icon() does the same, so the app
matches the shell and follows live theme switches; everywhere else it returns
QIcon.fromTheme(name) exactly as before.

GLYPHS is keyed by the freedesktop icon names the app already asks for, so a
call site only swaps QIcon.fromTheme(name) for glyphs.icon(name).
"""
from functools import lru_cache

from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QIcon, QIconEngine, QPainter, QPainterPath, QPalette, QPixmap
from PyQt6.QtWidgets import QApplication

from ui.omarchy_theme import is_omarchy, theme_color

# Code points are the same in every Nerd Font (nf-md-* names in comments).
GLYPHS: dict[str, int] = {
    "media-playback-start":   0xF040A,  # play
    "media-playback-stop":    0xF04DB,  # stop
    "audio-volume-medium":    0xF057E,  # volume_high
    "audio-volume-muted":     0xF0581,  # volume_off
    "audio-headphones":       0xF02CB,  # headphones
    "emblem-favorite":        0xF02D1,  # heart
    "emblem-favorite-symbolic": 0xF02D5,  # heart_outline
    "network-wireless":       0xF043B,  # radio_tower
    "document-edit":          0xF03EB,  # pencil
    "document-open-recent":   0xF02DA,  # history
    "starred":                0xF0674,  # creation (sparkles)
    "media-playlist-shuffle": 0xF049F,  # shuffle_variant
    "office-chart-bar":       0xF0535,  # trending_up
    "view-process-users":     0xF0849,  # account_group
    "preferences-system":     0xF0493,  # cog
    "help-about":             0xF02FD,  # information_outline
    "go-home":                0xF06A1,  # home_outline
    "web-browser":            0xF059F,  # web
    "edit-copy":              0xF018F,  # content_copy
    "document-save":          0xF0193,  # content_save
    "radio":                  0xF0439,  # radio
    "view-media-album-cover": 0xF0025,  # album
    "edit-delete":            0xF09E7,  # delete_outline
    "edit-clear-history":     0xF05E9,  # delete_sweep
    "list-add":               0xF0415,  # plus
    "audio-x-generic":        0xF075A,  # music
}

# Glyphs sized to the text beside them instead of their own design: Material's
# stop square is shorter than the cap height and floats above the baseline, so
# next to "Stop" it looked misaligned. These are fitted to span exactly from the
# UI font's cap height down to its baseline.
_CAP_FIT = {"media-playback-stop"}


def _font(pixel_size: int) -> QFont:
    # The fontconfig alias, not a concrete family: `omarchy font set` rewrites
    # it, and the Omarchy shell (Commons/Style.qml) binds to it the same way.
    font = QFont("monospace")
    font.setPixelSize(max(1, pixel_size))
    return font


@lru_cache(maxsize=None)
def _has_glyph(code_point: int) -> bool:
    return QFontMetricsF(_font(16)).inFontUcs4(code_point)


class _GlyphEngine(QIconEngine):
    """Paints the glyph at draw time, so the color tracks the current palette
    (live Omarchy theme switches, disabled state) and it's sharp at any scale."""

    def __init__(self, char: str, color_key: str | None, role: QPalette.ColorRole, cap_fit: bool = False):
        super().__init__()
        self._char = char
        self._color_key = color_key
        self._role = role
        self._cap_fit = cap_fit

    def _color(self, mode: QIcon.Mode) -> QColor:
        if mode == QIcon.Mode.Disabled:
            return QApplication.palette().color(QPalette.ColorGroup.Disabled, self._role)
        if self._color_key:
            return theme_color(self._color_key)
        return QApplication.palette().color(QPalette.ColorGroup.Normal, self._role)

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State):
        font = _font(min(rect.width(), rect.height()))
        center = QRectF(rect).center()
        # Exact outline bounds: tightBoundingRect() rounds outward to whole
        # pixels, too coarse to line a small square up with the text.
        path = QPainterPath()
        path.addText(0, 0, font, self._char)
        bounds = path.boundingRect()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._cap_fit:
            self._paint_cap_fit(painter, center, path, bounds, self._color(mode))
        else:
            # Like Omarchy's OpticalGlyph: center the painted bounds
            # horizontally (icon glyphs often sit off-center in their cell) but
            # keep the font's line box vertically, so glyphs don't drift
            # relative to the text baseline. Whole pixels keep edges crisp.
            metrics = QFontMetricsF(font)
            baseline = center.y() + (metrics.ascent() - metrics.descent()) / 2
            origin = QPointF(round(center.x() - bounds.center().x()), round(baseline))
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.setFont(font)
            painter.setPen(self._color(mode))
            painter.drawText(origin, self._char)
        painter.restore()

    @staticmethod
    def _paint_cap_fit(painter: QPainter, center: QPointF, path: QPainterPath, bounds: QRectF, color: QColor):
        # Qt centers a button's text line and its icon on the same middle, so
        # the text's baseline and cap height are known here from the UI font.
        # Snapped to device pixels so straight edges stay crisp.
        text = QFontMetricsF(QApplication.font())
        dpr = painter.device().devicePixelRatioF()

        def snap(v: float) -> float:
            return round(v * dpr) / dpr

        baseline = snap(center.y() + (text.ascent() - text.descent()) / 2)
        k = snap(text.capHeight()) / bounds.height()
        left = snap(center.x() - bounds.width() * k / 2)
        painter.translate(left - bounds.left() * k, baseline - bounds.bottom() * k)
        painter.scale(k, k)
        painter.fillPath(path, color)

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        pixmap = QPixmap(size * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap

    def clone(self) -> "QIconEngine":
        return _GlyphEngine(self._char, self._color_key, self._role, self._cap_fit)


def glyph_icon(
    name: str,
    color_key: str | None = None,
    role: QPalette.ColorRole = QPalette.ColorRole.ButtonText,
) -> QIcon | None:
    """The glyph for `name` on Omarchy, or None when not on Omarchy, the name
    isn't mapped, or the monospace font lacks Nerd Font glyphs.

    color_key picks a fixed theme color (e.g. "red") instead of `role`, for
    icons whose color carries meaning, like a filled favourite heart."""
    if not is_omarchy():
        return None
    code_point = GLYPHS.get(name)
    if code_point is None or not _has_glyph(code_point):
        return None
    return QIcon(_GlyphEngine(chr(code_point), color_key, role, name in _CAP_FIT))


def icon(name: str, color_key: str | None = None) -> QIcon:
    """Drop-in for QIcon.fromTheme(name): a glyph on Omarchy, the icon theme
    everywhere else."""
    glyph = glyph_icon(name, color_key)
    return glyph if glyph is not None else QIcon.fromTheme(name)

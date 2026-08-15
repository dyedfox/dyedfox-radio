import re
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QApplication,
    QDialog, QMenu, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QIcon, QPalette, QPixmap, QDesktopServices

from data.listening_stats import format_duration

_NO_LOGO_PATH = str(Path(__file__).parent.parent / "assets" / "icons" / "no_logo-256x256.png")
_default_logo: QPixmap | None = None


def _sanitize_filename(name: str) -> str:
    """Strip characters that are invalid in filenames on common platforms."""
    name = re.sub(r'[\\/:*?"<>|]', "", name or "").strip()
    return name or "image"


class _ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _ImagePopup(QDialog):
    """Resizable popup showing the active image; the image scales to fit the
    window (never upscaled past its source). Click the image to close."""

    def __init__(self, pix: QPixmap, title: str, default_name: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title or "Dyedfox Radio")
        self._src = pix
        self._default_name = default_name
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = _ClickableLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setMinimumSize(1, 1)  # allow the window to shrink below image size
        self._label.clicked.connect(self.accept)
        layout.addWidget(self._label)

        # Open fitted to ~80% of the available screen, never larger than the source.
        if pix.isNull():
            self.resize(256, 256)
        else:
            w, h = pix.width(), pix.height()
            screen = self.screen() or QApplication.primaryScreen()
            if screen:
                avail = screen.availableGeometry().size()
                if w > avail.width() * 0.8 or h > avail.height() * 0.8:
                    fitted = pix.size().scaled(
                        int(avail.width() * 0.8), int(avail.height() * 0.8),
                        Qt.AspectRatioMode.KeepAspectRatio,
                    )
                    w, h = fitted.width(), fitted.height()
            self.resize(w, h)
        self._render()

    def resizeEvent(self, event):
        self._render()
        super().resizeEvent(event)

    def _render(self):
        if self._src.isNull():
            return
        size = self._label.size()
        target_w = min(size.width(), self._src.width())    # never upscale past source
        target_h = min(size.height(), self._src.height())
        if target_w < 1 or target_h < 1:
            return
        self._label.setPixmap(
            self._src.scaled(
                target_w, target_h,
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
        )

    def contextMenuEvent(self, event):
        if self._src.isNull():
            return
        menu = QMenu(self)
        save = menu.addAction(QIcon.fromTheme("document-save"), self.tr("Save image as…"))
        save.triggered.connect(self._save_image)
        menu.exec(event.globalPos())

    def _save_image(self):
        if self._src.isNull():
            return
        start = str(Path.home() / f"{_sanitize_filename(self._default_name)}.png")
        path, selected = QFileDialog.getSaveFileName(
            self, self.tr("Save image"), start,
            self.tr("PNG image (*.png);;JPEG image (*.jpg *.jpeg)"),
        )
        if not path:
            return
        ext = Path(path).suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg"):
            path += ".jpg" if "jpeg" in selected.lower() else ".png"
            ext = Path(path).suffix.lower()
        fmt = "JPEG" if ext in (".jpg", ".jpeg") else "PNG"
        if not self._src.save(path, fmt):
            QMessageBox.critical(
                self, self.tr("Save image"), self.tr("Could not save the image."),
            )


def _default_logo_pixmap() -> QPixmap:
    """Shared 184×184 fallback logo used when a station has no favicon."""
    global _default_logo
    if _default_logo is None:
        pix = QPixmap(_NO_LOGO_PATH)
        if pix.isNull():
            _default_logo = QIcon.fromTheme("audio-x-generic").pixmap(48, 48)
        else:
            _default_logo = pix.scaled(
                184, 184,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    return _default_logo


class InfoPanel(QWidget):
    favourite_toggled = pyqtSignal(str, bool)  # uuid, new state

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(200)
        self._uuid = ""
        self._station_name = ""
        self._homepage_url = ""
        self._favicon_pix: QPixmap | None = None
        self._art_pix: QPixmap | None = None
        self._art_url = ""
        self._art_enabled = False
        self._prefer_art = False  # per-song view choice: show album art vs favicon

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._logo = _ClickableLabel()
        self._logo.setFixedSize(184, 184)
        self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo.setStyleSheet(
            "QLabel { background: palette(mid); border-radius: 6px; }"
        )
        self._logo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._logo.setToolTip(self.tr("Click to enlarge (right-click the image to save)"))
        self._logo.setPixmap(_default_logo_pixmap())
        self._logo.clicked.connect(self._open_image_popup)
        layout.addWidget(self._logo)

        layout.addSpacing(4)

        self._name = QLabel()
        self._name.setWordWrap(True)
        f = self._name.font()
        f.setBold(True)
        self._name.setFont(f)
        layout.addWidget(self._name)

        self._country = self._muted_label()
        layout.addWidget(self._country)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 2, 0, 0)
        btn_row.setSpacing(2)

        self._copy_name_btn = self._copy_button()
        self._copy_name_btn.setEnabled(False)
        self._copy_name_btn.setToolTip(self.tr("Copy station name"))
        self._copy_name_btn.clicked.connect(lambda: QApplication.clipboard().setText(self._station_name))
        btn_row.addWidget(self._copy_name_btn)

        self._art_toggle_btn = QPushButton()
        self._art_toggle_btn.setFlat(True)
        self._art_toggle_btn.setFixedSize(20, 20)
        self._art_toggle_btn.setCheckable(True)
        self._art_toggle_btn.setEnabled(False)
        self._art_toggle_btn.hide()
        self._art_toggle_btn.toggled.connect(self._on_art_toggled)
        self._update_art_toggle_icon()
        btn_row.addWidget(self._art_toggle_btn)

        self._homepage_btn = QPushButton()
        self._homepage_btn.setFlat(True)
        self._homepage_btn.setFixedSize(20, 20)
        self._homepage_btn.setToolTip(self.tr("Open station website"))
        self._homepage_btn.setEnabled(False)
        self._homepage_btn.hide()
        _home_icon = QIcon.fromTheme("go-home")
        if _home_icon.isNull():
            self._homepage_btn.setText("🏠")
        else:
            self._homepage_btn.setIcon(_home_icon)
        self._homepage_btn.clicked.connect(self._open_homepage)
        btn_row.addWidget(self._homepage_btn)

        self._info_btn = QPushButton()
        self._info_btn.setFlat(True)
        self._info_btn.setFixedSize(20, 20)
        self._info_btn.setToolTip(self.tr("Open on radio-browser.info"))
        self._info_btn.setEnabled(False)
        self._info_btn.hide()
        _info_icon = QIcon.fromTheme("web-browser")
        if _info_icon.isNull():
            self._info_btn.setText("↗")
        else:
            self._info_btn.setIcon(_info_icon)
        self._info_btn.clicked.connect(self._open_info_url)
        btn_row.addWidget(self._info_btn)

        self._fav_btn = QPushButton()
        self._fav_btn.setFlat(True)
        self._fav_btn.setFixedSize(24, 24)
        self._fav_btn.setCheckable(True)
        self._fav_btn.setEnabled(False)
        self._update_heart(False)
        self._fav_btn.toggled.connect(self._on_fav_toggled)
        btn_row.addWidget(self._fav_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        self._now_playing_text = ""
        now_row = QHBoxLayout()
        now_row.setContentsMargins(0, 0, 0, 0)
        now_row.setSpacing(4)
        self._now_playing = QLabel()
        self._now_playing.setWordWrap(True)
        now_row.addWidget(self._now_playing, 1)
        self._copy_song_btn = self._copy_button()
        self._copy_song_btn.setToolTip(self.tr("Copy song info"))
        self._copy_song_btn.clicked.connect(lambda: QApplication.clipboard().setText(self._now_playing_text))
        now_row.addWidget(self._copy_song_btn, 0, Qt.AlignmentFlag.AlignTop)
        self._now_playing_row = QWidget()
        self._now_playing_row.setLayout(now_row)
        self._now_playing_row.hide()
        layout.addWidget(self._now_playing_row)

        self._bitrate = self._muted_label()
        layout.addWidget(self._bitrate)

        self._language = self._muted_label()
        layout.addWidget(self._language)

        self._tags = self._muted_label()
        self._tags.setWordWrap(True)
        layout.addWidget(self._tags)

        self._votes = self._muted_label()
        layout.addWidget(self._votes)

        self._listening_time = self._muted_label()
        layout.addWidget(self._listening_time)

        layout.addStretch()

    def set_station(self, station: dict, is_favourite: bool = False):
        self._uuid = station.get("stationuuid", "")
        self._station_name = station.get("name", "").strip()
        self._homepage_url = station.get("homepage", "")
        self._name.setText(self._station_name)
        self._country.setText(station.get("country", ""))
        is_custom = bool(station.get("custom"))
        self._fav_btn.setVisible(not is_custom)
        self._fav_btn.setEnabled(not is_custom)
        has_homepage = bool(self._homepage_url) and not is_custom
        self._homepage_btn.setVisible(has_homepage)
        self._homepage_btn.setEnabled(has_homepage)
        self._info_btn.setVisible(not is_custom)
        self._info_btn.setEnabled(not is_custom)
        self._copy_name_btn.setEnabled(True)
        if not is_custom:
            self.set_favourite(is_favourite)

        bitrate = station.get("bitrate", 0)
        codec = station.get("codec", "")
        parts = [p for p in [codec, f"{bitrate} kbps" if bitrate else ""] if p]
        self._bitrate.setText("  ·  ".join(parts))

        self._language.setText(station.get("language", ""))

        raw_tags = station.get("tags", "")
        tag_list = [t.strip() for t in raw_tags.split(",") if t.strip()]
        self._tags.setText(", ".join(tag_list[:8]))

        votes = station.get("votes", 0)
        self._votes.setText(f"▲ {votes:,}" if votes else "")

        self._now_playing_row.hide()
        self._now_playing.clear()
        self._now_playing_text = ""

        # Set by the caller right after via set_listening_time(); this just
        # clears the previous station's figure so it doesn't flash stale data.
        self._listening_time.clear()

        # Reset artwork state for the new station; art (if any) arrives later.
        self._favicon_pix = None
        self._art_pix = None
        self._art_url = ""
        self._prefer_art = self._art_enabled
        self._art_toggle_btn.blockSignals(True)
        self._art_toggle_btn.setChecked(self._prefer_art)
        self._art_toggle_btn.blockSignals(False)
        self._art_toggle_btn.setEnabled(False)
        self._art_toggle_btn.setVisible(self._art_enabled)
        self._update_art_toggle_icon()
        self._logo.setPixmap(_default_logo_pixmap())

    def set_album_art_enabled(self, enabled: bool):
        self._art_enabled = enabled

    def set_favicon(self, data: bytes):
        pix = QPixmap()
        pix.loadFromData(data)
        if pix.isNull():
            return
        self._favicon_pix = pix
        self._apply_logo()

    def set_album_art(self, data: bytes, art_url: str):
        pix = QPixmap()
        pix.loadFromData(data)
        if pix.isNull():
            return
        self._art_pix = pix
        self._art_url = art_url
        self._art_toggle_btn.setVisible(self._art_enabled)
        self._art_toggle_btn.setEnabled(self._art_enabled)
        self._apply_logo()
        self._update_art_toggle_icon()

    def clear_album_art(self):
        """Drop any fetched cover art and revert to the favicon (e.g. on stop)."""
        self._art_pix = None
        self._art_url = ""
        self._art_toggle_btn.setEnabled(False)
        self._apply_logo()
        self._update_art_toggle_icon()

    def _apply_logo(self):
        if self._prefer_art and self._art_pix is not None:
            pix = self._art_pix
        elif self._favicon_pix is not None:
            pix = self._favicon_pix
        else:
            self._logo.setPixmap(_default_logo_pixmap())
            return
        self._logo.setPixmap(
            pix.scaled(184, 184, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )

    def _on_art_toggled(self, checked: bool):
        self._prefer_art = checked
        self._apply_logo()
        self._update_art_toggle_icon()

    def _update_art_toggle_icon(self):
        showing_art = self._prefer_art and self._art_pix is not None
        if showing_art:
            icon = QIcon.fromTheme("view-media-album-cover")
            fallback, tip = "🖼", self.tr("Show station logo")
        else:
            icon = QIcon.fromTheme("radio")
            if icon.isNull():
                icon = QIcon.fromTheme("image-x-generic")
            fallback, tip = "📻", self.tr("Show album art")
        if icon.isNull():
            self._art_toggle_btn.setText(fallback)
            self._art_toggle_btn.setIcon(QIcon())
        else:
            self._art_toggle_btn.setText("")
            self._art_toggle_btn.setIcon(icon)
        self._art_toggle_btn.setToolTip(tip)

    def _open_image_popup(self):
        if self._prefer_art and self._art_pix is not None:
            pix = self._art_pix
            default_name = self._now_playing_text or self._station_name
        elif self._favicon_pix is not None:
            pix = self._favicon_pix
            default_name = self._station_name
        else:
            pix = _default_logo_pixmap()
            default_name = self._station_name
        _ImagePopup(pix, self._station_name, default_name, self).exec()

    def set_now_playing(self, title: str):
        if title:
            self._now_playing_text = title
            self._now_playing.setText(title)
            self._now_playing_row.show()
        else:
            self._now_playing_row.hide()
            self._now_playing_text = ""

    def set_listening_time(self, seconds: float):
        self._listening_time.setText(
            self.tr("Listened: {0}").format(format_duration(seconds)) if seconds else ""
        )

    def set_favourite(self, is_fav: bool):
        self._fav_btn.blockSignals(True)
        self._fav_btn.setChecked(is_fav)
        self._fav_btn.blockSignals(False)
        self._update_heart(is_fav)

    def _open_homepage(self):
        if self._homepage_url:
            QDesktopServices.openUrl(QUrl(self._homepage_url))

    def _open_info_url(self):
        if self._uuid:
            QDesktopServices.openUrl(QUrl(f"https://www.radio-browser.info/history/{self._uuid}"))

    def _on_fav_toggled(self, checked: bool):
        self._update_heart(checked)
        if self._uuid:
            self.favourite_toggled.emit(self._uuid, checked)

    def _update_heart(self, is_fav: bool):
        icon = QIcon.fromTheme("emblem-favorite" if is_fav else "emblem-favorite-symbolic")
        if icon.isNull():
            self._fav_btn.setText("♥" if is_fav else "♡")
            self._fav_btn.setIcon(QIcon())
        else:
            self._fav_btn.setText("")
            self._fav_btn.setIcon(icon)

    @staticmethod
    def _copy_button() -> QPushButton:
        btn = QPushButton()
        btn.setFlat(True)
        btn.setFixedSize(20, 20)
        icon = QIcon.fromTheme("edit-copy")
        if icon.isNull():
            btn.setText("⧉")
        else:
            btn.setIcon(icon)
        return btn

    @staticmethod
    def _muted_label() -> QLabel:
        label = QLabel()
        p = label.palette()
        p.setColor(
            QPalette.ColorRole.WindowText,
            label.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText),
        )
        label.setPalette(p)
        return label

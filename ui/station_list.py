from __future__ import annotations
import math
from pathlib import Path
import requests
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLineEdit, QPushButton, QLabel, QSizePolicy, QApplication, QComboBox,
    QStyle, QStyleOption, QMenu, QInputDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QSize, QTimer, QRunnable, QObject, pyqtSignal, QThreadPool, QEvent
from PyQt6.QtGui import QIcon, QPalette, QPixmap, QPainter, QColor, QBrush

from data.favourites import FavouritesManager
from data.listening_stats import ListeningStatsManager, format_duration
from data.settings import Settings

_NO_LOGO_PATH = str(Path(__file__).parent.parent / "assets" / "icons" / "no_logo-256x256.png")
_default_favicon: QPixmap | None = None


def _default_favicon_pixmap() -> QPixmap:
    """Shared 30×30 fallback icon used when a station has no favicon."""
    global _default_favicon
    if _default_favicon is None:
        pix = QPixmap(_NO_LOGO_PATH)
        if pix.isNull():
            _default_favicon = QIcon.fromTheme("audio-x-generic").pixmap(28, 28)
        else:
            _default_favicon = pix.scaled(
                30, 30,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    return _default_favicon


class _SpinnerLabel(QLabel):
    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self.hide()

    def start(self):
        self._frame = 0
        self.setText(self._FRAMES[0])
        self.show()
        self._timer.start(80)

    def stop(self):
        self._timer.stop()
        self.hide()

    def _step(self):
        self._frame = (self._frame + 1) % len(self._FRAMES)
        self.setText(self._FRAMES[self._frame])


class _WaveWidget(QWidget):
    """Animated 3-bar equalizer shown while a station is playing."""

    _BAR_PHASES = [0.0, 1.3, 2.6]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self._tick = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def start(self):
        self._timer.start(60)
        self.show()

    def freeze(self):
        self._timer.stop()

    def show_frozen(self):
        self._timer.stop()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _step(self):
        self._tick += 1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(QPalette.ColorRole.Highlight))

        w, h = self.width(), self.height()
        bar_w, gap, n = 4, 3, 3
        x0 = (w - (n * bar_w + (n - 1) * gap)) // 2
        t = self._tick * 0.22

        for i in range(n):
            amp = 0.5 + 0.5 * math.sin(t + self._BAR_PHASES[i])
            bar_h = max(4, int(4 + amp * (h - 10)))
            x = x0 + i * (bar_w + gap)
            y = (h - bar_h) // 2
            painter.drawRoundedRect(x, y, bar_w, bar_h, 2, 2)

        painter.end()


class _ElidedLabel(QLabel):
    """QLabel that truncates text with … instead of forcing the widget wider.
    Emits elision_changed whenever it starts or stops being truncated, so a
    parent can surface the full text (e.g. as a tooltip) only when it's clipped."""

    elision_changed = pyqtSignal()

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = text
        self._is_elided = False
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        super().setText(text)

    def setText(self, text: str):
        self._full_text = text
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def is_elided(self) -> bool:
        return self._is_elided

    def _elide(self):
        w = self.width()
        if w <= 0:
            super().setText(self._full_text)
            elided = False
        else:
            shown = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, w)
            super().setText(shown)
            elided = shown != self._full_text
        if elided != self._is_elided:
            self._is_elided = elided
            self.elision_changed.emit()


class _FaviconSignals(QObject):
    loaded = pyqtSignal(str, bytes)  # uuid, raw bytes
    finished = pyqtSignal(object)    # the loader itself, for keep-alive bookkeeping


class _FaviconLoader(QRunnable):
    def __init__(self, uuid: str, url: str):
        super().__init__()
        self.setAutoDelete(False)
        self._uuid = uuid
        self._url = url
        self.signals = _FaviconSignals()

    def run(self):
        try:
            resp = requests.get(self._url, timeout=2, headers={"User-Agent": "dyedfox-radio/1.0"})
            if resp.ok and resp.content:
                try:
                    self.signals.loaded.emit(self._uuid, resp.content)
                except RuntimeError:
                    pass
        except Exception:
            pass
        finally:
            # autoDelete is False, so Python owns this QRunnable. The owner must
            # keep a reference until run() has fully returned — dropping it while
            # the pool still holds the runnable causes a use-after-free (SIGSEGV).
            # Signal completion so the owner can release its reference safely.
            try:
                self.signals.finished.emit(self)
            except RuntimeError:
                pass


class StationRowWidget(QWidget):
    play_requested = pyqtSignal(dict)
    favourite_toggled = pyqtSignal(str, bool)  # uuid, new state
    label_toggled = pyqtSignal(str, str, bool)  # uuid, label, new state
    reset_listening_requested = pyqtSignal(str)  # uuid

    def __init__(self, station: dict, favourites: FavouritesManager, parent=None, on_delete=None, on_edit=None, on_remove=None, listening_seconds: float = 0, listening_stats: ListeningStatsManager | None = None):
        super().__init__(parent)
        self._station = station
        self._favourites = favourites
        self._listening_stats = listening_stats
        uuid = station.get("stationuuid", "")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        icon_box = QWidget()
        icon_box.setFixedSize(30, 30)
        self._favicon = QLabel(icon_box)
        self._favicon.setFixedSize(30, 30)
        self._favicon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._favicon.setPixmap(_default_favicon_pixmap())
        self._wave = _WaveWidget(icon_box)
        self._wave.hide()
        layout.addWidget(icon_box)

        text = QWidget()
        text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_layout = QVBoxLayout(text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        name_label = _ElidedLabel(station.get("name", "").strip())
        f = name_label.font()
        f.setBold(True)
        name_label.setFont(f)
        name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_layout.addWidget(name_label)
        self._name_label = name_label

        tags = [t.strip() for t in station.get("tags", "").split(",") if t.strip()]
        first_tag = tags[0] if tags else ""
        country = station.get("country", "")
        codec = station.get("codec", "")
        bitrate = station.get("bitrate", 0)
        votes = station.get("votes", 0)
        votes_str = f"{votes // 1000}k ♥" if votes >= 1000 else (f"{votes} ♥" if votes else "")
        # Everything except the listening figure is fixed for the row; keep the
        # base parts so the "X listened" tail can be refreshed in place (e.g.
        # after a reset) without rebuilding the whole row.
        self._meta_base = [country, first_tag, codec, f"{bitrate} kbps" if bitrate else "", votes_str]
        meta_label = _ElidedLabel("")
        meta_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        meta_label.setStyleSheet("color: palette(placeholder-text);")
        text_layout.addWidget(meta_label)
        self._meta_label = meta_label

        # The labels are transparent to the mouse (clicks fall through to the row
        # to start playback), so hover tooltips must live on the row itself. Show
        # the full text only for whichever line is actually truncated, re-checked
        # whenever either label re-elides on resize.
        name_label.elision_changed.connect(self._update_overflow_tooltip)
        meta_label.elision_changed.connect(self._update_overflow_tooltip)
        self.set_listening_seconds(listening_seconds)
        self._update_overflow_tooltip()

        layout.addWidget(text, 1)

        if on_delete is not None:
            self._heart_btn = None
            if on_edit is not None:
                edit_btn = QPushButton()
                edit_btn.setFlat(True)
                edit_btn.setFixedSize(24, 24)
                edit_icon = QIcon.fromTheme("document-edit")
                if edit_icon.isNull():
                    edit_btn.setText("✎")
                else:
                    edit_btn.setIcon(edit_icon)
                edit_btn.clicked.connect(lambda: on_edit(uuid))
                layout.addWidget(edit_btn)
            del_btn = QPushButton()
            del_btn.setFlat(True)
            del_btn.setFixedSize(24, 24)
            del_btn.setToolTip(self.tr("Delete station"))
            icon = QIcon.fromTheme("edit-delete")
            if icon.isNull():
                del_btn.setText("✕")
            else:
                del_btn.setIcon(icon)
            del_btn.clicked.connect(lambda: on_delete(uuid))
            layout.addWidget(del_btn)
        else:
            self._heart_btn = QPushButton()
            self._heart_btn.setFlat(True)
            self._heart_btn.setFixedSize(24, 24)
            self._heart_btn.setCheckable(True)
            is_fav = favourites.is_favourite(uuid)
            self._heart_btn.setChecked(is_fav)
            self._update_heart(is_fav)
            self._heart_btn.toggled.connect(lambda checked, u=uuid: self._on_heart(u, checked))
            layout.addWidget(self._heart_btn)

            # History rows keep the heart but add a remove-from-history button
            # to the right of it (these are real stations, unlike Custom rows).
            if on_remove is not None:
                remove_btn = QPushButton()
                remove_btn.setFlat(True)
                remove_btn.setFixedSize(24, 24)
                remove_btn.setToolTip(self.tr("Remove from history"))
                rm_icon = QIcon.fromTheme("edit-delete")
                if rm_icon.isNull():
                    remove_btn.setText("✕")
                else:
                    remove_btn.setIcon(rm_icon)
                remove_btn.clicked.connect(lambda: on_remove(uuid))
                layout.addWidget(remove_btn)

        # Right-click any row for its context menu: label management on
        # favouritable rows (Custom stations can't be labelled) plus a
        # "Reset time listened" entry available on every station.
        self._can_label = on_delete is None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def set_listening_seconds(self, seconds: float):
        listened = self.tr("{0} listened").format(format_duration(seconds)) if seconds else ""
        parts = [p for p in self._meta_base + [listened] if p]
        self._meta_label.setText("  ·  ".join(parts))
        # The meta content changed; refresh the tooltip even if the elided/not
        # state didn't flip (elision_changed wouldn't fire in that case).
        self._update_overflow_tooltip()

    def _update_overflow_tooltip(self):
        parts = []
        if self._name_label.is_elided():
            parts.append(self._name_label._full_text)
        if self._meta_label.is_elided():
            parts.append(self._meta_label._full_text)
        self.setToolTip("\n".join(parts))

    def set_favicon(self, data: bytes):
        pix = QPixmap()
        pix.loadFromData(data)
        if not pix.isNull():
            self._favicon.setPixmap(
                pix.scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )

    def set_playing(self, playing: bool):
        if playing:
            self._favicon.hide()
            self._wave.start()
        else:
            self._wave.stop()
            self._favicon.show()

    def freeze_wave(self):
        self._favicon.hide()
        self._wave.show_frozen()

    def update_favourite(self, is_fav: bool):
        if self._heart_btn is None:
            return
        self._heart_btn.blockSignals(True)
        self._heart_btn.setChecked(is_fav)
        self._heart_btn.blockSignals(False)
        self._update_heart(is_fav)

    def _update_heart(self, is_fav: bool):
        if self._heart_btn is None:
            return
        icon = QIcon.fromTheme("emblem-favorite" if is_fav else "emblem-favorite-symbolic")
        if icon.isNull():
            self._heart_btn.setText("♥" if is_fav else "♡")
            self._heart_btn.setIcon(QIcon())
        else:
            self._heart_btn.setText("")
            self._heart_btn.setIcon(icon)

    def _on_heart(self, uuid: str, checked: bool):
        self._update_heart(checked)
        self.favourite_toggled.emit(uuid, checked)

    def _show_context_menu(self, pos):
        uuid = self._station.get("stationuuid", "")
        menu = QMenu(self)

        if self._can_label:
            if not self._favourites.is_favourite(uuid):
                action = menu.addAction(self.tr("Favourite this station to add labels"))
                action.setEnabled(False)
            else:
                current = set(self._favourites.labels_for(uuid))
                all_labels = self._favourites.all_labels()
                if not all_labels:
                    action = menu.addAction(self.tr("No labels yet"))
                    action.setEnabled(False)
                for label in all_labels:
                    action = menu.addAction(label)
                    action.setCheckable(True)
                    action.setChecked(label in current)
                    action.toggled.connect(lambda checked, l=label: self.label_toggled.emit(uuid, l, checked))
                new_action = menu.addAction(self.tr("New label…"))
                new_action.triggered.connect(lambda: self._create_label(uuid))
            menu.addSeparator()

        reset_action = menu.addAction(self.tr("Reset time listened"))
        has_time = self._listening_stats is not None and self._listening_stats.total_seconds(uuid) > 0
        reset_action.setEnabled(has_time)
        reset_action.triggered.connect(lambda: self._reset_listening(uuid))

        menu.exec(self.mapToGlobal(pos))

    def _reset_listening(self, uuid: str):
        name = self._station.get("name", "").strip()
        confirm = QMessageBox.question(
            self,
            self.tr("Reset time listened"),
            self.tr("Reset the listening time for “{0}”?").format(name),
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.reset_listening_requested.emit(uuid)

    def _create_label(self, uuid: str):
        text, ok = QInputDialog.getText(self, self.tr("New label"), self.tr("Label name:"))
        text = text.strip()
        if ok and text:
            self.label_toggled.emit(uuid, text, True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.play_requested.emit(self._station)
        super().mousePressEvent(event)


class _LimitOverlay(QWidget):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "background: palette(window); border: 1px solid palette(mid); border-radius: 6px;"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 10, 5)
        lbl = QLabel(text)
        lbl.setStyleSheet("border: none; background: transparent; color: palette(placeholder-text);")
        row.addWidget(lbl)

    def paintEvent(self, event):
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)


class StationListWidget(QWidget):
    station_play_requested = pyqtSignal(dict)
    favourite_toggled = pyqtSignal(str, bool)
    label_toggled = pyqtSignal(str, str, bool)  # uuid, label, new state
    search_params_changed = pyqtSignal(str, str, str, str)  # name, country, tag, language
    station_delete_requested = pyqtSignal(str)
    station_edit_requested = pyqtSignal(str)
    station_remove_requested = pyqtSignal(str)  # remove a single station from history
    reset_listening_requested = pyqtSignal(str)  # forget listening time for one station
    clear_history_requested = pyqtSignal()
    add_station_requested = pyqtSignal()
    playing_visibility_changed = pyqtSignal(bool)

    def __init__(self, favourites: FavouritesManager, settings: Settings, listening_stats: ListeningStatsManager, parent=None):
        super().__init__(parent)
        self._favourites = favourites
        self._settings = settings
        self._listening_stats = listening_stats
        self._current_view = "all"
        self._stations_raw: list[dict] = []
        self._deletable: bool = False
        self._limit_reached: bool = False
        self._fav_uuids: set[str] = set()
        self._recent_uuids: list[str] = []
        self._label_filter: str | None = None
        self._row_widgets: dict[str, StationRowWidget] = {}
        self._item_map: dict[str, QListWidgetItem] = {}
        self._playing_uuid: str | None = None
        self._is_playing: bool = False
        self._favicon_cache: dict[str, bytes] = {}
        self._pool = QThreadPool.globalInstance()
        # Keep-alive set for in-flight favicon loaders (autoDelete=False). A loader
        # stays here until its run() finishes or we successfully cancel it before
        # it starts; otherwise the pool could dereference a freed QRunnable.
        self._active_loaders: set[_FaviconLoader] = set()
        self._build_gen: int = 0
        self._built_uuids: list[str] = []
        self._built_deletable: bool = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        search_bar = QWidget()
        search_layout = QHBoxLayout(search_bar)
        search_layout.setContentsMargins(8, 4, 8, 4)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(self.tr("Search stations…"))
        self._search_input.setClearButtonEnabled(True)
        search_layout.addWidget(self._search_input)

        # Loading spinner lives in the always-visible search bar (not the sort
        # bar, which is hidden for the curated lists) so every view shows it.
        self._spinner = _SpinnerLabel()
        self._spinner.setStyleSheet("color: palette(placeholder-text);")
        search_layout.addWidget(self._spinner)

        # Station count, also in the search bar so it shows for every view.
        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_label.setStyleSheet("color: palette(placeholder-text);")
        search_layout.addWidget(self._status_label)

        # Shown only in the History view (see set_view); clears all recent items.
        self._clear_history_btn = QPushButton(self.tr("Clear history"))
        self._clear_history_btn.setToolTip(self.tr("Remove all stations from history"))
        clear_icon = QIcon.fromTheme("edit-clear-history")
        if not clear_icon.isNull():
            self._clear_history_btn.setIcon(clear_icon)
        self._clear_history_btn.clicked.connect(self.clear_history_requested)
        self._clear_history_btn.hide()
        search_layout.addWidget(self._clear_history_btn)

        # Shown only in the Custom view (see set_view); opens the add-station dialog.
        self._add_station_btn = QPushButton(self.tr("+ Add station"))
        self._add_station_btn.setToolTip(self.tr("Add a custom station"))
        add_icon = QIcon.fromTheme("list-add")
        if not add_icon.isNull():
            self._add_station_btn.setIcon(add_icon)
        self._add_station_btn.clicked.connect(self.add_station_requested)
        self._add_station_btn.hide()
        search_layout.addWidget(self._add_station_btn)

        layout.addWidget(search_bar)

        self._sort_bar = QWidget()
        sort_layout = QHBoxLayout(self._sort_bar)
        sort_layout.setContentsMargins(8, 0, 8, 4)
        sort_layout.setSpacing(6)

        self._sort_field = QComboBox()
        for label, key in [
            (self.tr("Name"),     "name"),
            (self.tr("Country"),  "country"),
            (self.tr("Bitrate"),  "bitrate"),
            (self.tr("Votes"),    "votes"),
            (self.tr("Language"), "language"),
            (self.tr("Codec"),    "codec"),
        ]:
            self._sort_field.addItem(label, key)

        self._country_input = QLineEdit()
        self._country_input.setPlaceholderText(self.tr("Country…"))
        self._country_input.setClearButtonEnabled(True)

        self._tag_input = QLineEdit()
        self._tag_input.setPlaceholderText(self.tr("Genre / tag…"))
        self._tag_input.setClearButtonEnabled(True)

        self._language_input = QLineEdit()
        self._language_input.setPlaceholderText(self.tr("Language…"))
        self._language_input.setClearButtonEnabled(True)

        self._sort_descending: bool = False
        self._sort_dir = QPushButton("↑")
        self._sort_dir.setFlat(True)
        self._sort_dir.setFixedWidth(28)
        self._sort_dir.setToolTip(self.tr("Toggle sort direction"))

        sort_layout.addWidget(self._sort_field)
        sort_layout.addWidget(self._sort_dir)
        sort_layout.addWidget(self._country_input, 1)
        sort_layout.addWidget(self._tag_input, 1)
        sort_layout.addWidget(self._language_input, 1)

        layout.addWidget(self._sort_bar)

        self._list = QListWidget()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setSpacing(0)
        layout.addWidget(self._list)

        self._limit_overlay = _LimitOverlay(
            self.tr("Result limit reached · narrow your search to see more"),
            self,
        )
        self._limit_overlay.hide()

        self._error_widget = QWidget()
        self._error_widget.hide()
        error_layout = QVBoxLayout(self._error_widget)
        error_layout.setSpacing(8)

        self._error_label = QLabel()
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setWordWrap(True)
        error_layout.addWidget(self._error_label)

        self._error_hint = QLabel(self.tr("This may be a temporary server-side issue."))
        self._error_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_hint.setEnabled(False)
        error_layout.addWidget(self._error_hint)

        self._retry_callback = None
        self._retry_btn = QPushButton(self.tr("Retry"))
        self._retry_btn.setFixedWidth(80)
        self._retry_btn.clicked.connect(lambda: self._retry_callback and self._retry_callback())
        self._retry_btn.hide()
        retry_row = QWidget()
        retry_row_layout = QHBoxLayout(retry_row)
        retry_row_layout.setContentsMargins(0, 0, 0, 0)
        retry_row_layout.addStretch()
        retry_row_layout.addWidget(self._retry_btn)
        retry_row_layout.addStretch()
        error_layout.addWidget(retry_row)

        layout.addWidget(self._error_widget)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._on_search_debounced)
        self._search_input.textChanged.connect(lambda: self._search_timer.start(350))

        self._sort_field.currentIndexChanged.connect(self._on_sort_changed)
        self._sort_dir.clicked.connect(self._on_sort_dir_clicked)
        self._country_input.textChanged.connect(lambda: self._search_timer.start(350))
        self._tag_input.textChanged.connect(lambda: self._search_timer.start(350))
        self._language_input.textChanged.connect(lambda: self._search_timer.start(350))

        self._load_sort_controls("all")

    def _highlight_brush(self) -> QBrush:
        pal = QApplication.palette()
        h = pal.color(QPalette.ColorRole.Highlight)
        b = pal.color(QPalette.ColorRole.Base)
        t = 0.20
        return QBrush(QColor(
            int(b.red()   * (1 - t) + h.red()   * t),
            int(b.green() * (1 - t) + h.green() * t),
            int(b.blue()  * (1 - t) + h.blue()  * t),
        ))

    def _set_item_highlight(self, uuid: str, on: bool):
        if uuid in self._item_map:
            if on:
                brush = self._highlight_brush()
                self._item_map[uuid].setBackground(brush)
                color = brush.color().name()
                self._list.setStyleSheet(
                    f"QListWidget::item:selected {{ background-color: {color}; }}"
                )
            else:
                self._item_map[uuid].setBackground(QBrush())
                self._list.setStyleSheet("")

    def set_stations(self, stations: list[dict], deletable: bool = False, limit_reached: bool = False):
        self._stations_raw = list(stations)
        self._deletable = deletable
        self._limit_reached = limit_reached

        new_sorted_uuids = [s.get("stationuuid", "") for s in self._sorted_stations()]
        if new_sorted_uuids == self._built_uuids and deletable == self._built_deletable:
            self._error_widget.hide()
            self._list.show()
            self._apply_filter()
            return

        self._rebuild()

    _BATCH_SIZE = 40

    def _cancel_loaders(self):
        # Cancel queued favicon loaders. tryTake removes those not yet started, so
        # they will never run and are safe to release. Loaders already running keep
        # their reference until their finished signal fires — releasing them now
        # would let the pool touch freed memory (SIGSEGV).
        for loader in list(self._active_loaders):
            if self._pool.tryTake(loader):
                self._active_loaders.discard(loader)

    def _on_loader_finished(self, loader):
        self._active_loaders.discard(loader)

    def _rebuild(self):
        self._cancel_loaders()

        sorted_stations = self._sorted_stations()
        self._built_uuids = [s.get("stationuuid", "") for s in sorted_stations]
        self._built_deletable = self._deletable
        self._build_gen += 1
        gen = self._build_gen

        self._error_widget.hide()
        self._list.show()
        self._list.setUpdatesEnabled(False)
        self._list.clear()
        self._list.setUpdatesEnabled(True)
        self._row_widgets.clear()
        self._item_map.clear()

        pending = list(sorted_stations)

        def _next_batch():
            if self._build_gen != gen:
                return
            batch, pending[:] = pending[:self._BATCH_SIZE], pending[self._BATCH_SIZE:]
            for station in batch:
                self._build_row(station)
            if pending:
                QTimer.singleShot(0, _next_batch)
            else:
                self._finish_rebuild()

        _next_batch()

    def _build_row(self, station: dict):
        uuid = station.get("stationuuid", "")
        item = QListWidgetItem(self._list)
        item.setSizeHint(QSize(0, 56))
        item.setData(Qt.ItemDataRole.UserRole, station)

        on_delete = (lambda u: self.station_delete_requested.emit(u)) if self._deletable else None
        on_edit = (lambda u: self.station_edit_requested.emit(u)) if self._deletable else None
        on_remove = (lambda u: self.station_remove_requested.emit(u)) if self._current_view == "recent" else None
        listening_seconds = self._listening_stats.total_seconds(uuid)
        row = StationRowWidget(
            station, self._favourites, on_delete=on_delete, on_edit=on_edit, on_remove=on_remove,
            listening_seconds=listening_seconds, listening_stats=self._listening_stats,
        )
        self._list.setItemWidget(item, row)
        self._row_widgets[uuid] = row
        self._item_map[uuid] = item

        row.play_requested.connect(lambda s=station, i=item: self._on_row_play(s, i))
        row.reset_listening_requested.connect(self.reset_listening_requested)
        if not self._deletable:
            row.favourite_toggled.connect(self.favourite_toggled)
            row.label_toggled.connect(self.label_toggled)

        favicon_url = station.get("favicon", "")
        if favicon_url:
            if uuid in self._favicon_cache:
                row.set_favicon(self._favicon_cache[uuid])
            else:
                loader = _FaviconLoader(uuid, favicon_url)
                loader.signals.loaded.connect(self._on_favicon_loaded)
                loader.signals.finished.connect(self._on_loader_finished)
                self._active_loaders.add(loader)
                self._pool.start(loader)

    def _finish_rebuild(self):
        if self._playing_uuid and self._playing_uuid in self._row_widgets:
            self._set_item_highlight(self._playing_uuid, True)
            if self._is_playing:
                self._row_widgets[self._playing_uuid].set_playing(True)
            else:
                self._row_widgets[self._playing_uuid].freeze_wave()
        self._apply_filter()

    def _sorted_stations(self) -> list[dict]:
        # No-sort views arrive in a meaningful server/raw order (recency for
        # recent & now_listening, trend rank for trending, recency for new,
        # shuffled for random) and have no sort bar — keep that order as-is.
        if self._current_view in self._NO_SORT_VIEWS:
            return list(self._stations_raw)
        prefs = self._settings["sort"].get(self._current_view, {"field": "name", "ascending": True})
        field = prefs["field"]
        ascending = prefs["ascending"]

        def sort_key(s: dict):
            v = s.get(field, "")
            return v.strip().lower() if isinstance(v, str) else (v or 0)

        return sorted(self._stations_raw, key=sort_key, reverse=not ascending)

    def _load_sort_controls(self, view: str):
        prefs = self._settings["sort"].get(view, {"field": "name", "ascending": True})
        keys = [self._sort_field.itemData(i) for i in range(self._sort_field.count())]
        field_idx = keys.index(prefs["field"]) if prefs["field"] in keys else 0
        self._sort_descending = not prefs["ascending"]
        self._sort_field.blockSignals(True)
        self._sort_field.setCurrentIndex(field_idx)
        self._sort_field.blockSignals(False)
        self._sort_dir.setText("↓" if self._sort_descending else "↑")

    def _on_sort_dir_clicked(self):
        self._sort_descending = not self._sort_descending
        self._sort_dir.setText("↓" if self._sort_descending else "↑")
        self._on_sort_changed()

    def _on_sort_changed(self):
        field = self._sort_field.currentData()
        self._settings["sort"][self._current_view] = {"field": field, "ascending": not self._sort_descending}
        self._settings.save()
        self._rebuild()

    def _emit_playing_visibility(self):
        visible = (
            self._playing_uuid is not None
            and self._playing_uuid in self._item_map
            and not self._item_map[self._playing_uuid].isHidden()
        )
        self.playing_visibility_changed.emit(visible)

    def scroll_to_playing(self):
        if self._playing_uuid and self._playing_uuid in self._item_map:
            item = self._item_map[self._playing_uuid]
            if not item.isHidden():
                self._list.scrollToItem(item, self._list.ScrollHint.PositionAtCenter)

    def cancel_pending_favicons(self):
        self._cancel_loaders()

    def start_loading(self):
        self._spinner.start()

    def stop_loading(self):
        self._spinner.stop()

    def set_error(self, message: str, on_retry=None):
        self._list.hide()
        self._error_label.setText(message)
        self._retry_callback = on_retry
        self._retry_btn.setVisible(on_retry is not None)
        self._error_widget.show()

    def _show_limit_overlay(self):
        geo = self._list.geometry()
        sh = self._limit_overlay.sizeHint()
        w = min(sh.width(), geo.width() - 20)
        self._limit_overlay.setGeometry(
            geo.x() + (geo.width() - w) // 2,
            geo.y() + geo.height() - sh.height() - 10,
            w,
            sh.height(),
        )
        self._limit_overlay.raise_()
        self._limit_overlay.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._limit_overlay.isVisible():
            QTimer.singleShot(0, self._show_limit_overlay)

    _NO_SORT_VIEWS = {"recent", "new", "random", "trending", "now_listening"}

    def set_view(self, view: str, fav_uuids: set[str], recent_uuids: list[str], label_filter: str | None = None):
        self._current_view = view
        self._fav_uuids = fav_uuids
        self._recent_uuids = recent_uuids
        self._label_filter = label_filter
        self._limit_reached = False
        self._clear_history_btn.setVisible(view == "recent")
        self._add_station_btn.setVisible(view == "custom")
        self._sort_bar.setVisible(view not in self._NO_SORT_VIEWS)
        if view not in self._NO_SORT_VIEWS:
            self._load_sort_controls(view)
        for w in (self._country_input, self._tag_input, self._language_input):
            w.blockSignals(True)
            w.clear()
            w.blockSignals(False)
        self._apply_filter()

    def update_listening_time(self, uuid: str | None = None):
        """Refresh the "X listened" figure on existing rows in place, in any
        view. Pass a uuid to update one row, or nothing to update all (e.g.
        after clearing)."""
        targets = [uuid] if uuid else list(self._row_widgets.keys())
        for u in targets:
            row = self._row_widgets.get(u)
            if row is not None:
                row.set_listening_seconds(self._listening_stats.total_seconds(u))

    def mark_playing(self, uuid: str):
        if self._playing_uuid:
            self._set_item_highlight(self._playing_uuid, False)
            if self._playing_uuid in self._row_widgets:
                self._row_widgets[self._playing_uuid].set_playing(False)
        self._playing_uuid = uuid
        self._is_playing = True
        self._set_item_highlight(uuid, True)
        if uuid in self._row_widgets:
            self._row_widgets[uuid].set_playing(True)
        self._emit_playing_visibility()

    def mark_stopped(self):
        self._is_playing = False
        if self._playing_uuid and self._playing_uuid in self._row_widgets:
            self._row_widgets[self._playing_uuid].freeze_wave()

    def mark_resumed(self):
        self._is_playing = True
        if self._playing_uuid and self._playing_uuid in self._row_widgets:
            self._row_widgets[self._playing_uuid].set_playing(True)

    def refresh_filter(self):
        """Re-apply the current view's visibility rules without resetting any
        other state (e.g. after a label is toggled on/off for a station)."""
        self._apply_filter()

    def update_favourite(self, uuid: str, is_fav: bool):
        if uuid in self._row_widgets:
            self._row_widgets[uuid].update_favourite(is_fav)
        if self._current_view == "favourites":
            self._apply_filter()

    def _on_row_play(self, station: dict, item: QListWidgetItem):
        self._list.setCurrentItem(item)
        self.station_play_requested.emit(station)

    def _on_favicon_loaded(self, uuid: str, data: bytes):
        self._favicon_cache[uuid] = data
        if uuid in self._row_widgets:
            self._row_widgets[uuid].set_favicon(data)

    def _on_search_debounced(self):
        self._limit_reached = False
        self._apply_filter()
        if self._current_view == "all":
            name = self._search_input.text().strip()
            country = self._country_input.text().strip()
            tag = self._tag_input.text().strip().lower()
            language = self._language_input.text().strip().lower()
            self._spinner.start()
            self.search_params_changed.emit(name, country, tag, language)

    def _apply_filter(self):
        words = self._search_input.text().lower().split()
        country_text = self._country_input.text().strip().lower()
        tag_text = self._tag_input.text().strip().lower()
        language_text = self._language_input.text().strip().lower()
        recent_set = set(self._recent_uuids)

        for i in range(self._list.count()):
            item = self._list.item(i)
            station = item.data(Qt.ItemDataRole.UserRole)
            if not station:
                continue
            uuid = station.get("stationuuid", "")

            visible = True
            if self._current_view == "favourites":
                visible = uuid in self._fav_uuids
                if visible and self._label_filter:
                    visible = self._favourites.has_label(uuid, self._label_filter)
            elif self._current_view == "recent":
                visible = uuid in recent_set

            if visible and words:
                combined = (
                    station.get("name", "") + " " +
                    station.get("tags", "") + " " +
                    station.get("country", "") + " " +
                    station.get("language", "")
                ).lower()
                if not all(w in combined for w in words):
                    visible = False

            if visible and country_text:
                if country_text not in station.get("country", "").lower():
                    visible = False

            if visible and tag_text:
                if tag_text not in station.get("tags", "").lower():
                    visible = False

            if visible and language_text:
                if language_text not in station.get("language", "").lower():
                    visible = False

            item.setHidden(not visible)

        self._spinner.stop()
        total = self._list.count()
        visible_count = sum(1 for i in range(total) if not self._list.item(i).isHidden())
        at_limit = self._limit_reached and visible_count == total
        if total > 0:
            count_str = f"{visible_count}+" if at_limit else str(visible_count)
            self._status_label.setText(self.tr("{0} stations").format(count_str))
        else:
            self._status_label.clear()
        if at_limit:
            QTimer.singleShot(0, self._show_limit_overlay)
        else:
            self._limit_overlay.hide()
        self._emit_playing_visibility()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.PaletteChange:
            if self._playing_uuid:
                self._set_item_highlight(self._playing_uuid, True)
                self._list.viewport().update()
            ss = self._limit_overlay.styleSheet()
            self._limit_overlay.setStyleSheet("")
            self._limit_overlay.setStyleSheet(ss)
        super().changeEvent(event)

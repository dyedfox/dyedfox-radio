from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QFrame, QSystemTrayIcon, QApplication, QMessageBox,
    QScrollArea, QToolButton, QSplitter,
)
from pathlib import Path
import subprocess
from PyQt6.QtCore import Qt, QSize, QEvent, QThreadPool, QTimer
from PyQt6.QtGui import QIcon, QShortcut, QKeySequence, QPixmap

_NOTIF_ICON_PATH = Path.home() / ".config" / "dyedfox-radio" / "notif_icon.png"
_ART_NOTIF_ICON_PATH = Path.home() / ".config" / "dyedfox-radio" / "notif_art.png"
_NOTIFY_ART_TIMEOUT_MS = 9000  # backstop: release a held notification if a cover lookup stalls
_SIDEBAR_MIN_W = 148  # narrowest the nav entries stay readable
_SIDEBAR_MAX_W = 400

from ui.station_list import StationListWidget
from ui.info_panel import InfoPanel
from ui.now_playing import NowPlayingBar
from ui.controls import ControlBar
from ui.settings_dialog import SettingsDialog
from ui.about_dialog import AboutDialog
from ui.notifier import DBusNotifier
from ui.add_station_dialog import AddStationDialog
from player.backend import GStreamerBackend
from api.radio_browser import RadioBrowserClient
from data.favourites import FavouritesManager, RecentManager, new_cache, trending_cache, random_cache, now_listening_cache
from data.listening_stats import ListeningStatsManager
from data.settings import Settings, DEFAULTS
from data.custom_stations import CustomStationsManager

_STATS_FLUSH_INTERVAL_MS = 30000  # periodic checkpoint; app quits via os._exit(), skipping normal shutdown


class MainWindow(QMainWindow):
    def __init__(
        self,
        backend: GStreamerBackend,
        api: RadioBrowserClient,
        favourites: FavouritesManager,
        recent: RecentManager,
        settings: Settings,
        listening_stats: ListeningStatsManager,
    ):
        super().__init__()
        self._backend = backend
        self._api = api
        self._favourites = favourites
        self._recent = recent
        self._settings = settings
        self._listening_stats = listening_stats
        self._custom = CustomStationsManager()
        self._current_station: dict | None = None
        self._current_view = "all"
        self._top_stations: list = []
        self._new_stations_cache: list = new_cache.load()
        self._trending_stations_cache: list = trending_cache.load()
        self._random_stations_cache: list = random_cache.load()
        self._now_listening_stations_cache: list = now_listening_cache.load()
        self._search_results: list = []
        self._last_search_key: tuple = ("", "", "")
        self._last_title: str = ""
        self._tray = None
        self._mpris = None
        # Send song notifications over D-Bus so their body is clickable (raises
        # the window). Falls back to notify-send/tray when D-Bus is unavailable.
        self._notifier = DBusNotifier(parent=self)
        self._notifier.clicked.connect(self._raise_window)
        self._panel_favicon_loaders: set = set()  # keep-alive until run() finishes
        self._current_favicon: QIcon | None = None
        self._pending_notification: tuple[str, str, int] | None = None  # (station, title, art_token)
        self._notify_await_art: bool = False  # holding the notification for a cover lookup
        self._artwork_loaders: set = set()  # keep-alive until run() finishes
        self._art_token: int = 0  # identifies the current song for stale-result guarding
        self._current_art_url: str = ""

        # Clicking a station calls GStreamer set_state(NULL), which blocks the GUI
        # thread until the stream tears down. Rapid clicks stack these teardowns
        # and freeze the UI, so debounce: only the final selection actually plays.
        self._pending_play_station: dict | None = None
        self._play_debounce = QTimer(self)
        self._play_debounce.setSingleShot(True)
        self._play_debounce.setInterval(250)
        self._play_debounce.timeout.connect(self._start_pending_play)

        self.setWindowTitle("Dyedfox Radio")
        _icon = Path(__file__).parent.parent / "assets" / "icons" / "dyedfox-radio.png"
        self.setWindowIcon(QIcon(str(_icon)))

        # The app quits via os._exit() (see main.py), which skips finalizers, so
        # window size is persisted on change rather than at quit. Debounce to avoid
        # rewriting settings.json on every resize event during a drag. Created
        # before _restore_window_size() since resize() may fire resizeEvent.
        self._size_save_timer = QTimer(self)
        self._size_save_timer.setSingleShot(True)
        self._size_save_timer.setInterval(500)
        self._size_save_timer.timeout.connect(self._save_window_size)

        # Same deal for the sidebar width, which the user can drag.
        self._sidebar_save_timer = QTimer(self)
        self._sidebar_save_timer.setSingleShot(True)
        self._sidebar_save_timer.setInterval(500)
        self._sidebar_save_timer.timeout.connect(self._save_sidebar_width)

        # Periodically checkpoints the in-progress listening-time segment to
        # disk; a no-op when nothing is playing. Guards against losing a long
        # session's time since the app quits via os._exit() (see main.py).
        self._stats_flush_timer = QTimer(self)
        self._stats_flush_timer.setInterval(_STATS_FLUSH_INTERVAL_MS)
        self._stats_flush_timer.timeout.connect(self._on_stats_flush_tick)
        self._stats_flush_timer.start()

        self._restore_window_size()
        self._setup_ui()
        self._connect_signals()
        self._apply_settings()
        self._setup_shortcuts()

    def _sep(self, vertical=False) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine if vertical else QFrame.Shape.HLine)
        return sep

    def _setup_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # The sidebar is drag-resizable, so it sits in a splitter. The handle is
        # transparent and slightly wider than the divider line it replaces —
        # a 1px grab target is too fiddly — with the visible line kept as the
        # first child on the right so the look matches the other dividers.
        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.setChildrenCollapsible(False)
        self._content_splitter.setHandleWidth(4)
        self._content_splitter.setStyleSheet("QSplitter::handle { background: transparent; }")
        root_layout.addWidget(self._content_splitter, 1)

        self._sidebar = self._build_sidebar()
        self._content_splitter.addWidget(self._sidebar)

        main_area = QWidget()
        content_layout = QHBoxLayout(main_area)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        content_layout.addWidget(self._sep(vertical=True))

        self._station_list = StationListWidget(self._favourites, self._settings, self._listening_stats)
        content_layout.addWidget(self._station_list, 1)

        content_layout.addWidget(self._sep(vertical=True))

        self._info_panel = InfoPanel()
        self._info_panel.set_album_art_enabled(self._settings["show_album_art"])
        content_layout.addWidget(self._info_panel)

        self._content_splitter.addWidget(main_area)
        self._content_splitter.setStretchFactor(0, 0)  # sidebar keeps its width
        self._content_splitter.setStretchFactor(1, 1)  # station list absorbs resizes
        self._content_splitter.splitterMoved.connect(lambda *_: self._sidebar_save_timer.start())
        self._restore_sidebar_width()

        root_layout.addWidget(self._sep())

        self._now_playing = NowPlayingBar()
        root_layout.addWidget(self._now_playing)

        root_layout.addWidget(self._sep())

        self._controls = ControlBar()
        root_layout.addWidget(self._controls)

    def _build_sidebar(self) -> QWidget:
        from PyQt6.QtWidgets import QLabel
        sidebar = QWidget()
        sidebar.setMinimumWidth(_SIDEBAR_MIN_W)
        sidebar.setMaximumWidth(_SIDEBAR_MAX_W)
        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Navigation scrolls on its own so a long label list can't squeeze the
        # entries; Settings/About stay pinned below it.
        nav = QWidget()
        layout = QVBoxLayout(nav)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(2)

        _nav_style = "QPushButton { text-align: left; padding: 4px 8px; }"
        _sec_style = "QLabel { color: palette(placeholder-text); font-size: small; padding: 6px 8px 2px 8px; }"
        self._nav_btns: dict[str, QPushButton] = {}

        def _section(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(_sec_style)
            return lbl

        def _nav_btn(label: str, view: str, icon: str) -> QPushButton:
            btn = QPushButton(label)
            btn.setFlat(True)
            btn.setCheckable(True)
            btn.setIcon(QIcon.fromTheme(icon))
            btn.setIconSize(QSize(16, 16))
            btn.setStyleSheet(_nav_style)
            btn.clicked.connect(lambda _, v=view: self._switch_view(v))
            self._nav_btns[view] = btn
            return btn

        # --- LIBRARY ---
        layout.addWidget(_section(self.tr("LIBRARY")))
        layout.addWidget(_nav_btn(self.tr("All stations"), "all",        "network-wireless"))
        # Favourites carries a disclosure arrow that folds its label sub-menu
        # away; the arrow is a separate button so clicking the row still
        # navigates rather than collapsing.
        fav_row = QWidget()
        fav_row_layout = QHBoxLayout(fav_row)
        fav_row_layout.setContentsMargins(0, 0, 0, 0)
        fav_row_layout.setSpacing(0)
        fav_row_layout.addWidget(_nav_btn(self.tr("Favourites"), "favourites", "emblem-favorite"), 1)

        self._labels_toggle = QToolButton()
        self._labels_toggle.setAutoRaise(True)
        self._labels_toggle.setFixedWidth(16)
        self._labels_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._labels_toggle.clicked.connect(self._toggle_labels)
        fav_row_layout.addWidget(self._labels_toggle)
        layout.addWidget(fav_row)

        # Populated by _rebuild_label_nav() with one indented entry per favourite
        # label, so it must stay directly under the Favourites row.
        self._label_nav = QWidget()
        self._label_nav_layout = QVBoxLayout(self._label_nav)
        self._label_nav_layout.setContentsMargins(0, 0, 0, 0)
        self._label_nav_layout.setSpacing(2)
        layout.addWidget(self._label_nav)

        layout.addWidget(_nav_btn(self.tr("Custom"),       "custom",     "document-edit"))
        layout.addWidget(_nav_btn(self.tr("History"), "recent", "document-open-recent"))

        # --- DISCOVER ---
        layout.addWidget(_section(self.tr("DISCOVER")))
        layout.addWidget(_nav_btn(self.tr("New"),           "new",           "starred"))
        layout.addWidget(_nav_btn(self.tr("Random"),        "random",        "media-playlist-shuffle"))
        layout.addWidget(_nav_btn(self.tr("Trending"),      "trending",      "office-chart-bar"))
        layout.addWidget(_nav_btn(self.tr("Now Listening"), "now_listening", "view-process-users"))

        self._nav_btns["all"].setChecked(True)

        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(nav)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(scroll, 1)

        outer.addWidget(self._sep())

        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(4, 4, 4, 8)
        footer_layout.setSpacing(2)

        self._settings_btn = QPushButton(self.tr("Settings"))
        self._settings_btn.setFlat(True)
        self._settings_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._settings_btn.setIcon(QIcon.fromTheme("preferences-system"))
        self._settings_btn.setIconSize(QSize(16, 16))
        self._settings_btn.setStyleSheet("QPushButton { text-align: left; padding: 4px 8px; }")
        self._settings_btn.clicked.connect(self._open_settings)
        footer_layout.addWidget(self._settings_btn)

        self._about_btn = QPushButton(self.tr("About"))
        self._about_btn.setFlat(True)
        self._about_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._about_btn.setIcon(QIcon.fromTheme("help-about"))
        self._about_btn.setIconSize(QSize(16, 16))
        self._about_btn.setStyleSheet("QPushButton { text-align: left; padding: 4px 8px; }")
        self._about_btn.clicked.connect(self._open_about)
        footer_layout.addWidget(self._about_btn)

        outer.addWidget(footer)

        self._rebuild_label_nav()

        return sidebar

    def _toggle_labels(self):
        self._settings["labels_expanded"] = not self._settings["labels_expanded"]
        self._settings.save()
        self._rebuild_label_nav()

    def _rebuild_label_nav(self):
        """Refresh the indented label entries under Favourites to match the
        current set of favourite labels (labels with no favourites left just
        disappear)."""
        while self._label_nav_layout.count():
            item = self._label_nav_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            for view, btn in list(self._nav_btns.items()):
                if btn is widget:
                    del self._nav_btns[view]
            widget.deleteLater()

        labels = self._favourites.all_labels()
        expanded = self._settings["labels_expanded"]
        self._labels_toggle.setVisible(bool(labels))
        self._labels_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._labels_toggle.setToolTip(
            self.tr("Hide labels") if expanded else self.tr("Show labels")
        )
        self._label_nav.setVisible(bool(labels) and expanded)

        for label in labels:
            view = f"label:{label}"
            btn = QPushButton(label)
            btn.setFlat(True)
            btn.setCheckable(True)
            btn.setStyleSheet(
                "QPushButton { text-align: left; padding: 4px 8px 4px 26px; color: palette(placeholder-text); }"
            )
            btn.setChecked(view == self._current_view)
            btn.clicked.connect(lambda _, v=view: self._switch_view(v))
            self._nav_btns[view] = btn
            self._label_nav_layout.addWidget(btn)

    def _connect_signals(self):
        self._backend.metadata_changed.connect(self._on_metadata)
        self._backend.error_occurred.connect(self._on_stream_error)
        self._backend.playback_stopped.connect(self._on_stopped)
        self._backend.playback_started.connect(self._on_started)
        self._backend.reconnecting.connect(self._on_reconnecting)

        self._station_list.station_play_requested.connect(self._on_station_play)
        self._station_list.favourite_toggled.connect(self._on_favourite_toggled)
        self._station_list.label_toggled.connect(self._on_label_toggled)
        self._station_list.search_params_changed.connect(self._on_search_params_changed)
        self._station_list.station_delete_requested.connect(self._on_custom_delete)
        self._station_list.station_edit_requested.connect(self._on_custom_edit)
        self._station_list.station_remove_requested.connect(self._on_remove_recent)
        self._station_list.reset_listening_requested.connect(self._on_reset_listening)
        self._station_list.clear_history_requested.connect(self._on_clear_recent)
        self._station_list.add_station_requested.connect(self._on_add_custom_station)

        self._now_playing.clicked.connect(self._station_list.scroll_to_playing)
        self._station_list.playing_visibility_changed.connect(self._now_playing.set_clickable)
        self._controls.playback_toggled.connect(self._on_playback_toggled)
        self._controls.volume_changed.connect(self._on_volume_changed)
        self._controls.mute_toggled.connect(self._on_mute_toggled)

        self._info_panel.favourite_toggled.connect(self._on_favourite_toggled)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(QApplication.quit)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.hide)

    def _apply_settings(self):
        vol = self._settings["volume"]
        self._controls.set_volume_slider(vol)
        self._backend.set_volume(vol)

    def load_top_stations(self, autoplay_uuid: str = ""):
        self._station_list.start_loading()

        def on_loaded(stations: list):
            self._top_stations = stations
            if self._current_view == "all":
                self._station_list.set_stations(stations)
            if autoplay_uuid:
                match = next((s for s in stations if s.get("stationuuid") == autoplay_uuid), None)
                if match:
                    self._on_station_play(match)
                elif autoplay_uuid.startswith("custom-"):
                    custom_match = next((s for s in self._custom.all() if s.get("stationuuid") == autoplay_uuid), None)
                    if custom_match:
                        self._on_station_play(custom_match)
                else:
                    self._api.stations_by_uuids(
                        [autoplay_uuid],
                        on_result=lambda result: self._on_station_play(result[0]) if result else None,
                    )

        self._api.top_stations(
            limit=self._settings["station_limit"],
            on_result=on_loaded,
            on_error=lambda e: self._station_list.set_error(
                self.tr("Could not load stations — check your connection"),
                on_retry=lambda: self.load_top_stations(),
            ),
        )

    def set_tray(self, tray):
        self._tray = tray

    def set_mpris(self, mpris):
        self._mpris = mpris

    def _open_settings(self):
        dlg = SettingsDialog(self._settings, self._listening_stats, self)
        dlg.listening_cleared.connect(self._on_listening_cleared)
        dlg.exec()
        enabled = self._settings["show_album_art"]
        self._info_panel.set_album_art_enabled(enabled)
        if not enabled:
            self._info_panel.clear_album_art()
        elif self._current_station and self._last_title:
            self._fetch_artwork(self._last_title)

    def _open_about(self):
        AboutDialog(self).exec()

    def _switch_view(self, view: str):
        self._current_view = view
        self._settings["last_view"] = view
        self._settings.save()
        self._search_results = []
        self._last_search_key = ("", "", "")
        for v, btn in self._nav_btns.items():
            btn.setChecked(v == view)

        # A label view ("label:<name>") browses the same favourites data as the
        # Favourites view, just narrowed by label — see StationListWidget's
        # label_filter handling in _apply_filter.
        label_filter = view[6:] if view.startswith("label:") else None
        data_view = "favourites" if label_filter else view

        # Navigating into a label implies its group is open — otherwise the
        # active entry would stay hidden (e.g. restoring last_view at startup).
        if label_filter and not self._settings["labels_expanded"]:
            self._settings["labels_expanded"] = True
            self._settings.save()
            self._rebuild_label_nav()

        # Set the filter mode first so it's in place when async stations arrive.
        self._station_list.set_view(data_view, self._favourites.uuids(), self._recent.uuids(), label_filter=label_filter)

        if data_view == "custom":
            self._station_list.set_stations(self._custom.all(), deletable=True)
            return

        if data_view == "favourites":
            cached = self._favourites.cached_stations()
            self._station_list.start_loading()
            if cached:
                self._station_list.set_stations(cached)
            uuids = list(self._favourites.uuids())

            def _on_fav_loaded(stations: list):
                self._favourites.cache_stations(stations)
                self._station_list.set_stations(stations)

            self._api.stations_by_uuids(
                uuids,
                on_result=_on_fav_loaded,
                on_error=lambda e: self._station_list.set_error(
                    self.tr("Could not load favourites — check your connection"),
                    on_retry=lambda: self._switch_view(view),
                ) if not cached else None,
            )
        elif data_view == "recent":
            uuids = self._recent.uuids()
            if uuids:
                cached = self._recent.cached_stations()
                self._station_list.start_loading()
                if cached:
                    self._station_list.set_stations(cached)
                def _on_recent_loaded(stations: list, ordered=uuids):
                    by_uuid = {s.get("stationuuid"): s for s in stations}
                    ordered_stations = [by_uuid[u] for u in ordered if u in by_uuid]
                    self._recent.cache_stations(ordered_stations)
                    self._station_list.set_stations(ordered_stations)
                self._api.stations_by_uuids(
                    uuids,
                    on_result=_on_recent_loaded,
                    on_error=lambda e: self._station_list.set_error(
                        self.tr("Could not load history — check your connection"),
                        on_retry=lambda: self._switch_view("recent"),
                    ) if not cached else None,
                )
            else:
                self._station_list.set_stations([])
        elif data_view == "new":
            self._station_list.start_loading()
            if self._new_stations_cache:
                self._station_list.set_stations(self._new_stations_cache)
            def _on_new_loaded(stations: list):
                self._new_stations_cache = stations
                new_cache.save(stations)
                self._station_list.set_stations(stations)
            self._api.new_stations(
                limit=self._settings["station_limit"],
                on_result=_on_new_loaded,
                on_error=lambda e: self._station_list.set_error(
                    self.tr("Could not load new stations — check your connection"),
                    on_retry=lambda: self._switch_view("new"),
                ) if not self._new_stations_cache else None,
            )
        elif data_view == "random":
            self._station_list.start_loading()
            if self._random_stations_cache:
                self._station_list.set_stations(self._random_stations_cache)
            def _on_random_loaded(stations: list):
                self._random_stations_cache = stations
                random_cache.save(stations)
                self._station_list.set_stations(stations)
            self._api.random_stations(
                limit=50,
                on_result=_on_random_loaded,
                on_error=lambda e: self._station_list.set_error(
                    self.tr("Could not load stations — check your connection"),
                    on_retry=lambda: self._switch_view("random"),
                ) if not self._random_stations_cache else None,
            )
        elif data_view == "trending":
            self._station_list.start_loading()
            if self._trending_stations_cache:
                self._station_list.set_stations(self._trending_stations_cache)
            def _on_trending_loaded(stations: list):
                self._trending_stations_cache = stations
                trending_cache.save(stations)
                self._station_list.set_stations(stations)
            self._api.trending_stations(
                limit=self._settings["station_limit"],
                on_result=_on_trending_loaded,
                on_error=lambda e: self._station_list.set_error(
                    self.tr("Could not load trending stations — check your connection"),
                    on_retry=lambda: self._switch_view("trending"),
                ) if not self._trending_stations_cache else None,
            )
        elif data_view == "now_listening":
            self._station_list.start_loading()
            if self._now_listening_stations_cache:
                self._station_list.set_stations(self._now_listening_stations_cache)
            def _on_now_listening_loaded(stations: list):
                self._now_listening_stations_cache = stations
                now_listening_cache.save(stations)
                self._station_list.set_stations(stations)
            self._api.now_listening_stations(
                limit=self._settings["station_limit"],
                on_result=_on_now_listening_loaded,
                on_error=lambda e: self._station_list.set_error(
                    self.tr("Could not load now listening stations — check your connection"),
                    on_retry=lambda: self._switch_view("now_listening"),
                ) if not self._now_listening_stations_cache else None,
            )
        else:
            if self._top_stations:
                self._station_list.start_loading()
                self._station_list.set_stations(self._top_stations)
            else:
                self.load_top_stations()

    def _on_station_play(self, station: dict):
        url = station.get("url_resolved", "")
        if not url:
            return
        # Defer the actual play so a burst of clicks results in a single stream
        # teardown/start instead of one blocking set_state(NULL) per click.
        self._pending_play_station = station
        self._play_debounce.start()

    def _start_pending_play(self):
        station = self._pending_play_station
        self._pending_play_station = None
        if not station:
            return
        url = station.get("url_resolved", "")
        if not url:
            return
        # Switching stations doesn't emit playback_stopped for the outgoing one
        # (see backend.play()), so fold its segment into the totals here.
        self._listening_stats.stop()
        self._current_station = station
        self._last_title = ""
        self._art_token += 1  # invalidate any in-flight artwork from the previous station
        self._current_art_url = ""
        self._backend.play(url)
        self._now_playing.set_station(station.get("name", "").strip())
        self._now_playing.clear_song()
        self._info_panel.set_station(station, self._favourites.is_favourite(station.get("stationuuid", "")))
        self._update_listening_time_display()
        self._station_list.mark_playing(station.get("stationuuid", ""))
        self._recent.add(station.get("stationuuid", ""))

        self._current_favicon = None
        self._pending_notification = None
        self._notify_await_art = False
        favicon_url = station.get("favicon", "")
        if favicon_url:
            self._load_panel_favicon(favicon_url)

    def _load_panel_favicon(self, url: str):
        from ui.station_list import _FaviconLoader
        loader = _FaviconLoader("panel", url)
        loader.signals.loaded.connect(self._on_panel_favicon_loaded)
        # Hold a reference until run() finishes; switching stations quickly would
        # otherwise GC an in-flight loader and crash the pool (use-after-free).
        loader.signals.finished.connect(self._panel_favicon_loaders.discard)
        self._panel_favicon_loaders.add(loader)
        QThreadPool.globalInstance().start(loader)

    def _on_panel_favicon_loaded(self, _uuid: str, data: bytes):
        self._info_panel.set_favicon(data)
        pix = QPixmap()
        pix.loadFromData(data)
        if not pix.isNull():
            self._current_favicon = QIcon(pix)
            pix.save(str(_NOTIF_ICON_PATH), "PNG")
            # Fire a notification that was waiting only for the favicon. If we're
            # instead holding it for a cover lookup, the artwork path owns it.
            if self._pending_notification and not self._notify_await_art:
                _, _, token = self._pending_notification
                self._flush_notification(token)

    def _on_metadata(self, title: str):
        self._now_playing.set_song(title)
        self._info_panel.set_now_playing(title)
        if self._tray and self._current_station:
            self._tray.update_status(self._current_station.get("name", ""), title)
        if title == self._last_title:
            return
        self._last_title = title
        art_started = self._fetch_artwork(title)
        if self._settings["notifications"] and self._tray and self._current_station:
            station_name = self._current_station.get("name", "Dyedfox Radio")
            token = self._art_token
            self._pending_notification = (station_name, title, token)
            if art_started:
                # Hold the notification until the cover resolves. The lookup's
                # finished signal releases it (with the cover, or the favicon if
                # no match); a backstop timer guards against a stalled lookup.
                self._notify_await_art = True
                QTimer.singleShot(_NOTIFY_ART_TIMEOUT_MS, lambda: self._flush_notification(token))
            else:
                self._notify_await_art = False
                if self._current_favicon or not self._current_station.get("favicon"):
                    self._flush_notification(token)  # favicon if loaded, else themed icon
                # else: a favicon is still downloading — _on_panel_favicon_loaded fires it
        if self._mpris and self._current_station:
            self._mpris.update_metadata(
                title,
                self._current_station.get("name", ""),
                self._current_station.get("favicon", ""),
            )

    def _fetch_artwork(self, title: str) -> bool:
        """Start an async cover lookup. Returns True if a lookup was started."""
        from api.artwork import parse_now_playing, ArtworkLoader
        self._art_token += 1
        self._current_art_url = ""
        # Drop the previous song's cover so it doesn't linger if the new song has
        # no match; revert to the favicon until/unless new art arrives.
        self._info_panel.clear_album_art()
        if not (self._settings["show_album_art"] and self._current_station):
            return False
        query = parse_now_playing(title)
        if not query:
            return False
        loader = ArtworkLoader(self._art_token, query)
        loader.signals.loaded.connect(self._on_artwork_loaded)
        loader.signals.finished.connect(self._artwork_loaders.discard)
        loader.signals.finished.connect(lambda _l, t=self._art_token: self._on_artwork_finished(t))
        self._artwork_loaders.add(loader)
        QThreadPool.globalInstance().start(loader)
        return True

    def _on_artwork_loaded(self, token: int, data: bytes, art_url: str):
        if token != self._art_token:
            return  # stale: the song or station changed before this resolved
        self._info_panel.set_album_art(data, art_url)
        self._current_art_url = art_url
        if self._mpris and self._current_station:
            self._mpris.update_metadata(
                self._last_title,
                self._current_station.get("name", ""),
                art_url,
            )
        # Release a held notification with the cover as its icon.
        if self._notify_await_art:
            pix = QPixmap()
            pix.loadFromData(data)
            if not pix.isNull():
                pix.save(str(_ART_NOTIF_ICON_PATH), "PNG")
                self._flush_notification(token, str(_ART_NOTIF_ICON_PATH))
            else:
                self._flush_notification(token)

    def _on_artwork_finished(self, token: int):
        # The lookup concluded. If a cover was found, _on_artwork_loaded already
        # fired the notification; otherwise release it now with the favicon so a
        # no-match song doesn't wait for the backstop timer.
        if self._notify_await_art:
            self._flush_notification(token)

    def _on_stream_error(self, msg: str):
        print(f"dyedfox-radio: stream error: {msg}", flush=True)
        self._now_playing.set_error()
        self._controls.set_playing(False)
        if self._tray:
            station = self._current_station.get("name", "the station") if self._current_station else "the station"
            self._tray.showMessage(
                "Dyedfox Radio",
                self.tr("Could not connect to {0}. The stream may be down or unavailable.").format(station),
                QSystemTrayIcon.MessageIcon.Warning,
                4000,
            )

    def _on_playback_toggled(self):
        if self._backend.is_playing:
            self._backend.stop()
        else:
            self._backend.play_last()

    def _on_volume_changed(self, value: int):
        self._backend.set_volume(value)
        self._settings["volume"] = value
        self._settings.save()

    def _on_mute_toggled(self):
        muted = not self._backend.is_muted
        self._backend.set_muted(muted)
        self._controls.set_muted(muted)
        if self._tray:
            self._tray.set_muted(muted)

    def set_muted(self, muted: bool):
        self._backend.set_muted(muted)
        self._controls.set_muted(muted)

    def _on_stopped(self):
        self._listening_stats.stop()
        self._update_listening_time_display()
        self._controls.set_playing(False)
        self._station_list.mark_stopped()
        if self._mpris:
            self._mpris.update_playback_status()

    def _on_reconnecting(self, attempt: int):
        # No audio flows during the reconnect gap, so pause accrual.
        self._listening_stats.stop()
        self._update_listening_time_display()
        self._now_playing.set_reconnecting(attempt)
        self._controls.set_playing(False)
        self._station_list.mark_stopped()

    def _on_started(self):
        if self._current_station:
            self._listening_stats.start(self._current_station.get("stationuuid", ""))
        self._update_listening_time_display()
        self._controls.set_playing(True)
        self._station_list.mark_resumed()
        # After an auto-reconnect the strip shows "Reconnecting…"; restore the
        # station name (the next ICY tag refills the song).
        if self._current_station:
            self._now_playing.set_station(self._current_station.get("name", "").strip())
        if self._mpris:
            self._mpris.update_playback_status()

    def _on_stats_flush_tick(self):
        self._listening_stats.flush()
        self._update_listening_time_display()

    def _update_listening_time_display(self):
        uuid = self._current_station.get("stationuuid", "") if self._current_station else ""
        self._info_panel.set_listening_time(self._listening_stats.total_seconds(uuid) if uuid else 0)
        # Keep the playing station's list row in sync with the info panel (its
        # figure otherwise only reflects the total captured when the row was built).
        if uuid:
            self._station_list.update_listening_time(uuid)

    def _flush_notification(self, token: int, icon_path: str | None = None):
        """Fire the queued notification if it still belongs to the given song."""
        if not self._pending_notification:
            return
        station, title, tok = self._pending_notification
        if tok != token:
            return  # a newer song superseded this one
        self._pending_notification = None
        self._notify_await_art = False
        self._show_notification(station, title, icon_path)

    def _raise_window(self):
        """Bring the main window to the front (from a clicked notification)."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _show_notification(self, station: str, title: str, icon_path: str | None = None):
        if icon_path and Path(icon_path).exists():
            icon = icon_path
        elif self._current_favicon and _NOTIF_ICON_PATH.exists():
            icon = str(_NOTIF_ICON_PATH)
        else:
            icon = "dyedfox-radio"
        # Prefer D-Bus so the notification is clickable (opens the window).
        if self._notifier.notify(station, title, icon):
            return
        # Fallback 1: notify-send. Fallback 2: the tray bubble.
        try:
            subprocess.Popen(
                ["notify-send", "-a", "Dyedfox Radio", "-t", "3000", "-i", icon, "--", station, title]
            )
        except FileNotFoundError:
            if self._tray:
                if icon_path and Path(icon_path).exists():
                    self._tray.showMessage(station, title, QIcon(icon_path), 3000)
                else:
                    self._tray.showMessage(station, title, QSystemTrayIcon.MessageIcon.NoIcon, 3000)

    def _on_clear_recent(self):
        self._recent.clear()
        self._station_list.set_view("recent", self._favourites.uuids(), self._recent.uuids())

    def _on_remove_recent(self, uuid: str):
        self._recent.remove(uuid)
        # set_view re-filters the existing rows against the updated history, so the
        # removed station disappears without a network round-trip.
        self._station_list.set_view("recent", self._favourites.uuids(), self._recent.uuids())

    def _on_reset_listening(self, uuid: str):
        self._listening_stats.remove(uuid)
        # remove() drops the in-progress segment if this station is playing;
        # resume it so the ongoing session keeps counting from zero.
        if (self._backend.is_playing and self._current_station
                and self._current_station.get("stationuuid", "") == uuid):
            self._listening_stats.start(uuid)
        self._update_listening_time_display()
        self._station_list.update_listening_time(uuid)

    def _on_listening_cleared(self):
        if self._backend.is_playing and self._current_station:
            self._listening_stats.start(self._current_station.get("stationuuid", ""))
        self._update_listening_time_display()
        self._station_list.update_listening_time()

    def _on_favourite_toggled(self, uuid: str, is_fav: bool):
        self._favourites.set(uuid, is_fav)
        self._station_list.update_favourite(uuid, is_fav)
        if self._current_station and self._current_station.get("stationuuid") == uuid:
            self._info_panel.set_favourite(is_fav)
        if not is_fav:
            # Unfavouriting drops the station's labels too (see FavouritesManager.set),
            # which can empty a label and remove its entry from the sidebar.
            self._rebuild_label_nav()
            active_label = self._current_view[6:] if self._current_view.startswith("label:") else None
            if active_label and active_label not in self._favourites.all_labels():
                self._switch_view("favourites")

    def _on_label_toggled(self, uuid: str, label: str, on: bool):
        self._favourites.set_label(uuid, label, on)
        self._rebuild_label_nav()
        active_label = self._current_view[6:] if self._current_view.startswith("label:") else None
        if active_label and active_label not in self._favourites.all_labels():
            # The label we were browsing has no favourites left; it just
            # vanished from the sidebar, so fall back to plain Favourites.
            self._switch_view("favourites")
        elif self._current_view == "favourites" or active_label:
            self._station_list.refresh_filter()

    def _on_search_params_changed(self, name: str, country: str, tag: str, language: str):
        if not name and not country and not tag and not language:
            if self._top_stations:
                self._station_list.set_stations(self._top_stations)
            else:
                self.load_top_stations()
            return

        key = (name, country, tag, language)
        if key == self._last_search_key and self._search_results:
            limit_reached = len(self._search_results) >= self._settings["station_limit"]
            self._station_list.set_stations(self._search_results, limit_reached=limit_reached)
            return

        self._last_search_key = key

        def _on_result(stations: list):
            self._search_results = stations
            limit_reached = len(stations) >= self._settings["station_limit"]
            self._station_list.set_stations(stations, limit_reached=limit_reached)

        self._api.search(
            name=name,
            country=country,
            tag=tag,
            language=language,
            limit=self._settings["station_limit"],
            on_result=_on_result,
            on_error=lambda e: (print(f"dyedfox-radio: search error: {e}", flush=True), self._station_list.stop_loading()),
        )

    def _on_add_custom_station(self):
        dlg = AddStationDialog(self)
        if dlg.exec():
            name, url, favicon, tags, country, language = dlg.values()
            self._custom.add(name, url, favicon, tags, country, language)
            self._station_list.set_stations(self._custom.all(), deletable=True)

    def _on_custom_edit(self, uuid: str):
        station = next((s for s in self._custom.all() if s.get("stationuuid") == uuid), None)
        if not station:
            return
        dlg = AddStationDialog(
            self,
            name=station.get("name", ""),
            url=station.get("url_resolved", ""),
            favicon=station.get("favicon", ""),
            tags=station.get("tags", ""),
            country=station.get("country", ""),
            language=station.get("language", ""),
        )
        if dlg.exec():
            name, url, favicon, tags, country, language = dlg.values()
            self._custom.update(uuid, name, url, favicon, tags, country, language)
            self._station_list.set_stations(self._custom.all(), deletable=True)

    def _on_custom_delete(self, uuid: str):
        name = next(
            (s.get("name", "") for s in self._custom.all() if s.get("stationuuid") == uuid),
            "",
        )
        reply = QMessageBox.question(
            self,
            self.tr("Delete station"),
            self.tr("Delete “{0}” from your custom stations?").format(name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._custom.remove(uuid)
        self._station_list.set_stations(self._custom.all(), deletable=True)

    def _restore_window_size(self):
        size = self._settings["window_size"]
        try:
            w, h = int(size[0]), int(size[1])
        except (TypeError, ValueError, IndexError):
            w, h = 960, 620
        # Guard against absurd or corrupt values restoring an unusable window.
        w = max(640, min(w, 7680))
        h = max(480, min(h, 4320))
        self.resize(w, h)

    def _save_window_size(self):
        # Persist only the normal (restored) size, not maximized/minimized states,
        # so un-maximizing later returns to a sensible size.
        if self.isMaximized() or self.isMinimized() or self.isFullScreen():
            return
        self._settings["window_size"] = [self.width(), self.height()]
        self._settings.save()

    def _restore_sidebar_width(self):
        try:
            w = int(self._settings["sidebar_width"])
        except (TypeError, ValueError):
            w = DEFAULTS["sidebar_width"]
        w = max(_SIDEBAR_MIN_W, min(w, _SIDEBAR_MAX_W))
        self._content_splitter.setSizes([w, max(1, self.width() - w)])

    def _save_sidebar_width(self):
        self._settings["sidebar_width"] = self._sidebar.width()
        self._settings.save()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._size_save_timer.start()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.PaletteChange:
            for btn in [
                *self._nav_btns.values(),
                self._settings_btn, self._about_btn,
            ]:
                btn.setStyleSheet(btn.styleSheet())
        super().changeEvent(event)

    def closeEvent(self, event):
        event.ignore()
        self.hide()

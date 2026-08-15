from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QCheckBox, QComboBox,
    QDialogButtonBox, QGroupBox, QLabel, QPushButton, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import QEvent, pyqtSignal
from pathlib import Path

from data.settings import Settings
from data.listening_stats import ListeningStatsManager
from data import backup as _backup


class SettingsDialog(QDialog):
    listening_cleared = pyqtSignal()

    def __init__(self, settings: Settings, listening_stats: ListeningStatsManager, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._listening_stats = listening_stats
        self.setWindowTitle(self.tr("Settings"))
        self.setFixedWidth(600)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # --- Startup ---
        startup = QGroupBox(self.tr("Startup"))
        startup_layout = QVBoxLayout(startup)

        self._start_minimized = QCheckBox(self.tr("Start minimized to tray"))
        self._start_minimized.setChecked(settings["start_minimized"])
        startup_layout.addWidget(self._start_minimized)

        self._autoplay_last = QCheckBox(self.tr("Autoplay last station"))
        self._autoplay_last.setChecked(settings["autoplay_last"])
        startup_layout.addWidget(self._autoplay_last)

        note = QLabel(self.tr("Startup options take effect on next launch."))
        note.setEnabled(False)
        startup_layout.addWidget(note)

        layout.addWidget(startup)

        # --- Stations ---
        stations = QGroupBox(self.tr("Stations"))
        stations_layout = QFormLayout(stations)
        stations_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        self._limit = QComboBox()
        for n in [50, 100, 200, 500]:
            self._limit.addItem(self.tr("{0} stations").format(n), n)
        current = settings["station_limit"]
        self._limit.setCurrentIndex({50: 0, 100: 1, 200: 2, 500: 3}.get(current, 1))
        stations_layout.addRow(self.tr("Top stations to load:"), self._limit)

        stations_note = QLabel(self.tr("Higher values slow down initial load and search. Prefer using search and filters over increasing this limit."))
        stations_note.setEnabled(False)
        stations_note.setWordWrap(True)
        stations_layout.addRow(stations_note)

        layout.addWidget(stations)

        # --- Notifications ---
        notif = QGroupBox(self.tr("Notifications"))
        notif_layout = QVBoxLayout(notif)

        self._notifications = QCheckBox(self.tr("Show song change notifications"))
        self._notifications.setChecked(settings["notifications"])
        notif_layout.addWidget(self._notifications)

        layout.addWidget(notif)

        # --- Now playing ---
        nowplaying = QGroupBox(self.tr("Now playing"))
        nowplaying_layout = QVBoxLayout(nowplaying)

        self._show_album_art = QCheckBox(self.tr("Show album art for the current song"))
        self._show_album_art.setChecked(settings["show_album_art"])
        nowplaying_layout.addWidget(self._show_album_art)

        art_note = QLabel(self.tr("Cover art is looked up from Deezer using the song title. "
                                  "Falls back to the station logo when no match is found."))
        art_note.setEnabled(False)
        art_note.setWordWrap(True)
        nowplaying_layout.addWidget(art_note)

        layout.addWidget(nowplaying)

        # --- Listening time ---
        listening = QGroupBox(self.tr("Listening time"))
        listening_layout = QVBoxLayout(listening)

        listening_note = QLabel(self.tr("Time listened is tracked per station and shown in the info panel and History."))
        listening_note.setEnabled(False)
        listening_note.setWordWrap(True)
        listening_layout.addWidget(listening_note)

        clear_row = QHBoxLayout()
        clear_listening_btn = QPushButton(self.tr("Clear all listening time…"))
        clear_listening_btn.setEnabled(listening_stats.has_any())
        clear_listening_btn.clicked.connect(self._on_clear_listening)
        clear_row.addWidget(clear_listening_btn)
        clear_row.addStretch()
        listening_layout.addLayout(clear_row)

        layout.addWidget(listening)

        # --- Backup / Restore ---
        backup_group = QGroupBox(self.tr("Backup"))
        backup_layout = QVBoxLayout(backup_group)

        backup_note = QLabel(self.tr("Back up and restore your favourites, labels, custom stations, history, listening time, and settings."))
        backup_note.setEnabled(False)
        backup_note.setWordWrap(True)
        backup_layout.addWidget(backup_note)

        btn_row = QHBoxLayout()
        export_btn = QPushButton(self.tr("Export…"))
        export_btn.clicked.connect(self._on_export)
        import_btn = QPushButton(self.tr("Import…"))
        import_btn.clicked.connect(self._on_import)
        btn_row.addWidget(export_btn)
        btn_row.addWidget(import_btn)
        btn_row.addStretch()
        backup_layout.addLayout(btn_row)

        layout.addWidget(backup_group)

        # --- Buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Size the height to exactly fit the content at 600px wide. Word-wrapped
        # labels report their sizeHint at their own natural width, which would
        # otherwise leave blank space once the dialog is widened to 600.
        layout.activate()
        h = layout.heightForWidth(600)
        if h > 0:
            self.setFixedHeight(h)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.PaletteChange:
            for w in self.findChildren(QLabel | QGroupBox):
                w.update()
        super().changeEvent(event)

    def _save(self):
        self._settings["start_minimized"] = self._start_minimized.isChecked()
        self._settings["autoplay_last"] = self._autoplay_last.isChecked()
        self._settings["station_limit"] = self._limit.currentData()
        self._settings["notifications"] = self._notifications.isChecked()
        self._settings["show_album_art"] = self._show_album_art.isChecked()
        self._settings.save()
        self.accept()

    def _on_clear_listening(self):
        confirm = QMessageBox.question(
            self,
            self.tr("Clear all listening time"),
            self.tr("Clear all listening time? This cannot be undone."),
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._listening_stats.clear()
        self.listening_cleared.emit()
        QMessageBox.information(
            self, self.tr("Listening time"), self.tr("All listening time cleared.")
        )

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Export backup"),
            _backup.default_export_name(),
            self.tr("Zip files (*.zip)"),
        )
        if not path:
            return
        try:
            _backup.export_backup(Path(path))
            QMessageBox.information(self, self.tr("Backup"), self.tr("Backup exported successfully."))
        except Exception as e:
            QMessageBox.critical(self, self.tr("Backup"), self.tr("Export failed: {0}").format(str(e)))

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Import backup"),
            "",
            self.tr("Zip files (*.zip)"),
        )
        if not path:
            return
        try:
            restored = _backup.import_backup(Path(path))
            QMessageBox.information(
                self,
                self.tr("Backup"),
                self.tr("Restored: {0}.\nRestart the app to apply changes.").format(", ".join(restored)),
            )
        except Exception as e:
            QMessageBox.critical(self, self.tr("Backup"), self.tr("Import failed: {0}").format(str(e)))

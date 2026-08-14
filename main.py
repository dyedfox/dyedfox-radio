import os
import sys

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

# Must be called before QApplication and any dbus connection.
try:
    import dbus.mainloop.glib
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
except ImportError:
    pass

from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QTranslator, QLocale, QThreadPool
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtGui import QIcon

from player.backend import GStreamerBackend
from player.mpris import setup_mpris
from api.radio_browser import RadioBrowserClient, shutdown as _shutdown_workers
from data.favourites import FavouritesManager, RecentManager
from data.listening_stats import ListeningStatsManager
from data.settings import Settings
from tray.tray_icon import SystemTrayIcon
from ui.main_window import MainWindow

_INSTANCE_KEY = "dyedfox-radio-instance"


def _try_raise_existing() -> bool:
    """Return True if another instance was found and signalled."""
    sock = QLocalSocket()
    sock.connectToServer(_INSTANCE_KEY)
    if sock.waitForConnected(500):
        sock.write(b"raise")
        sock.flush()
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return True
    return False


def _migrate_config():
    old = Path.home() / ".config" / "radiox"
    new = Path.home() / ".config" / "dyedfox-radio"
    if old.exists() and not new.exists():
        old.rename(new)


def main():
    _migrate_config()

    # GStreamer's audio sinks report this to PulseAudio/PipeWire as the client
    # name; without it KDE's volume mixer labels the stream by the process name
    # ("python3.14") instead of the app.
    GLib.set_prgname("dyedfox-radio")
    GLib.set_application_name("Dyedfox Radio")
    Gst.init(None)

    # Load settings before QApplication so we can suppress the KDE startup
    # notification when starting minimized. Qt reads DESKTOP_STARTUP_ID in
    # QApplication.__init__; removing it beforehand prevents KDE from showing
    # a launcher indicator that would linger alongside the tray icon.
    settings = Settings()
    if settings["start_minimized"]:
        os.environ.pop("DESKTOP_STARTUP_ID", None)

    app = QApplication(sys.argv)
    app.setApplicationName("Dyedfox Radio")
    app.setDesktopFileName("dyedfox-radio")
    app.setQuitOnLastWindowClosed(False)

    _translations_dir = Path(__file__).parent / "translations"
    _translator = QTranslator(app)
    _locale = QLocale.system().name()  # e.g. "uk_UA"
    if _translator.load(f"dyedfox-radio_{_locale}", str(_translations_dir)):
        app.installTranslator(_translator)

    if _try_raise_existing():
        sys.exit(0)

    _icon_path = str(Path(__file__).parent / "assets" / "icons" / "dyedfox-radio.png")
    app.setWindowIcon(QIcon(_icon_path))

    favourites = FavouritesManager()
    recent = RecentManager()
    listening_stats = ListeningStatsManager()
    backend = GStreamerBackend()
    api = RadioBrowserClient()

    window = MainWindow(backend, api, favourites, recent, settings, listening_stats)

    tray = SystemTrayIcon(window, backend)
    QTimer.singleShot(0, tray.show)
    window.set_tray(tray)

    mpris = setup_mpris(backend, window)
    window.set_mpris(mpris)

    server = QLocalServer()
    QLocalServer.removeServer(_INSTANCE_KEY)
    server.listen(_INSTANCE_KEY)

    def _on_new_connection():
        conn = server.nextPendingConnection()
        conn.waitForReadyRead(200)
        window.showNormal()
        window.raise_()
        window.activateWindow()
        conn.deleteLater()

    server.newConnection.connect(_on_new_connection)

    def _on_quit():
        listening_stats.stop()
        backend.shutdown()
        _shutdown_workers()
        window._station_list.cancel_pending_favicons()
        QThreadPool.globalInstance().clear()

    app.aboutToQuit.connect(_on_quit)

    if not settings["start_minimized"]:
        window.show()

    autoplay_uuid = recent.uuids()[0] if settings["autoplay_last"] and recent.uuids() else ""
    last_view = settings["last_view"]
    if last_view != "all":
        window._switch_view(last_view)
    if last_view == "all" or autoplay_uuid:
        window.load_top_stations(autoplay_uuid=autoplay_uuid)

    rc = app.exec()
    # The Qt loop has ended and aboutToQuit cleanup has run. GStreamer, PipeWire
    # and GLib spin up native threads that can deadlock Python's interpreter
    # shutdown, leaving the process resident after the tray icon disappears. All
    # state is persisted on change, so terminate immediately instead of risking
    # a hung finalization.
    os._exit(rc)


if __name__ == "__main__":
    main()

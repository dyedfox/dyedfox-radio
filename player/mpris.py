from PyQt6.QtCore import QObject, pyqtSignal, QTimer

try:
    import dbus
    import dbus.service
    from gi.repository import GLib
    _DBUS_OK = True
except ImportError:
    _DBUS_OK = False

_PROP = "org.freedesktop.DBus.Properties"
_MP2 = "org.mpris.MediaPlayer2"
_PLAYER = "org.mpris.MediaPlayer2.Player"
BUS_NAME = "org.mpris.MediaPlayer2.dyedfox_radio"
OBJ_PATH = "/org/mpris/MediaPlayer2"


class _Bridge(QObject):
    """Carries D-Bus method calls safely to the Qt main thread."""
    play_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    playpause_requested = pyqtSignal()
    volume_requested = pyqtSignal(int)
    raise_requested = pyqtSignal()
    quit_requested = pyqtSignal()


if _DBUS_OK:
    class _MprisObject(dbus.service.Object):
        def __init__(self, bus, bridge, backend):
            super().__init__(bus, OBJ_PATH)
            self._bridge = bridge
            self._backend = backend
            self._metadata = dbus.Dictionary(
                {"mpris:trackid": dbus.ObjectPath("/org/dyedfoxradio/track/0"),
                 "xesam:title": dbus.String(""),
                 "xesam:artist": dbus.Array([""], signature="s")},
                signature="sv",
            )

        @dbus.service.method(_MP2)
        def Raise(self): self._bridge.raise_requested.emit()

        @dbus.service.method(_MP2)
        def Quit(self): self._bridge.quit_requested.emit()

        @dbus.service.method(_PLAYER)
        def Play(self): self._bridge.play_requested.emit()

        @dbus.service.method(_PLAYER)
        def Pause(self): self._bridge.stop_requested.emit()

        @dbus.service.method(_PLAYER)
        def Stop(self): self._bridge.stop_requested.emit()

        @dbus.service.method(_PLAYER)
        def PlayPause(self): self._bridge.playpause_requested.emit()

        @dbus.service.method(_PROP, in_signature="ss", out_signature="v")
        def Get(self, iface, prop):
            return self._all_props(iface).get(prop, dbus.String(""))

        @dbus.service.method(_PROP, in_signature="s", out_signature="a{sv}")
        def GetAll(self, iface):
            return self._all_props(iface)

        @dbus.service.method(_PROP, in_signature="ssv")
        def Set(self, iface, prop, value):
            if iface == _PLAYER and prop == "Volume":
                self._bridge.volume_requested.emit(int(float(value) * 100))

        @dbus.service.signal(_PROP, signature="sa{sv}as")
        def PropertiesChanged(self, iface, changed, invalidated): pass

        def push_metadata(self, title: str, station: str, art_url: str):
            self._metadata = dbus.Dictionary(
                {"mpris:trackid": dbus.ObjectPath("/org/dyedfoxradio/track/1"),
                 "xesam:title": dbus.String(title),
                 "xesam:artist": dbus.Array([station], signature="s"),
                 "mpris:artUrl": dbus.String(art_url)},
                signature="sv",
            )
            has_url = self._backend.last_url is not None
            self.PropertiesChanged(
                _PLAYER,
                {
                    "Metadata": self._metadata,
                    "PlaybackStatus": self._status(),
                    # Per MPRIS spec these are capability flags, not state — keep
                    # them true whenever a station is loaded so consumers like the
                    # lock screen don't disable their controls. PlaybackStatus
                    # alone tells them which icon (play/pause) to show.
                    "CanPause": dbus.Boolean(has_url),
                    "CanPlay": dbus.Boolean(has_url),
                    "CanStop": dbus.Boolean(has_url),
                },
                [],
            )

        def push_status(self):
            has_url = self._backend.last_url is not None
            self.PropertiesChanged(_PLAYER, {
                "PlaybackStatus": self._status(),
                "CanPause": dbus.Boolean(has_url),
                "CanPlay": dbus.Boolean(has_url),
                "CanStop": dbus.Boolean(has_url),
            }, [])

        def _status(self) -> dbus.String:
            return dbus.String("Playing" if self._backend.is_playing else "Stopped")

        def _all_props(self, iface: str) -> dict:
            if iface == _MP2:
                return {
                    "Identity": dbus.String("Dyedfox Radio"),
                    "DesktopEntry": dbus.String("dyedfox-radio"),
                    "CanRaise": dbus.Boolean(True),
                    "CanQuit": dbus.Boolean(True),
                    "HasTrackList": dbus.Boolean(False),
                    "SupportedUriSchemes": dbus.Array([], signature="s"),
                    "SupportedMimeTypes": dbus.Array([], signature="s"),
                }
            if iface == _PLAYER:
                has_url = self._backend.last_url is not None
                return {
                    "PlaybackStatus": self._status(),
                    "CanGoNext": dbus.Boolean(False),
                    "CanGoPrevious": dbus.Boolean(False),
                    "CanPause": dbus.Boolean(has_url),
                    "CanPlay": dbus.Boolean(has_url),
                    "CanStop": dbus.Boolean(has_url),
                    "CanSeek": dbus.Boolean(False),
                    "CanControl": dbus.Boolean(True),
                    "Metadata": self._metadata,
                    # Required by the spec as int64 (µs). A live stream has no
                    # position, but without it Get() fell back to a string,
                    # which shells like Omarchy's failed to parse.
                    "Position": dbus.Int64(0),
                    "Volume": dbus.Double(1.0),
                    "Rate": dbus.Double(1.0),
                    "MinimumRate": dbus.Double(1.0),
                    "MaximumRate": dbus.Double(1.0),
                }
            return {}


class MprisPlayer:
    def __init__(self, obj: "_MprisObject", poller: QTimer, bridge: _Bridge):
        self._obj = obj
        self._poller = poller   # keep alive
        self._bridge = bridge   # keep alive

    def update_metadata(self, title: str, station: str, art_url: str = ""):
        self._obj.push_metadata(title, station, art_url)

    def update_playback_status(self):
        self._obj.push_status()


def setup_mpris(backend, window) -> "MprisPlayer | None":
    if not _DBUS_OK:
        print("dyedfox-radio: python-dbus not available, MPRIS disabled", flush=True)
        return None
    try:
        bus = dbus.SessionBus()
        bus.request_name(BUS_NAME)

        bridge = _Bridge()
        bridge.play_requested.connect(backend.play_last)
        bridge.stop_requested.connect(backend.stop)
        bridge.playpause_requested.connect(
            lambda: backend.stop() if backend.is_playing else backend.play_last()
        )
        bridge.volume_requested.connect(backend.set_volume)
        bridge.raise_requested.connect(lambda: (window.show(), window.raise_()))
        bridge.quit_requested.connect(
            __import__("PyQt6.QtWidgets", fromlist=["QApplication"]).QApplication.quit
        )

        obj = _MprisObject(bus, bridge, backend)

        # Poll the GLib default context from Qt's event loop.
        # This dispatches incoming D-Bus method calls (Play/Stop/etc.) without
        # a separate thread, keeping everything on the Qt main thread.
        context = GLib.MainContext.default()
        poller = QTimer()
        poller.timeout.connect(lambda: context.iteration(False) if context.pending() else None)
        poller.start(50)

        return MprisPlayer(obj, poller, bridge)
    except Exception as e:
        print(f"dyedfox-radio: MPRIS setup failed: {e}", flush=True)
        return None

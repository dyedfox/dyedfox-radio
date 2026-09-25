import threading

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class GStreamerBackend(QObject):
    metadata_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    playback_started = pyqtSignal()
    playback_stopped = pyqtSignal()
    reconnecting = pyqtSignal(int)  # attempt number (1-based)

    # Backoff schedule (ms) for automatic reconnection after a dropped stream.
    # Indexed by the current attempt; the final value is reused once exhausted.
    _RETRY_DELAYS_MS = (1000, 2000, 4000, 8000, 15000)
    _MAX_RETRIES = len(_RETRY_DELAYS_MS)
    # How long a stream must keep playing before we consider it healthy and
    # clear the retry counter. Guards against a station that connects then drops
    # immediately, which would otherwise reconnect forever.
    _STABLE_AFTER_MS = 10000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = Gst.ElementFactory.make("playbin", "player")
        if self._player is None:
            raise RuntimeError(
                "Failed to create GStreamer playbin element. "
                "Make sure gstreamer and gst-plugins-good are installed."
            )

        # pulsesink is preferred for compatibility; it works reliably with both
        # PulseAudio and PipeWire (via pipewire-pulse). pipewiresink can have
        # issues on some Fedora/Arch setups. autoaudiosink is the final fallback.
        audio_sink = None
        self._sink_factory = ""
        for sink_name in ("pulsesink", "pipewiresink", "autoaudiosink"):
            audio_sink = Gst.ElementFactory.make(sink_name, "audio_sink")
            if audio_sink is not None:
                self._tag_sink_for_mixer(audio_sink)
                self._player.set_property("audio-sink", audio_sink)
                self._sink_factory = sink_name
                break
        
        if audio_sink is None:
            raise RuntimeError(
                "Failed to create any GStreamer audio sink. Make sure the "
                "gstreamer audio plugins are installed (gst-plugins-good, "
                "gst-plugins-base, or gst-plugins-bad)."
            )

        self._audio_sink = audio_sink
        self._device_monitor: Gst.DeviceMonitor | None = None
        # Output device requested by the user ("" = system default); applied on
        # the worker thread since it needs the pipeline torn down (see below).
        self._output_device = ""
        self._pending_device: str | None = None
        self._devices: dict = {}

        self._bus = self._player.get_bus()
        self._last_url: str | None = None
        self._playing = False
        self._retry_count = 0
        self._reconnecting = False

        # Pipeline state changes block: set_state(NULL) joins the streaming
        # thread, which can stall for seconds on a slow/half-open network socket.
        # Running that on the GUI thread freezes the window, so a dedicated worker
        # performs every transition. PyGObject releases the GIL during the C call,
        # so the UI stays responsive. Only the most recent request matters
        # (latest-wins), which collapses a burst of clicks into one teardown.
        self._cmd: tuple[str, str | None] | None = None
        self._cmd_lock = threading.Lock()
        self._cmd_event = threading.Event()
        self._worker_stop = False
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._attempt_reconnect)

        self._stable_timer = QTimer(self)
        self._stable_timer.setSingleShot(True)
        self._stable_timer.timeout.connect(self._mark_stable)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_bus)
        self._timer.start(200)

    @staticmethod
    def _tag_sink_for_mixer(audio_sink):
        """Label the audio stream so KDE's volume mixer shows the app name and
        icon instead of the process name ("python3.14").

        Verified against pw-dump: plasma-pa shows a node's node.description /
        media.name (not application.name), and those default to the process name
        unless set. So the display-name fields go in 'stream-properties'.
        'client-properties' carries the client-level application.name (grouping),
        and pulsesink uses 'client-name' for the same. Skip absent properties."""
        if audio_sink.find_property("client-properties") is not None:
            # application.process.binary defaults to the interpreter ("python3.14")
            # and plasma-pa uses it as the client-name prefix, so override it too.
            cp = Gst.Structure.new_from_string(
                'client-properties,application.name="Dyedfox Radio",'
                'application.process.binary=dyedfox-radio,'
                'application.icon_name=dyedfox-radio'
            )
            if cp is not None:
                audio_sink.set_property("client-properties", cp)
        elif audio_sink.find_property("client-name") is not None:
            audio_sink.set_property("client-name", "Dyedfox Radio")
        if audio_sink.find_property("stream-properties") is not None:
            props = Gst.Structure.new_from_string(
                'stream-properties,media.role=music,'
                'application.name="Dyedfox Radio",application.icon_name=dyedfox-radio,'
                'media.name="Dyedfox Radio",node.name=dyedfox-radio,'
                'node.description="Dyedfox Radio"'
            )
            if props is not None:
                audio_sink.set_property("stream-properties", props)

    def _submit(self, action: str, url: str | None = None):
        # Hand a transition to the worker thread; latest request wins.
        with self._cmd_lock:
            self._cmd = (action, url)
            self._cmd_event.set()

    def _worker_loop(self):
        while True:
            self._cmd_event.wait()
            if self._worker_stop:
                self._player.set_state(Gst.State.NULL)
                return
            with self._cmd_lock:
                cmd = self._cmd
                self._cmd = None
                device = self._pending_device
                self._pending_device = None
                self._cmd_event.clear()
            if device is not None:
                # The sink only opens its device when it leaves NULL, so drop the
                # pipeline, retarget the sink, and resume if we were playing.
                self._player.set_state(Gst.State.NULL)
                self._apply_device(device)
                if cmd is None and self._playing and self._last_url:
                    cmd = ("resume", None)
            if cmd is None:
                continue
            action, url = cmd
            if action == "play":
                # Tear the old stream down, then point playbin at the new URL.
                self._player.set_state(Gst.State.NULL)
                self._player.set_property("uri", url)
                self._player.set_state(Gst.State.PLAYING)
            elif action == "resume":
                self._player.set_state(Gst.State.PLAYING)
            elif action == "stop":
                self._player.set_state(Gst.State.NULL)

    def play(self, url: str):
        self._reconnect_timer.stop()
        self._stable_timer.stop()
        self._retry_count = 0
        self._reconnecting = False
        self._playing = True
        self._last_url = url
        self._submit("play", url)
        self.playback_started.emit()

    def play_last(self):
        if self._last_url:
            self.play(self._last_url)

    def stop(self):
        self._reconnect_timer.stop()
        self._stable_timer.stop()
        self._retry_count = 0
        self._reconnecting = False
        self._playing = False
        self._submit("stop")
        self.playback_stopped.emit()

    def shutdown(self):
        # Full teardown for app exit: stop every timer so nothing fires during
        # shutdown, then tell the worker to drop the pipeline to NULL and exit.
        # Join briefly; if a transition is wedged on the network, the os._exit
        # backstop in main() guarantees the process still dies.
        self._timer.stop()
        self._reconnect_timer.stop()
        self._stable_timer.stop()
        self._playing = False
        self._reconnecting = False
        self._worker_stop = True
        self._cmd_event.set()
        self._worker.join(timeout=2)

    # --- Output device selection ---

    def _native_selection(self) -> bool:
        # pulsesink and pipewiresink can be pointed at a sink by name. Anything
        # else (autoaudiosink, older pipewiresink) gets its sink element swapped
        # for one built by the device itself (see _apply_device).
        if self._sink_factory == "pulsesink":
            return self._audio_sink.find_property("device") is not None
        if self._sink_factory == "pipewiresink":
            return self._audio_sink.find_property("target-object") is not None
        return False

    def list_output_devices(self) -> list[tuple[str, str]]:
        """Return (device_id, display_name) for each available audio output."""
        if self._device_monitor is None:
            self._device_monitor = Gst.DeviceMonitor.new()
            self._device_monitor.add_filter("Audio/Sink", None)
        devices = []
        found = {}
        # get_devices() probes the providers directly when the monitor isn't
        # started, so there's no need to keep a running monitor around.
        for device in self._device_monitor.get_devices() or []:
            device_id = self._device_id(device)
            if not device_id or device_id in found:
                continue
            found[device_id] = device
            devices.append((device_id, device.get_display_name() or device_id))
        # Kept so the worker can build a sink from the Gst.Device when the
        # current sink can't be retargeted by name. Replaced, never mutated.
        self._devices = found
        return devices

    @staticmethod
    def _device_id(device) -> str:
        # pulsedeviceprovider exposes the sink name as 'internal-name';
        # pipewiredeviceprovider puts it in the node.name property. Under
        # pipewire-pulse the two are the same string. Other providers (ALSA)
        # have neither, so fall back to the display name.
        if device.find_property("internal-name") is not None:
            name = device.get_property("internal-name")
            if name:
                return name
        props = device.get_properties()
        if props is not None:
            name = props.get_string("node.name")
            if name:
                return name
        return device.get_display_name() or ""

    def set_output_device(self, device_id: str):
        """Route audio to device_id ("" = system default). Takes effect
        immediately, briefly restarting the stream if one is playing."""
        if device_id == self._output_device:
            return
        self._output_device = device_id
        with self._cmd_lock:
            self._pending_device = device_id
            self._cmd_event.set()

    def _apply_device(self, device_id: str):
        # Worker thread only, with the pipeline in NULL.
        if self._native_selection():
            # None clears the target so the sound server uses its default.
            value = device_id or None
            if self._sink_factory == "pulsesink":
                self._audio_sink.set_property("device", value)
            else:
                self._audio_sink.set_property("target-object", value)
            return
        if device_id:
            device = self._devices.get(device_id)
            sink = device.create_element(None) if device is not None else None
        else:
            sink = Gst.ElementFactory.make(self._sink_factory, None)
        if sink is None:
            print(f"dyedfox-radio: could not open audio output {device_id!r}", flush=True)
            return
        self._tag_sink_for_mixer(sink)
        self._player.set_property("audio-sink", sink)
        self._audio_sink = sink

    def set_volume(self, value: int):
        self._player.set_property("volume", max(0, min(100, value)) / 100.0)

    def set_muted(self, muted: bool):
        self._player.set_property("mute", muted)

    @property
    def is_muted(self) -> bool:
        return bool(self._player.get_property("mute"))

    @property
    def is_playing(self) -> bool:
        # Reflects intent, set synchronously in play()/stop(). Querying the
        # pipeline (get_state) races the asynchronous PLAYING transition and can
        # report "not playing" for a moment right after resuming, which leaves
        # MPRIS/tray controls stuck in the stopped state.
        return self._playing

    @property
    def last_url(self) -> str | None:
        return self._last_url

    def _handle_disconnect(self, reason: str):
        # A live radio stream dropped: a clean server disconnect (EOS), a network
        # blip, or an audio device switch (e.g. Bluetooth). Reconnect with
        # backoff before giving up so transient drops recover on their own.
        self._stable_timer.stop()
        if self._last_url and self._retry_count < self._MAX_RETRIES:
            delay = self._RETRY_DELAYS_MS[self._retry_count]
            self._retry_count += 1
            self._reconnecting = True
            self.reconnecting.emit(self._retry_count)
            self._reconnect_timer.start(delay)
        else:
            self._submit("stop")
            self._retry_count = 0
            self._reconnecting = False
            self._playing = False
            self.playback_stopped.emit()
            self.error_occurred.emit(reason)

    def _attempt_reconnect(self):
        if not self._last_url:
            return
        # Teardown + restart happens on the worker thread (see _worker_loop).
        self._submit("play", self._last_url)
        # Retry counter resets only once playback has been stable for a while
        # (see _mark_stable), not the moment we connect.

    def _mark_stable(self):
        self._retry_count = 0

    def _poll_bus(self):
        while True:
            msg = self._bus.pop()
            if msg is None:
                break
            if msg.type == Gst.MessageType.ERROR:
                err, _ = msg.parse_error()
                self._handle_disconnect(str(err))
            elif msg.type == Gst.MessageType.EOS:
                # Server closed the connection cleanly; for live radio this is a
                # dropout, not a real end, so reconnect rather than going silent.
                self._handle_disconnect("Stream ended")
            elif msg.type == Gst.MessageType.STREAM_START:
                if self._reconnecting:
                    self._reconnecting = False
                    self.playback_started.emit()
                self._stable_timer.start(self._STABLE_AFTER_MS)
            elif msg.type == Gst.MessageType.TAG:
                tags = msg.parse_tag()
                ok, title = tags.get_string(Gst.TAG_TITLE)
                if ok and title:
                    self.metadata_changed.emit(title)

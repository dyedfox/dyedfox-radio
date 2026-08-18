import json
from pathlib import Path

_CONFIG = Path.home() / ".config" / "dyedfox-radio"
_FILE = _CONFIG / "settings.json"

DEFAULTS: dict = {
    "start_minimized": False,
    "autoplay_last": False,
    "volume": 80,
    "station_limit": 100,
    "notifications": True,
    "show_album_art": True,
    "last_view": "all",
    "labels_expanded": True,
    "sidebar_width": 168,
    "window_size": [960, 620],
    "sort": {
        "all":        {"field": "votes", "ascending": False},
        "favourites": {"field": "name",  "ascending": True},
        "custom":     {"field": "name",  "ascending": True},
        "recent":     {"field": "last_listened", "ascending": False},
    },
}


class Settings:
    def __init__(self):
        _CONFIG.mkdir(parents=True, exist_ok=True)
        self._data = dict(DEFAULTS)
        try:
            saved = json.loads(_FILE.read_text())
            self._data.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except Exception:
            pass

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def save(self):
        try:
            _FILE.write_text(json.dumps(self._data, indent=2))
        except Exception as e:
            print(f"dyedfox-radio: failed to save settings: {e}", flush=True)

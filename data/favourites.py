from __future__ import annotations
import json
from pathlib import Path

_CONFIG = Path.home() / ".config" / "dyedfox-radio"
_FAV_FILE = _CONFIG / "favourites.json"
_FAV_CACHE_FILE = _CONFIG / "favourites_cache.json"
_LABELS_FILE = _CONFIG / "favourite_labels.json"
_RECENT_FILE = _CONFIG / "recent.json"
_RECENT_CACHE_FILE = _CONFIG / "recent_cache.json"
_NEW_CACHE_FILE = _CONFIG / "new_cache.json"
_TRENDING_CACHE_FILE = _CONFIG / "trending_cache.json"
_RANDOM_CACHE_FILE = _CONFIG / "random_cache.json"
_NOW_LISTENING_CACHE_FILE = _CONFIG / "now_listening_cache.json"


class FavouritesManager:
    def __init__(self):
        _CONFIG.mkdir(parents=True, exist_ok=True)
        self._uuids: set[str] = set(self._load(_FAV_FILE))
        self._labels: dict[str, list[str]] = self._load_labels()

    def is_favourite(self, uuid: str) -> bool:
        return uuid in self._uuids

    def set(self, uuid: str, is_fav: bool):
        if is_fav:
            self._uuids.add(uuid)
        else:
            self._uuids.discard(uuid)
            # Labels only make sense for current favourites; drop them so an
            # unfavourited-then-refavourited station doesn't resurface stale tags.
            if self._labels.pop(uuid, None) is not None:
                self._save_labels()
        self._save(_FAV_FILE, list(self._uuids))

    def uuids(self) -> set[str]:
        return set(self._uuids)

    def labels_for(self, uuid: str) -> list[str]:
        return list(self._labels.get(uuid, []))

    def has_label(self, uuid: str, label: str) -> bool:
        return label in self._labels.get(uuid, [])

    def set_label(self, uuid: str, label: str, on: bool):
        labels = self._labels.setdefault(uuid, [])
        if on:
            if label not in labels:
                labels.append(label)
        else:
            if label in labels:
                labels.remove(label)
            if not labels:
                self._labels.pop(uuid, None)
        self._save_labels()

    def rename_label(self, old: str, new: str):
        # Renaming onto an existing label merges the two.
        for labels in self._labels.values():
            if old in labels:
                if new in labels:
                    labels.remove(old)
                else:
                    labels[labels.index(old)] = new
        self._save_labels()

    def delete_label(self, label: str):
        # Stations stay favourited; they only lose this label.
        for uuid in [u for u, labels in self._labels.items() if label in labels]:
            self._labels[uuid].remove(label)
            if not self._labels[uuid]:
                del self._labels[uuid]
        self._save_labels()

    def all_labels(self) -> list[str]:
        names: set[str] = set()
        for labels in self._labels.values():
            names.update(labels)
        return sorted(names, key=str.lower)

    def uuids_for_label(self, label: str) -> set[str]:
        return {uuid for uuid, labels in self._labels.items() if label in labels}

    def _load_labels(self) -> dict:
        try:
            return json.loads(_LABELS_FILE.read_text())
        except Exception:
            return {}

    def _save_labels(self):
        try:
            _LABELS_FILE.write_text(json.dumps(self._labels, indent=2))
        except Exception as e:
            print(f"dyedfox-radio: failed to save favourite labels: {e}", flush=True)

    def cached_stations(self) -> list[dict]:
        try:
            data = json.loads(_FAV_CACHE_FILE.read_text())
            return [s for s in data if s.get("stationuuid") in self._uuids]
        except Exception:
            return []

    def cache_stations(self, stations: list[dict]):
        try:
            _FAV_CACHE_FILE.write_text(json.dumps(stations, indent=2))
        except Exception as e:
            print(f"dyedfox-radio: failed to save favourites cache: {e}", flush=True)

    @staticmethod
    def _load(path: Path) -> list:
        try:
            return json.loads(path.read_text())
        except Exception:
            return []

    @staticmethod
    def _save(path: Path, data: list):
        try:
            path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"dyedfox-radio: failed to save {path}: {e}", flush=True)


class StationsCache:
    def __init__(self, path: Path):
        _CONFIG.mkdir(parents=True, exist_ok=True)
        self._path = path

    def load(self) -> list[dict]:
        try:
            return json.loads(self._path.read_text())
        except Exception:
            return []

    def save(self, stations: list[dict]):
        try:
            self._path.write_text(json.dumps(stations))
        except Exception as e:
            print(f"dyedfox-radio: failed to save {self._path.name}: {e}", flush=True)


new_cache = StationsCache(_NEW_CACHE_FILE)
trending_cache = StationsCache(_TRENDING_CACHE_FILE)
random_cache = StationsCache(_RANDOM_CACHE_FILE)
now_listening_cache = StationsCache(_NOW_LISTENING_CACHE_FILE)


class RecentManager:
    MAX = 20

    def __init__(self):
        _CONFIG.mkdir(parents=True, exist_ok=True)
        self._uuids: list[str] = self._load()

    def add(self, uuid: str):
        try:
            self._uuids.remove(uuid)
        except ValueError:
            pass
        self._uuids.insert(0, uuid)
        self._uuids = self._uuids[: self.MAX]
        self._save()

    def uuids(self) -> list[str]:
        return list(self._uuids)

    def remove(self, uuid: str):
        try:
            self._uuids.remove(uuid)
        except ValueError:
            return
        self._save()

    def clear(self):
        self._uuids = []
        self._save()

    def cached_stations(self) -> list[dict]:
        # Resolved station details from a previous load, filtered to the current
        # history and returned in history order. Lets the History view render
        # offline / when the API hiccups instead of showing a retry banner.
        try:
            data = json.loads(_RECENT_CACHE_FILE.read_text())
            by_uuid = {s.get("stationuuid"): s for s in data}
            return [by_uuid[u] for u in self._uuids if u in by_uuid]
        except Exception:
            return []

    def cache_stations(self, stations: list[dict]):
        try:
            _RECENT_CACHE_FILE.write_text(json.dumps(stations, indent=2))
        except Exception as e:
            print(f"dyedfox-radio: failed to save recent cache: {e}", flush=True)

    def _load(self) -> list:
        try:
            return json.loads(_RECENT_FILE.read_text())
        except Exception:
            return []

    def _save(self):
        try:
            _RECENT_FILE.write_text(json.dumps(self._uuids, indent=2))
        except Exception as e:
            print(f"dyedfox-radio: failed to save recent: {e}", flush=True)

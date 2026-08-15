from __future__ import annotations
import json
import zipfile
from pathlib import Path
from datetime import datetime

_CONFIG = Path.home() / ".config" / "dyedfox-radio"
_FILES = [
    "favourites.json", "favourites_cache.json", "favourite_labels.json",
    "recent.json", "recent_cache.json", "settings.json",
    "custom_stations.json", "listening_time.json",
]


def export_backup(path: Path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in _FILES:
            f = _CONFIG / name
            if f.exists():
                zf.write(f, name)


def import_backup(path: Path) -> list[str]:
    restored = []
    with zipfile.ZipFile(path, "r") as zf:
        for name in zf.namelist():
            if name in _FILES:
                zf.extract(name, _CONFIG)
                restored.append(name)
    return restored


def default_export_name() -> str:
    return f"dyedfox-radio-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"

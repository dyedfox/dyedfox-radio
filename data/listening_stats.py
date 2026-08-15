from __future__ import annotations
import json
import time
from pathlib import Path

_CONFIG = Path.home() / ".config" / "dyedfox-radio"
_STATS_FILE = _CONFIG / "listening_time.json"


def format_duration(seconds: float) -> str:
    total_minutes = int(seconds) // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return "<1m"


class ListeningStatsManager:
    """Tracks cumulative time spent actually playing each station, keyed by
    stationuuid. The app quits via os._exit() (see main.py) rather than a
    graceful shutdown, so the running segment is checkpointed periodically
    (via flush()) instead of relying on a save-on-exit hook."""

    def __init__(self):
        _CONFIG.mkdir(parents=True, exist_ok=True)
        self._totals: dict[str, float] = self._load()
        self._active_uuid: str | None = None
        self._segment_start: float | None = None

    def start(self, uuid: str):
        if not uuid or uuid == self._active_uuid:
            return
        self.stop()
        self._active_uuid = uuid
        self._segment_start = time.monotonic()

    def stop(self):
        """End the in-progress segment (if any), folding its time into the total."""
        self._checkpoint()
        self._active_uuid = None
        self._segment_start = None

    def flush(self):
        """Fold the in-progress segment's elapsed time into the total so far
        without ending it — a periodic checkpoint against an unclean exit."""
        self._checkpoint()

    def _checkpoint(self):
        if self._active_uuid is None or self._segment_start is None:
            return
        now = time.monotonic()
        elapsed = now - self._segment_start
        self._segment_start = now
        if elapsed <= 0:
            return
        self._totals[self._active_uuid] = self._totals.get(self._active_uuid, 0.0) + elapsed
        self._save()

    def total_seconds(self, uuid: str) -> float:
        total = self._totals.get(uuid, 0.0)
        if uuid == self._active_uuid and self._segment_start is not None:
            total += time.monotonic() - self._segment_start
        return total

    def has_any(self) -> bool:
        """True if there is any recorded time to clear — either saved totals or
        an in-progress segment currently accruing."""
        return bool(self._totals) or self._active_uuid is not None

    def remove(self, uuid: str):
        """Forget the accumulated time for one station. Drops the in-progress
        segment too if it belongs to that station, so it can't be folded back in."""
        if uuid == self._active_uuid:
            self._active_uuid = None
            self._segment_start = None
        if self._totals.pop(uuid, None) is not None:
            self._save()

    def clear(self):
        """Forget all accumulated time and any in-progress segment."""
        self._active_uuid = None
        self._segment_start = None
        self._totals = {}
        self._save()

    def _load(self) -> dict:
        try:
            return json.loads(_STATS_FILE.read_text())
        except Exception:
            return {}

    def _save(self):
        try:
            _STATS_FILE.write_text(json.dumps(self._totals, indent=2))
        except Exception as e:
            print(f"dyedfox-radio: failed to save listening stats: {e}", flush=True)

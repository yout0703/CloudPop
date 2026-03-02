"""Persistent state for STRM generator: path map + last-scan timestamps."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_STATE_PATH = Path.home() / ".cloudpop" / "state.json"


class GeneratorState:
    """Loads / saves cid→path mapping and per-cloud-path scan timestamps."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._path = state_path or _DEFAULT_STATE_PATH
        self._data: dict = self._load()

    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning("Failed to load state file %s: %s", self._path, exc)
        return {"cid_path_map": {}, "last_scan": {}}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # cid → path map
    # ------------------------------------------------------------------

    def get_path_map(self) -> dict[str, str]:
        return self._data.setdefault("cid_path_map", {})

    def update_path_map(self, mapping: dict[str, str]) -> None:
        self._data.setdefault("cid_path_map", {}).update(mapping)

    def get_cached_path(self, cid: str) -> str | None:
        return self._data.get("cid_path_map", {}).get(cid)

    # ------------------------------------------------------------------
    # Last-scan timestamp per cloud path
    # ------------------------------------------------------------------

    def get_last_scan(self, cloud_path: str) -> int:
        return int(self._data.get("last_scan", {}).get(cloud_path, 0))

    def set_last_scan(self, cloud_path: str, ts: int) -> None:
        self._data.setdefault("last_scan", {})[cloud_path] = ts

"""TTL-based in-memory cache backed by cachetools."""

from __future__ import annotations

import time
from threading import Lock
from typing import Optional

from cachetools import TTLCache


class CacheManager:
    """Thread-safe in-process TTL cache.

    Two logical buckets share one underlying TTLCache keyed with a prefix:
      ``dl:{pickcode}``  – download URLs (shorter TTL)
      ``fi:{file_id}``   – FileInfo JSON   (longer TTL)

    Both buckets use the *default* TTL set at construction time.  Callers
    can override TTL per-item through the ``ttl`` parameter of ``set()``.

    Because cachetools.TTLCache does not support per-item TTL natively, we
    store ``(value, expires_at)`` tuples and check expiry on ``get()``.
    """

    def __init__(self, maxsize: int = 4096, default_ttl: int = 3600) -> None:
        self._lock = Lock()
        # Large maxsize; items evict by time anyway.
        self._store: TTLCache = TTLCache(maxsize=maxsize, ttl=default_ttl)
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                self._store.pop(key, None)
                self._misses += 1
                return None
            self._hits += 1
            return value

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        effective_ttl = ttl if ttl is not None else self._default_ttl
        expires_at = time.monotonic() + effective_ttl
        with self._lock:
            # Re-create cache entry; TTLCache will evict at its own schedule,
            # but we double-check with our stored expires_at on get().
            self._store[key] = (value, expires_at)

    def delete(self, key: str) -> bool:
        with self._lock:
            existed = key in self._store
            self._store.pop(key, None)
            return existed

    def clear(self) -> int:
        with self._lock:
            count = len(self._store)
            self._store.clear()
            self._hits = 0
            self._misses = 0
            return count

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total else 0.0
            return {
                "size": len(self._store),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 4),
            }


# Module-level singleton.
_cache: CacheManager | None = None


def get_cache(maxsize: int = 4096, default_ttl: int = 3600) -> CacheManager:
    global _cache
    if _cache is None:
        _cache = CacheManager(maxsize=maxsize, default_ttl=default_ttl)
    return _cache


def reset_cache() -> None:
    """Force recreation (used in tests)."""
    global _cache
    _cache = None

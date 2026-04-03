"""Simple in-memory TTL cache for deduplication and rate limit tracking."""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """Dictionary-based cache with per-key time-to-live expiration."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        """Return cached value or None if missing / expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        """Store *value* under *key* with a TTL."""
        self._store[key] = (value, time.monotonic() + ttl_seconds)

    def has(self, key: str) -> bool:
        """Return True if *key* exists and is not expired."""
        return self.get(key) is not None

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def cleanup(self) -> int:
        """Remove all expired entries. Returns count of removed items."""
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]
        return len(expired)

    @property
    def size(self) -> int:
        return len(self._store)

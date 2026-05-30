"""AI result cache (SPEC 05 §4.5).

Keyed by ``sha256(task | prompt_version | normalized_input)`` — the model is
**not** part of the key, so an identical re-import returns the previously
computed final result (including any escalation outcome) without any provider
call. This is the second-largest cost saving after escalation avoidance.

Two backends ship here: an in-process LRU (default, thread-safe) and a
``NullCache`` (disabled). A Mongo-backed TTL cache can be slotted in later via
the same :class:`CacheBackend` protocol without touching the router.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Protocol

from config import settings


def make_cache_key(task: str, prompt_version: str, payload: str) -> str:
    """Stable key over (task, prompt version, normalized input payload)."""
    digest = hashlib.sha256()
    digest.update(task.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(prompt_version.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(payload.encode("utf-8"))
    return digest.hexdigest()


class CacheBackend(Protocol):
    """Minimal cache contract used by the router."""

    def get(self, key: str) -> dict | None: ...

    def put(self, key: str, value: dict) -> None: ...


class NullCache:
    """No-op backend (caching disabled)."""

    def get(self, key: str) -> dict | None:
        return None

    def put(self, key: str, value: dict) -> None:
        return None


class InMemoryLRUCache:
    """Thread-safe bounded LRU cache (process-local)."""

    def __init__(self, maxsize: int = 4096) -> None:
        self._maxsize = max(1, maxsize)
        self._store: "OrderedDict[str, dict]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        with self._lock:
            value = self._store.get(key)
            if value is not None:
                self._store.move_to_end(key)
            return value

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            self._store[key] = value
            self._store.move_to_end(key)
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)

    def __len__(self) -> int:  # test/debug convenience
        with self._lock:
            return len(self._store)


def default_cache() -> CacheBackend:
    """Backend chosen by config (``AI_CACHE_ENABLED``)."""
    if settings.ai_cache_enabled:
        return InMemoryLRUCache()
    return NullCache()

"""In-process cache with TTL and on-disk persistence.

Every external provider call goes through this layer so a demo never repeats a
slow request and so repeated analyses stay cheap and reproducible.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ..config import settings

_memory: dict[str, tuple[float, Any]] = {}


def _path_for(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return settings.cache_dir / f"{digest}.json"


def get(key: str, ttl_seconds: int | None = None) -> Any | None:
    ttl = settings.cache_ttl_seconds if ttl_seconds is None else ttl_seconds
    now = time.time()

    if key in _memory:
        stored_at, value = _memory[key]
        if now - stored_at <= ttl:
            return value
        _memory.pop(key, None)

    path = _path_for(key)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if now - payload.get("stored_at", 0) <= ttl:
            _memory[key] = (payload["stored_at"], payload["value"])
            return payload["value"]
    return None


def set(key: str, value: Any) -> None:
    now = time.time()
    _memory[key] = (now, value)
    path = _path_for(key)
    try:
        path.write_text(
            json.dumps({"stored_at": now, "key": key, "value": value}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def clear() -> None:
    _memory.clear()
    for path in settings.cache_dir.glob("*.json"):
        try:
            path.unlink()
        except OSError:
            pass

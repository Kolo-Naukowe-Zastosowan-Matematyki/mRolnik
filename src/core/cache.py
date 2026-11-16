"""Bezpieczny, wątkowo-bezpieczny cache z TTL na SQLite (JSON, bez pickle).

Użycie:
    from src.core.cache import TTLCache
    c = TTLCache("weather", max_entries=5000)
    v = c.get(key)            # None gdy brak / wygasło
    c.set(key, value, ttl=1800)
    v = c.get_or_set(key, lambda: fetch(), ttl=600)
Wartości muszą być serializowalne do JSON. Każda przestrzeń nazw = osobna tabela w jednym pliku.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from src.core.config import settings

_DB_PATH: Path = settings.cache_dir / "mrolnik_cache.sqlite3"
_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        _CONN = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=10)
        _CONN.execute("PRAGMA journal_mode=WAL")
        _CONN.execute("PRAGMA synchronous=NORMAL")
    return _CONN


class TTLCache:
    def __init__(self, namespace: str, max_entries: int = 10000, default_ttl: float = 3600):
        if not namespace.isidentifier():
            raise ValueError("namespace must be an identifier")
        self.table = f"c_{namespace}"
        self.max_entries = max_entries
        self.default_ttl = default_ttl
        with _LOCK:
            _conn().execute(
                f"CREATE TABLE IF NOT EXISTS {self.table} (k TEXT PRIMARY KEY, v TEXT NOT NULL, exp REAL NOT NULL, ts REAL NOT NULL)"
            )
            _conn().commit()

    def get(self, key: str) -> Any:
        now = time.time()
        with _LOCK:
            row = _conn().execute(f"SELECT v, exp FROM {self.table} WHERE k=?", (key,)).fetchone()
        if not row:
            return None
        if row[1] < now:
            self.delete(key)
            return None
        return json.loads(row[0])

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        now = time.time()
        payload = json.dumps(value, ensure_ascii=False, default=str)
        with _LOCK:
            c = _conn()
            c.execute(
                f"INSERT OR REPLACE INTO {self.table} (k, v, exp, ts) VALUES (?, ?, ?, ?)",
                (key, payload, now + (ttl if ttl is not None else self.default_ttl), now),
            )
            # sprzątanie: wygasłe + limit wpisów (najstarsze wylatują)
            c.execute(f"DELETE FROM {self.table} WHERE exp < ?", (now,))
            n = c.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0]
            if n > self.max_entries:
                c.execute(
                    f"DELETE FROM {self.table} WHERE k IN (SELECT k FROM {self.table} ORDER BY ts ASC LIMIT ?)",
                    (n - self.max_entries,),
                )
            c.commit()

    def delete(self, key: str) -> None:
        with _LOCK:
            _conn().execute(f"DELETE FROM {self.table} WHERE k=?", (key,))
            _conn().commit()

    def clear(self) -> None:
        with _LOCK:
            _conn().execute(f"DELETE FROM {self.table}")
            _conn().commit()

    def get_or_set(self, key: str, factory: Callable[[], Any], ttl: Optional[float] = None) -> Any:
        v = self.get(key)
        if v is not None:
            return v
        v = factory()
        if v is not None:
            self.set(key, v, ttl)
        return v

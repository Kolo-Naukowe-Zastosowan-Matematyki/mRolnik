"""Ogranicznik żądań "token bucket" w pamięci procesu (bez zależności zewnętrznych)."""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Tuple


class TokenBucketLimiter:
    def __init__(self, max_keys: int = 50_000, idle_s: float = 900.0):
        self._buckets: Dict[str, List[float]] = {}  # klucz -> [tokeny, ostatnia_aktualizacja]
        self._lock = threading.Lock()
        self._max_keys, self._idle = max_keys, idle_s

    def take(self, key: str, capacity: float, rate: float, cost: float = 1.0) -> Tuple[bool, float]:
        """Pobiera token. Zwraca (czy_dozwolone, sekundy_do_ponowienia)."""
        now = time.monotonic()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                if len(self._buckets) >= self._max_keys:
                    self._sweep(now)
                b = self._buckets[key] = [capacity, now]
            b[0] = min(capacity, b[0] + (now - b[1]) * rate)
            b[1] = now
            if b[0] >= cost:
                b[0] -= cost
                return True, 0.0
            return False, (cost - b[0]) / rate if rate > 0 else 3600.0

    def _sweep(self, now: float) -> None:
        stale = [k for k, (_, ts) in self._buckets.items() if now - ts > self._idle]
        for k in stale:
            del self._buckets[k]
        if len(self._buckets) >= self._max_keys:  # nadal pełno: usuń najstarsze
            for k, _ in sorted(self._buckets.items(), key=lambda kv: kv[1][1])[: self._max_keys // 10]:
                del self._buckets[k]

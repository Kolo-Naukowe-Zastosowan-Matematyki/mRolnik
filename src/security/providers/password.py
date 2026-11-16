"""Konta lokalne z hasłem (hash scrypt w env) i ochrona przed zgadywaniem haseł."""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Dict, Iterable, List

from src.security.config import PasswordUser
from src.security.passwords import DUMMY_HASH, verify_password
from src.security.providers.base import AuthError, AuthProvider, User

log = logging.getLogger(__name__)
audit = logging.getLogger("mrolnik.audit")


class LoginThrottle:
    """Po ``free_attempts`` nieudanych próbach blokada rośnie wykładniczo (30 s, 60 s, ... maks. 15 min).
    Liczniki zerują się po ``window_s`` bez prób. Stan w pamięci procesu."""

    def __init__(self, free_attempts: int = 5, base_s: float = 30.0, max_s: float = 900.0, window_s: float = 900.0):
        self.free, self.base, self.max, self.window = free_attempts, base_s, max_s, window_s
        self._state: Dict[str, List[float]] = {}  # klucz -> [liczba_porażek, ostatnia_próba, zablokowane_do]
        self._lock = threading.Lock()

    def retry_after(self, keys: Iterable[str]) -> int:
        now = time.time()
        with self._lock:
            wait = max((st[2] - now for k in keys if (st := self._state.get(k))), default=0.0)
        return math.ceil(wait) if wait > 0 else 0

    def failure(self, keys: Iterable[str]) -> None:
        now = time.time()
        with self._lock:
            if len(self._state) > 50_000:
                self._state = {k: v for k, v in self._state.items() if now - v[1] < self.window}
            for k in keys:
                st = self._state.get(k)
                if not st or now - st[1] > self.window:
                    st = [0.0, now, 0.0]
                st[0] += 1
                st[1] = now
                if st[0] >= self.free:
                    st[2] = now + min(self.base * 2 ** (st[0] - self.free), self.max)
                self._state[k] = st

    def success(self, keys: Iterable[str]) -> None:
        with self._lock:
            for k in keys:
                self._state.pop(k, None)


class PasswordProvider(AuthProvider):
    name = "password"
    interactive_form = True

    def __init__(self, users: Dict[str, PasswordUser], throttle: LoginThrottle | None = None):
        self.users = users
        self.throttle = throttle or LoginThrottle()

    def authenticate(self, login: str, password: str, client_ip: str) -> User:
        login_key = (login or "").strip().lower()[:64]
        keys = [f"u:{login_key}", f"ip:{client_ip}"]
        wait = self.throttle.retry_after(keys)
        if wait:
            audit.warning("logowanie zablokowane (backoff) ip=%s", client_ip)
            raise AuthError(f"Zbyt wiele nieudanych prób. Spróbuj ponownie za {math.ceil(wait / 60)} min.", retry_after=wait)
        account = self.users.get(login_key)
        ok = verify_password(password or "", account.password_hash if account else DUMMY_HASH)
        if not (account and ok):
            self.throttle.failure(keys)
            audit.warning("logowanie nieudane login=%s ip=%s", account.login if account else "<nieznany>", client_ip)
            raise AuthError("Nieprawidłowy login lub hasło.")
        self.throttle.success([f"u:{login_key}"])
        audit.info("logowanie udane user=%s rola=%s ip=%s", account.login, account.role, client_ip)
        return User(id=f"local:{account.login}", name=account.name or account.login, role=account.role)

"""Podpisane ciasteczka (HMAC-SHA256, bez zależności) oraz token CSRF."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any, Dict, Optional

SESSION_COOKIE = "mrolnik_session"
CSRF_COOKIE = "mrolnik_csrf"
OIDC_FLOW_COOKIE = "mrolnik_oidc"
CSRF_HEADER = "x-csrf-token"


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Signer:
    """Serializuje słownik do ``payload.podpis``; ``purpose`` rozdziela klucze dla różnych ciasteczek."""

    def __init__(self, secret: bytes, purpose: str):
        self._key = hmac.new(secret, b"mrolnik:" + purpose.encode(), hashlib.sha256).digest()

    def _sig(self, payload: str) -> str:
        return _b64e(hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest())

    def dumps(self, data: Dict[str, Any], ttl_s: int) -> str:
        body = dict(data, exp=int(time.time()) + int(ttl_s))
        payload = _b64e(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        return f"{payload}.{self._sig(payload)}"

    def loads(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not token or len(token) > 4096 or token.count(".") != 1:
            return None
        payload, sig = token.split(".")
        try:
            if not hmac.compare_digest(sig, self._sig(payload)):
                return None
            data = json.loads(_b64d(payload))
        except (ValueError, UnicodeError):
            return None
        if not isinstance(data, dict) or int(data.get("exp", 0)) < time.time():
            return None
        return data


class RevokedSessions:
    """Unieważnione (wylogowane) identyfikatory sesji – w pamięci, do czasu ich wygaśnięcia."""

    def __init__(self, max_items: int = 100_000):
        self._items: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._max = max_items

    def revoke(self, sid: str, exp: float) -> None:
        with self._lock:
            now = time.time()
            if len(self._items) >= self._max:
                self._items = {k: v for k, v in self._items.items() if v > now}
            self._items[sid] = exp

    def is_revoked(self, sid: str) -> bool:
        with self._lock:
            return sid in self._items


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def new_session_id() -> str:
    return secrets.token_urlsafe(18)


def cookie_header(name: str, value: str, *, max_age: Optional[int], secure: bool, http_only: bool,
                  same_site: str = "Lax", path: str = "/") -> str:
    """Wartość nagłówka Set-Cookie (używana także w middleware ASGI)."""
    parts = [f"{name}={value}", f"Path={path}", f"SameSite={same_site}"]
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
        if max_age <= 0:
            parts.append("Expires=Thu, 01 Jan 1970 00:00:00 GMT")
    if secure:
        parts.append("Secure")
    if http_only:
        parts.append("HttpOnly")
    return "; ".join(parts)

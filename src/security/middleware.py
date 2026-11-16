"""Middleware ASGI: nagłówki bezpieczeństwa, log dostępu i błędów, limit żądań, limit body, CSRF.

Czyste ASGI (bez BaseHTTPMiddleware), żeby nie buforować odpowiedzi i poprawnie obsłużyć strumienie.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import math
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional
from urllib.parse import urlparse

from starlette.datastructures import MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse

from src.security.config import SecurityConfig
from src.security.ratelimit import TokenBucketLimiter
from src.security.session import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, cookie_header, new_csrf_token

Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Dict[str, Any]]]
Send = Callable[[Dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

access_log = logging.getLogger("mrolnik.access")
audit = logging.getLogger("mrolnik.audit")
log = logging.getLogger(__name__)

DEFAULT_IMG_HOSTS = (
    "https://mapy.geoportal.gov.pl",
    "https://integracja.gugik.gov.pl",
    "https://*.tile.openstreetmap.org",
    "https://tile.openstreetmap.org",
)


def build_csp(extra_img_hosts=()) -> str:
    """CSP z kontraktu; ``extra_img_hosts`` (MROLNIK_CSP_IMG_HOSTS) dopisywane do img-src (np. kolejne WMS)."""
    hosts = list(DEFAULT_IMG_HOSTS) + [h for h in extra_img_hosts if h not in DEFAULT_IMG_HOSTS]
    return (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        f"img-src 'self' data: blob: {' '.join(hosts)}; "
        "connect-src 'self'; font-src 'self'; frame-ancestors 'self' https://erolnik.gov.pl https://*.gov.pl; "
        "base-uri 'self'; form-action 'self'; object-src 'none'"
    )


CSP = build_csp()
PERMISSIONS_POLICY = "geolocation=(self), camera=(), microphone=()"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
INTERNAL_ERROR = "Wystąpił wewnętrzny błąd serwera. Spróbuj ponownie później."


def _header(scope: Scope, name: bytes) -> str:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return ""


def _cookies(scope: Scope) -> Dict[str, str]:
    raw = _header(scope, b"cookie")
    return cookie_parser(raw) if raw else {}


def client_ip(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else "-"


async def _json(scope: Scope, receive: Receive, send: Send, status: int, detail: str,
                headers: Optional[Dict[str, str]] = None, **extra: Any) -> None:
    await JSONResponse({"detail": detail, **extra}, status_code=status, headers=headers)(scope, receive, send)


class SecurityHeadersMiddleware:
    """CSP, nosniff, Referrer-Policy, Permissions-Policy, HSTS (tylko prod) i domyślne Cache-Control."""

    def __init__(self, app: ASGIApp, is_prod: bool = False, csp: str = CSP):
        self.app, self.is_prod, self.csp = app, is_prod, csp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")

        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h["Content-Security-Policy"] = self.csp
                h["X-Content-Type-Options"] = "nosniff"
                h["Referrer-Policy"] = "strict-origin-when-cross-origin"
                h["Permissions-Policy"] = PERMISSIONS_POLICY
                h["Cross-Origin-Opener-Policy"] = "same-origin"
                if self.is_prod:
                    h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                if "cache-control" not in h:
                    h["Cache-Control"] = _cache_policy(path)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _cache_policy(path: str) -> str:
    if path.startswith("/static/vendor/"):
        return "public, max-age=604800"
    if path.startswith(("/static/", "/auth/static/")):
        return "no-cache"  # przeglądarka odpytuje serwer (ETag/304), więc nowa wersja JS/CSS trafia od razu
    return "no-store"


class AccessLogMiddleware:
    """Log dostępu (bez query string) + log audytowy 401/403/413/429/5xx + bezpieczna odpowiedź 500."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope.setdefault("state", {})
        start = time.perf_counter()
        status = {"code": 500, "started": False}

        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status["code"], status["started"] = message["status"], True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            error_id = uuid.uuid4().hex[:12]
            log.exception("Nieobsłużony wyjątek error_id=%s %s %s", error_id, scope.get("method"), scope.get("path"))
            if not status["started"]:
                await _json(scope, receive, send, 500, INTERNAL_ERROR, error_id=error_id)
            status["code"] = 500
        finally:
            self._log(scope, status["code"], (time.perf_counter() - start) * 1000)

    @staticmethod
    def _log(scope: Scope, code: int, ms: float) -> None:
        user = scope.get("state", {}).get("user")
        uid = getattr(user, "id", "-")
        path = scope.get("path", "")[:200]
        line = "%s %s %s %.0fms ip=%s user=%s"
        args = (scope.get("method"), path, code, ms, client_ip(scope), uid)
        if code in (401, 403, 413, 429) or code >= 500:
            audit.warning(line, *args)
        else:
            access_log.info(line, *args)


class RateLimitMiddleware:
    """Token bucket per IP i per sesja; ostrzejsze reguły dla wybranych ścieżek (cfg.rate_rules)."""

    def __init__(self, app: ASGIApp, cfg: SecurityConfig, limiter: Optional[TokenBucketLimiter] = None):
        self.app, self.cfg = app, cfg
        self.limiter = limiter or TokenBucketLimiter()

    def _rules(self, method: str, path: str) -> Iterable[str]:
        for key in ("default", path, f"{method} {path}"):
            if key in self.cfg.rate_rules:
                yield key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.cfg.rate_limit_enabled:
            return await self.app(scope, receive, send)
        ip = client_ip(scope)
        sess = _cookies(scope).get(SESSION_COOKIE)
        sess_key = hashlib.sha256(sess.encode()).hexdigest()[:20] if sess else None
        wait = 0.0
        for rule in self._rules(scope.get("method", ""), scope.get("path", "")):
            cap, rate = self.cfg.rate_rules[rule]
            keys = [f"{rule}|ip|{ip}"] + ([f"{rule}|s|{sess_key}"] if sess_key else [])
            for key in keys:
                ok, w = self.limiter.take(key, cap, rate)
                if not ok:
                    wait = max(wait, w)
        if wait > 0:
            retry = max(1, math.ceil(wait))
            return await _json(scope, receive, send, 429, f"Zbyt wiele żądań. Spróbuj ponownie za {retry} s.",
                               headers={"Retry-After": str(retry)})
        await self.app(scope, receive, send)


class BodySizeLimitMiddleware:
    """Odrzuca żądania z body większym niż limit (413), także bez nagłówka Content-Length."""

    def __init__(self, app: ASGIApp, max_bytes: int = 256 * 1024):
        self.app, self.max_bytes = app, max_bytes

    def _detail(self) -> str:
        return f"Żądanie jest zbyt duże (limit {self.max_bytes // 1024} KB)."

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = _header(scope, b"content-length")
        if length:
            try:
                too_big = int(length) > self.max_bytes
            except ValueError:
                return await _json(scope, receive, send, 400, "Nieprawidłowy nagłówek Content-Length.")
            if too_big:
                return await _json(scope, receive, send, 413, self._detail())
        state = {"received": 0, "exceeded": False, "replaced": False}

        async def receive_wrapper() -> Dict[str, Any]:
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > self.max_bytes:
                    state["exceeded"] = True
                    raise _BodyTooLarge()
            return message

        async def send_wrapper(message: Dict[str, Any]) -> None:
            # FastAPI zamienia wyjątek podczas czytania body na 400 – podmieniamy odpowiedź na 413
            if state["exceeded"]:
                if message["type"] == "http.response.start" and not state["replaced"]:
                    state["replaced"] = True
                    await _json(scope, receive, send, 413, self._detail())
                return
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except _BodyTooLarge:
            if not state["replaced"]:
                await _json(scope, receive, send, 413, self._detail())


class _BodyTooLarge(Exception):
    pass


class CSRFMiddleware:
    """Double-submit cookie: dla POST/PUT/PATCH/DELETE nagłówek X-CSRF-Token musi równać się ciasteczku
    mrolnik_csrf, a nagłówek Origin (jeśli jest) musi wskazywać tę aplikację lub dozwolone źródło.
    Ustawia ciasteczko CSRF, gdy go brak. Ścieżki z ``exempt`` sprawdzają token samodzielnie (formularze)."""

    def __init__(self, app: ASGIApp, cfg: SecurityConfig, exempt: Iterable[str] = ("/auth/login", "/auth/logout")):
        self.app, self.cfg, self.exempt = app, cfg, set(exempt)

    def origin_allowed(self, scope: Scope) -> bool:
        origin = _header(scope, b"origin")
        if not origin:
            return True
        if origin == "null":
            return False
        if origin.rstrip("/") in self.cfg.allowed_origins:
            return True
        return urlparse(origin).netloc.lower() == _header(scope, b"host").lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cookies = _cookies(scope)
        cookie_token = cookies.get(CSRF_COOKIE, "")
        if scope.get("method") in UNSAFE_METHODS:
            if not self.origin_allowed(scope):
                audit.warning("odrzucono żądanie z obcego Origin path=%s ip=%s", scope.get("path"), client_ip(scope))
                return await _json(scope, receive, send, 403, "Żądanie z niedozwolonego źródła.")
            if scope.get("path") not in self.exempt:
                header_token = _header(scope, CSRF_HEADER.encode())
                if not (cookie_token and header_token and hmac.compare_digest(cookie_token, header_token)):
                    return await _json(scope, receive, send, 403,
                                       "Brak lub nieprawidłowy token CSRF. Odśwież stronę i spróbuj ponownie.")
        if cookie_token:
            return await self.app(scope, receive, send)

        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                if not any(v.startswith(CSRF_COOKIE + "=") for v in h.getlist("set-cookie")):
                    h.append("set-cookie", cookie_header(CSRF_COOKIE, new_csrf_token(), max_age=None,
                                                         secure=self.cfg.cookie_secure, http_only=False))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def check_csrf_value(scope_cookies: Dict[str, str], supplied: str) -> bool:
    """Weryfikacja tokenu przesłanego w formularzu (ścieżki wyłączone z CSRFMiddleware)."""
    expected = scope_cookies.get(CSRF_COOKIE, "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))

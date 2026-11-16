"""Uwierzytelnienie i autoryzacja: zależności FastAPI ``require_user`` i ``require_role``.

Użycie w routerach:
    from src.security.auth import User, require_user, require_role
    @router.post("/api/x")
    def x(user: User = Depends(require_user)): ...
    @router.get("/api/admin", dependencies=[Depends(require_role("admin"))])
"""
from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from fastapi import Request
from starlette.responses import Response

from src.security.config import SecurityConfig
from src.security.providers import AuthProvider, User, make_provider
from src.security.session import (
    CSRF_COOKIE, SESSION_COOKIE, RevokedSessions, Signer, cookie_header, new_csrf_token, new_session_id,
)

log = logging.getLogger(__name__)
audit = logging.getLogger("mrolnik.audit")

LOGIN_URL = "/auth/login"


class AuthRequired(Exception):
    """Brak ważnej sesji → 401 JSON z ``login_url``."""


class Forbidden(Exception):
    """Zalogowany, ale bez wymaganej roli → 403."""


@dataclass
class SecurityContext:
    cfg: SecurityConfig
    provider: AuthProvider
    session_signer: Signer = field(init=False)
    flow_signer: Signer = field(init=False)
    revoked: RevokedSessions = field(default_factory=RevokedSessions)

    def __post_init__(self) -> None:
        self.session_signer = Signer(self.cfg.secret_key, "session")
        self.flow_signer = Signer(self.cfg.secret_key, "oidc-flow")

    @classmethod
    def create(cls, cfg: SecurityConfig) -> "SecurityContext":
        cfg.validate()
        return cls(cfg=cfg, provider=make_provider(cfg))

    # --- sesja ---
    def issue_session(self, response: Response, user: User) -> None:
        token = self.session_signer.dumps(
            {"sid": new_session_id(), "uid": user.id, "name": user.name, "role": user.role, "iat": int(time.time())},
            self.cfg.session_ttl_s,
        )
        secure = self.cfg.cookie_secure
        response.headers.append("set-cookie", cookie_header(
            SESSION_COOKIE, token, max_age=self.cfg.session_ttl_s, secure=secure, http_only=True))
        # nowy token CSRF po zalogowaniu (ochrona przed "utrwaleniem" tokenu)
        response.headers.append("set-cookie", cookie_header(
            CSRF_COOKIE, new_csrf_token(), max_age=None, secure=secure, http_only=False))

    def end_session(self, request: Request, response: Response) -> None:
        data = self.session_signer.loads(request.cookies.get(SESSION_COOKIE))
        if data:
            self.revoked.revoke(str(data.get("sid")), float(data.get("exp", 0)))
            audit.info("wylogowanie user=%s", data.get("uid"))
        secure = self.cfg.cookie_secure
        response.headers.append("set-cookie", cookie_header(SESSION_COOKIE, "", max_age=0, secure=secure, http_only=True))
        response.headers.append("set-cookie", cookie_header(
            CSRF_COOKIE, new_csrf_token(), max_age=None, secure=secure, http_only=False))

    def user_from_request(self, request: Request) -> Optional[User]:
        if self.cfg.auth_mode == "dev":
            return self.provider.user if _client_is_loopback(request) else None  # type: ignore[attr-defined]
        data = self.session_signer.loads(request.cookies.get(SESSION_COOKIE))
        if not data or self.revoked.is_revoked(str(data.get("sid"))):
            return None
        try:
            return User(id=str(data["uid"]), name=str(data.get("name", "")), role=str(data["role"]))
        except KeyError:
            return None


def _client_is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def get_context(request: Request) -> SecurityContext:
    ctx = getattr(request.app.state, "security", None)
    if ctx is None:
        raise RuntimeError("Moduł bezpieczeństwa nie został zainstalowany (install_security).")
    return ctx


def current_user(request: Request) -> Optional[User]:
    """Zalogowany użytkownik albo None (nie podnosi wyjątku)."""
    user = get_context(request).user_from_request(request)
    if user:
        request.state.user = user
    return user


def require_user(request: Request) -> User:
    user = current_user(request)
    if user is None:
        raise AuthRequired()
    return user


def require_role(*roles: str) -> Callable[[Request], User]:
    """Zależność wymagająca jednej z ról (admin ma dostęp zawsze)."""

    def dependency(request: Request) -> User:
        user = require_user(request)
        if not user.has_role(*roles):
            audit.warning("brak uprawnień user=%s rola=%s wymagane=%s path=%s",
                          user.id, user.role, ",".join(roles), request.url.path)
            raise Forbidden()
        return user

    return dependency


def safe_next(path: Optional[str], default: str = "/web") -> str:
    """Akceptuje wyłącznie ścieżki względne w tej aplikacji (ochrona przed open redirect)."""
    if not path or len(path) > 512 or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return default
    if any(ord(c) < 32 for c in path) or path.startswith(LOGIN_URL) or path.startswith("/auth/callback"):
        return default
    return path

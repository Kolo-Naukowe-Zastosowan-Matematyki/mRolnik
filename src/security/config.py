"""Konfiguracja modułu bezpieczeństwa.

Podstawowe wartości (host, tryb uwierzytelniania, klucz sesji, dozwolone źródła CORS, środowisko)
pochodzą z ``src.core.config.settings``; pozostałe ustawienia modułu czytamy z env (prefiks ``MROLNIK_``).
Testy mogą tworzyć ``SecurityConfig`` bezpośrednio, bez env.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

AUTH_MODES = ("dev", "password", "oidc", "saml")
ROLES = ("rolnik", "doradca", "admin")
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
_CSP_HOST_RE = re.compile(r"^https://(\*\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:\d{1,5})?$")

# (pojemność kubełka, liczba tokenów odnawianych na sekundę)
RateRule = Tuple[float, float]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        log.warning("Nieprawidłowa wartość %s, używam %s", name, default)
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "tak", "on")


def is_loopback_host(host: str) -> bool:
    """True, gdy adres nasłuchu jest dostępny wyłącznie z tego komputera."""
    h = (host or "").strip().strip("[]").lower()
    if h in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


@dataclass
class PasswordUser:
    login: str
    role: str
    password_hash: str
    name: str = ""


def parse_users(raw: str) -> Dict[str, PasswordUser]:
    """Format MROLNIK_USERS: ``login:rola:hash[,login2:rola:hash...]`` (hash z ``python -m src.security.passwords``)."""
    users: Dict[str, PasswordUser] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":", 2)
        if len(parts) != 3:
            raise ValueError("MROLNIK_USERS: oczekiwano formatu login:rola:hash")
        login, role, pw_hash = (p.strip() for p in parts)
        if role not in ROLES:
            raise ValueError(f"MROLNIK_USERS: nieznana rola '{role}' (dozwolone: {', '.join(ROLES)})")
        if not login or len(login) > 64:
            raise ValueError("MROLNIK_USERS: login musi mieć 1–64 znaki")
        users[login.lower()] = PasswordUser(login=login, role=role, password_hash=pw_hash, name=login)
    return users


@dataclass
class OIDCConfig:
    issuer: str = ""
    discovery_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: str = "openid profile"
    role_claim: str = "roles"
    default_role: str = "rolnik"

    @property
    def effective_discovery_url(self) -> str:
        if self.discovery_url:
            return self.discovery_url
        return self.issuer.rstrip("/") + "/.well-known/openid-configuration" if self.issuer else ""


def default_rate_rules() -> Dict[str, RateRule]:
    return {
        # klucz "default" obejmuje wszystkie żądania; pozostałe są dodatkowe dla wskazanych ścieżek
        "default": (120.0, 4.0),
        "/api/analyze": (6.0, 6 / 60),           # ok. 6 analiz na minutę
        "/api/search-location": (30.0, 1.0),     # wyszukiwanie "w trakcie pisania"
        "POST /api/parcel-search": (6.0, 6 / 60),  # wyszukiwarka działek: ciężkie zadania w tle (postęp przez GET – limit domyślny)
        "POST /auth/login": (10.0, 2 / 60),      # 10 prób, potem 2 na minutę
    }


@dataclass
class SecurityConfig:
    host: str = "127.0.0.1"
    auth_mode: str = "dev"
    secret_key: bytes = b""
    is_prod: bool = False
    allowed_origins: List[str] = field(default_factory=list)
    session_ttl_s: int = 8 * 3600
    cookie_secure: bool = False
    users: Dict[str, PasswordUser] = field(default_factory=dict)
    dev_role: str = "admin"
    oidc: OIDCConfig = field(default_factory=OIDCConfig)
    rate_limit_enabled: bool = True
    rate_rules: Dict[str, RateRule] = field(default_factory=default_rate_rules)
    max_body_bytes: int = 256 * 1024
    csp_img_hosts: List[str] = field(default_factory=list)
    secret_ephemeral: bool = False

    @classmethod
    def from_env(cls) -> "SecurityConfig":
        from src.core.config import settings

        secret = settings.secret_key.encode("utf-8") if settings.secret_key else b""
        cfg = cls(
            host=settings.host,
            auth_mode=settings.auth_mode.lower(),
            secret_key=secret,
            is_prod=settings.is_prod,
            allowed_origins=list(settings.allowed_origins),
            session_ttl_s=_env_int("MROLNIK_SESSION_TTL_MIN", 480) * 60,
            cookie_secure=_env_bool("MROLNIK_COOKIE_SECURE", settings.is_prod),
            users=parse_users(_env("MROLNIK_USERS")),
            dev_role=_env("MROLNIK_DEV_ROLE", "admin"),
            oidc=OIDCConfig(
                issuer=_env("MROLNIK_OIDC_ISSUER"),
                discovery_url=_env("MROLNIK_OIDC_DISCOVERY_URL"),
                client_id=_env("MROLNIK_OIDC_CLIENT_ID"),
                client_secret=_env("MROLNIK_OIDC_CLIENT_SECRET"),
                redirect_uri=_env("MROLNIK_OIDC_REDIRECT_URI"),
                scopes=_env("MROLNIK_OIDC_SCOPES", "openid profile"),
                role_claim=_env("MROLNIK_OIDC_ROLE_CLAIM", "roles"),
                default_role=_env("MROLNIK_OIDC_DEFAULT_ROLE", "rolnik"),
            ),
            rate_limit_enabled=_env_bool("MROLNIK_RATE_LIMIT", True),
            max_body_bytes=_env_int("MROLNIK_MAX_BODY_KB", 256) * 1024,
            csp_img_hosts=[h.strip() for h in _env("MROLNIK_CSP_IMG_HOSTS").split(",") if h.strip()],
        )
        return cfg

    def validate(self) -> "SecurityConfig":
        """Sprawdza konfigurację; przy błędzie podnosi RuntimeError (serwer nie wystartuje)."""
        if self.auth_mode not in AUTH_MODES:
            raise RuntimeError(f"MROLNIK_AUTH_MODE='{self.auth_mode}' – dozwolone: {', '.join(AUTH_MODES)}")
        if self.is_prod:
            if len(self.secret_key) < 32:
                raise RuntimeError(
                    "Środowisko prod wymaga MROLNIK_SECRET_KEY (min. 32 znaki), np. "
                    "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
                )
            if self.auth_mode == "dev":
                raise RuntimeError("Tryb MROLNIK_AUTH_MODE=dev jest niedozwolony w środowisku prod.")
        if not self.secret_key:
            self.secret_key = secrets.token_bytes(32)
            self.secret_ephemeral = True
            log.warning("Brak MROLNIK_SECRET_KEY: używam klucza tymczasowego (sesje wygasną po restarcie).")
        if self.auth_mode == "dev" and not is_loopback_host(self.host):
            raise RuntimeError(
                f"Tryb dev (bez logowania) działa tylko na 127.0.0.1/localhost, a ustawiono MROLNIK_HOST={self.host}. "
                "Ustaw MROLNIK_AUTH_MODE=password (i MROLNIK_USERS) albo oidc."
            )
        if self.dev_role not in ROLES:
            raise RuntimeError(f"MROLNIK_DEV_ROLE musi być jedną z: {', '.join(ROLES)}")
        if self.auth_mode == "password" and not self.users:
            raise RuntimeError("Tryb password wymaga MROLNIK_USERS (login:rola:hash). Hash: python -m src.security.passwords")
        if self.auth_mode == "oidc":
            o = self.oidc
            if not (o.effective_discovery_url and o.client_id and o.redirect_uri):
                raise RuntimeError(
                    "Tryb oidc wymaga MROLNIK_OIDC_ISSUER (lub _DISCOVERY_URL), MROLNIK_OIDC_CLIENT_ID i MROLNIK_OIDC_REDIRECT_URI."
                )
            if o.default_role not in ROLES:
                raise RuntimeError(f"MROLNIK_OIDC_DEFAULT_ROLE musi być jedną z: {', '.join(ROLES)}")
        bad_hosts = [h for h in self.csp_img_hosts if not _CSP_HOST_RE.match(h)]
        if bad_hosts:
            raise RuntimeError(
                f"MROLNIK_CSP_IMG_HOSTS: nieprawidłowe adresy {bad_hosts} (wymagane https://host[:port], bez ścieżek i spacji)."
            )
        if "*" in self.allowed_origins:
            log.warning("MROLNIK_ALLOWED_ORIGINS='*' jest ignorowane (ciasteczka sesji wymagają jawnej listy).")
            self.allowed_origins = [o for o in self.allowed_origins if o != "*"]
        if self.host in ("0.0.0.0", "::"):
            log.warning(
                "UWAGA: serwer nasłuchuje na WSZYSTKICH interfejsach (%s). Każdy w tej sieci może się połączyć. "
                "Używaj tylko w zaufanej sieci, z logowaniem i najlepiej za HTTPS (reverse proxy).",
                self.host,
            )
        return self

    def rule(self, key: str) -> Optional[RateRule]:
        return self.rate_rules.get(key)

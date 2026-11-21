"""Centralna konfiguracja mRolnik: wszystkie ustawienia i klucze z env (lub pliku .env).

Nigdy nie wpisuj kluczy do kodu. Skopiuj .env.example do .env i uzupełnij.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimalny parser .env (bez zależności). Nie nadpisuje zmiennych już ustawionych."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    v = _env(name, "1" if default else "0").lower()
    return v in ("1", "true", "yes", "tak", "on")


def _env_list(name: str, default: str = "") -> List[str]:
    return [x.strip() for x in _env(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # --- serwer ---
    host: str = field(default_factory=lambda: _env("MROLNIK_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("MROLNIK_PORT", _env("PORT", "8000"))))
    env: str = field(default_factory=lambda: _env("MROLNIK_ENV", "dev"))  # dev | prod
    cache_dir: Path = field(default_factory=lambda: Path(_env("MROLNIK_CACHE_DIR", str(BASE_DIR / "output" / "cache"))))

    # --- bezpieczeństwo (właściciel: moduł src/security) ---
    auth_mode: str = field(default_factory=lambda: _env("MROLNIK_AUTH_MODE", "dev"))  # dev | password | oidc
    secret_key: str = field(default_factory=lambda: _env("MROLNIK_SECRET_KEY", ""))
    allowed_origins: List[str] = field(default_factory=lambda: _env_list("MROLNIK_ALLOWED_ORIGINS", ""))

    # --- źródła danych (właściciel: moduł src/data) ---
    cdse_client_id: str = field(default_factory=lambda: _env("CDSE_CLIENT_ID"))
    cdse_client_secret: str = field(default_factory=lambda: _env("CDSE_CLIENT_SECRET"))
    contact_email: str = field(default_factory=lambda: _env("MROLNIK_CONTACT_EMAIL"))  # do User-Agent (polityka Nominatim)
    # Opcjonalny klucz planu komercyjnego Open-Meteo (customer-archive-api, bez darmowego limitu 10 000 zapytań/dobę)
    open_meteo_api_key: str = field(default_factory=lambda: _env("OPEN_METEO_API_KEY"))
    offline_demo: bool = field(default_factory=lambda: _env_bool("MROLNIK_OFFLINE_DEMO", False))

    @property
    def is_prod(self) -> bool:
        return self.env.lower() == "prod"

    @property
    def cdse_configured(self) -> bool:
        return bool(self.cdse_client_id and self.cdse_client_secret)


settings = Settings()
settings.cache_dir.mkdir(parents=True, exist_ok=True)

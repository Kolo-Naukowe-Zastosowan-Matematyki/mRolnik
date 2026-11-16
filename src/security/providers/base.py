"""Wspólny interfejs dostawców tożsamości (dev, hasło, OIDC, SAML)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple

from src.security.config import ROLES


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: str

    def to_public(self) -> Dict[str, str]:
        return asdict(self)

    def has_role(self, *roles: str) -> bool:
        return self.role == "admin" or self.role in roles


class AuthError(Exception):
    """Błąd logowania z komunikatem bezpiecznym do pokazania użytkownikowi."""

    def __init__(self, message: str, *, retry_after: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


def normalize_role(role: Any, default: str = "rolnik") -> str:
    if isinstance(role, (list, tuple)):
        for r in ("admin", "doradca", "rolnik"):
            if r in role:
                return r
        return default
    return role if role in ROLES else default


class AuthProvider:
    """Interfejs dostawcy. Dostawca przekierowujący (OIDC/SAML) implementuje begin/complete;
    dostawca formularzowy (hasło) implementuje ``authenticate``."""

    name = "base"
    interactive_form = False  # True: strona /auth/login pokazuje formularz login/hasło

    def authenticate(self, login: str, password: str, client_ip: str) -> User:  # pragma: no cover - interfejs
        raise NotImplementedError

    def begin_login(self, next_path: str) -> Tuple[str, Dict[str, Any]]:  # pragma: no cover - interfejs
        """Zwraca (URL przekierowania do dostawcy, stan przepływu do zapisania w podpisanym ciasteczku)."""
        raise NotImplementedError

    def complete_login(self, params: Dict[str, str], flow: Dict[str, Any]) -> User:  # pragma: no cover - interfejs
        """Kończy logowanie po powrocie z dostawcy (GET /auth/callback)."""
        raise NotImplementedError

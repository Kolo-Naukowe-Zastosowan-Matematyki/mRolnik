"""Dostawcy tożsamości. ``make_provider`` wybiera implementację wg MROLNIK_AUTH_MODE."""
from __future__ import annotations

from src.security.config import SecurityConfig
from src.security.providers.base import AuthError, AuthProvider, User


def make_provider(cfg: SecurityConfig) -> AuthProvider:
    if cfg.auth_mode == "dev":
        from src.security.providers.dev import DevProvider
        return DevProvider(cfg.dev_role)
    if cfg.auth_mode == "password":
        from src.security.providers.password import PasswordProvider
        return PasswordProvider(cfg.users)
    if cfg.auth_mode == "oidc":
        from src.security.providers.oidc import OIDCProvider
        return OIDCProvider(cfg.oidc)
    if cfg.auth_mode == "saml":
        from src.security.providers.saml import SAMLProvider
        return SAMLProvider()
    raise RuntimeError(f"Nieznany tryb uwierzytelniania: {cfg.auth_mode}")


__all__ = ["AuthError", "AuthProvider", "User", "make_provider"]

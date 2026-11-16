"""Tryb deweloperski: jeden lokalny użytkownik, bez logowania. Tylko dla 127.0.0.1/localhost."""
from __future__ import annotations

from src.security.providers.base import AuthProvider, User


class DevProvider(AuthProvider):
    name = "dev"

    def __init__(self, role: str = "admin"):
        self.user = User(id="lokalny", name="Użytkownik lokalny", role=role)

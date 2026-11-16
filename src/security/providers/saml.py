"""Zaślepka dostawcy SAML 2.0 – miejsce na integrację z login.gov.pl (Węzeł Krajowy).

Węzeł Krajowy udostępnia uwierzytelnienie wyłącznie przez SAML 2.0 (profil WK: AuthnRequest,
HTTP-Artifact + ArtifactResolve po SOAP, podpisy i szyfrowanie asercji). Implementacja wymaga:
 * porozumienia z operatorem WK (Ministerstwo Cyfryzacji / COI) i rejestracji usługi (środowisko testowe i produkcyjne),
 * pary kluczy i certyfikatu do podpisu (oraz deszyfrowania asercji) i metadanych SP (entityID, adres ACS),
 * metadanych IdP Węzła Krajowego i biblioteki SAML z weryfikacją XML-DSig (np. python3-saml/xmlsec lub pysaml2).
Szczegóły: docs/BEZPIECZENSTWO.md, sekcja "login.gov.pl i SSO eRolnika".
"""
from __future__ import annotations

from src.security.providers.base import AuthProvider


class SAMLProvider(AuthProvider):
    name = "saml"

    def __init__(self) -> None:
        raise RuntimeError(
            "Tryb MROLNIK_AUTH_MODE=saml (login.gov.pl / Węzeł Krajowy) nie jest jeszcze zaimplementowany. "
            "Wymagania i kroki: docs/BEZPIECZENSTWO.md. Do czasu integracji użyj trybu password lub oidc."
        )

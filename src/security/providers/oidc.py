"""Generyczny OpenID Connect: Authorization Code + PKCE (S256) + state + nonce.

Działa z każdym dostawcą OIDC (np. SSO eRolnika, Keycloak, Azure AD/Entra ID). login.gov.pl (Węzeł Krajowy)
używa SAML 2.0 – patrz ``saml.py`` i docs/BEZPIECZENSTWO.md.

Token ID pobieramy bezpośrednio z token endpointu przez TLS. Podpis RS256 weryfikujemy kluczami JWKS
(PKCS#1 v1.5, metoda "zakoduj i porównaj"); dla innych algorytmów opieramy się na OIDC Core 3.1.3.7
(bezpośredni kanał TLS z token endpointem) i zawsze sprawdzamy iss, aud, exp, iat i nonce.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse

from src.security.config import OIDCConfig
from src.security.providers.base import AuthError, AuthProvider, User, normalize_role

log = logging.getLogger(__name__)
audit = logging.getLogger("mrolnik.audit")

_LEEWAY_S = 120
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def make_pkce_pair() -> Tuple[str, str]:
    """(code_verifier, code_challenge S256) wg RFC 7636."""
    verifier = secrets.token_urlsafe(64)  # 86 znaków z [A-Za-z0-9_-]
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def rs256_verify(n: int, e: int, signing_input: bytes, signature: bytes) -> bool:
    """Weryfikacja RSASSA-PKCS1-v1_5 z SHA-256 (porównanie pełnego, oczekiwanego bloku EM)."""
    k = (n.bit_length() + 7) // 8
    if n.bit_length() < 2048 or len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_DIGEST_INFO + hashlib.sha256(signing_input).digest()
    ps_len = k - len(t) - 3
    expected = b"\x00\x01" + b"\xff" * ps_len + b"\x00" + t
    return hmac.compare_digest(em, expected)


def _requests_http() -> Tuple[Callable[..., Dict[str, Any]], Callable[..., Dict[str, Any]]]:
    import requests

    def get_json(url: str) -> Dict[str, Any]:
        r = requests.get(url, timeout=5, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()

    def post_form(url: str, data: Dict[str, str], auth: Optional[Tuple[str, str]] = None) -> Dict[str, Any]:
        r = requests.post(url, data=data, auth=auth, timeout=10, headers={"Accept": "application/json"})
        if r.status_code >= 400:
            log.warning("OIDC token endpoint zwrócił HTTP %s", r.status_code)
            r.raise_for_status()
        return r.json()

    return get_json, post_form


def _check_url(url: str, what: str) -> str:
    p = urlparse(url or "")
    local = p.hostname in ("localhost", "127.0.0.1")
    if p.scheme != "https" and not (p.scheme == "http" and local):
        raise AuthError(f"Konfiguracja OIDC: {what} musi używać https.")
    return url


class OIDCProvider(AuthProvider):
    name = "oidc"

    def __init__(self, cfg: OIDCConfig, get_json: Callable[..., Dict[str, Any]] | None = None,
                 post_form: Callable[..., Dict[str, Any]] | None = None):
        self.cfg = cfg
        if get_json is None or post_form is None:
            default_get, default_post = _requests_http()
            get_json, post_form = get_json or default_get, post_form or default_post
        self._get_json, self._post_form = get_json, post_form
        self._meta: Optional[Dict[str, Any]] = None
        self._meta_ts = 0.0
        self._jwks: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # --- metadane dostawcy ---
    def metadata(self) -> Dict[str, Any]:
        with self._lock:
            if self._meta and time.time() - self._meta_ts < 3600:
                return self._meta
        try:
            meta = self._get_json(_check_url(self.cfg.effective_discovery_url, "discovery URL"))
        except AuthError:
            raise
        except Exception as exc:
            log.error("Nie udało się pobrać konfiguracji OIDC: %s", type(exc).__name__)
            raise AuthError("Usługa logowania jest chwilowo niedostępna. Spróbuj później.") from exc
        for key in ("issuer", "authorization_endpoint", "token_endpoint"):
            if not meta.get(key):
                raise AuthError("Nieprawidłowa konfiguracja dostawcy logowania.")
        if self.cfg.issuer and meta["issuer"].rstrip("/") != self.cfg.issuer.rstrip("/"):
            log.error("OIDC: issuer z discovery (%s) różni się od skonfigurowanego", meta["issuer"])
            raise AuthError("Nieprawidłowa konfiguracja dostawcy logowania.")
        _check_url(meta["authorization_endpoint"], "authorization_endpoint")
        _check_url(meta["token_endpoint"], "token_endpoint")
        with self._lock:
            self._meta, self._meta_ts = meta, time.time()
        return meta

    # --- początek logowania ---
    def begin_login(self, next_path: str) -> Tuple[str, Dict[str, Any]]:
        meta = self.metadata()
        verifier, challenge = make_pkce_pair()
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        params = {
            "response_type": "code",
            "client_id": self.cfg.client_id,
            "redirect_uri": self.cfg.redirect_uri,
            "scope": self.cfg.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        sep = "&" if "?" in meta["authorization_endpoint"] else "?"
        url = meta["authorization_endpoint"] + sep + urlencode(params)
        return url, {"state": state, "nonce": nonce, "verifier": verifier, "next": next_path}

    # --- powrót z dostawcy ---
    def complete_login(self, params: Dict[str, str], flow: Dict[str, Any]) -> User:
        if params.get("error"):
            audit.warning("OIDC: dostawca zwrócił błąd %s", str(params.get("error"))[:64])
            raise AuthError("Logowanie zostało przerwane lub odrzucone przez dostawcę tożsamości.")
        state, code = params.get("state", ""), params.get("code", "")
        if not flow or not state or not hmac.compare_digest(state, str(flow.get("state", ""))):
            audit.warning("OIDC: niezgodny parametr state")
            raise AuthError("Sesja logowania wygasła lub jest nieprawidłowa. Zaloguj się ponownie.")
        if not code or len(code) > 2048:
            raise AuthError("Brak kodu autoryzacji.")
        meta = self.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.cfg.redirect_uri,
            "client_id": self.cfg.client_id,
            "code_verifier": flow["verifier"],
        }
        auth = (self.cfg.client_id, self.cfg.client_secret) if self.cfg.client_secret else None
        try:
            tokens = self._post_form(meta["token_endpoint"], data, auth)
        except Exception as exc:
            log.error("OIDC: wymiana kodu nie powiodła się: %s", type(exc).__name__)
            raise AuthError("Nie udało się dokończyć logowania. Spróbuj ponownie.") from exc
        claims = self.validate_id_token(str(tokens.get("id_token", "")), flow["nonce"], meta)
        role = normalize_role(claims.get(self.cfg.role_claim), self.cfg.default_role)
        name = claims.get("name") or " ".join(
            x for x in (claims.get("given_name"), claims.get("family_name")) if x
        ) or "Użytkownik"
        user = User(id=f"oidc:{claims['sub']}"[:200], name=str(name)[:120], role=role)
        audit.info("logowanie OIDC udane user=%s rola=%s", user.id, role)
        return user

    # --- walidacja tokenu ID ---
    def validate_id_token(self, id_token: str, nonce: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        bad = AuthError("Dostawca tożsamości zwrócił nieprawidłowy token.")
        try:
            h64, p64, s64 = id_token.split(".")
            header = json.loads(_b64url_decode(h64))
            claims = json.loads(_b64url_decode(p64))
            signature = _b64url_decode(s64)
        except (ValueError, UnicodeError):
            raise bad
        alg = str(header.get("alg") or "none")
        if alg == "none" or (alg.startswith("HS") and not self.cfg.client_secret):
            raise bad
        if alg == "RS256":
            if not self._verify_rs256(header, f"{h64}.{p64}".encode("ascii"), signature, meta):
                audit.warning("OIDC: nieprawidłowy podpis tokenu ID")
                raise bad
        elif alg.startswith("HS"):
            digest = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}.get(alg)
            mac = digest and hmac.new(self.cfg.client_secret.encode(), f"{h64}.{p64}".encode(), digest).digest()
            if not mac or not hmac.compare_digest(mac, signature):
                raise bad
        else:
            log.info("OIDC: alg=%s – opieram się na kanale TLS z token endpointem (OIDC Core 3.1.3.7)", alg)
        now = time.time()
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        checks = [
            str(claims.get("iss", "")).rstrip("/") == str(meta["issuer"]).rstrip("/"),
            self.cfg.client_id in auds,
            len(auds) == 1 or claims.get("azp") == self.cfg.client_id,
            isinstance(claims.get("exp"), (int, float)) and claims["exp"] + _LEEWAY_S > now,
            isinstance(claims.get("iat"), (int, float)) and claims["iat"] - _LEEWAY_S < now,
            hmac.compare_digest(str(claims.get("nonce", "")), nonce),
            bool(claims.get("sub")),
        ]
        if not all(checks):
            audit.warning("OIDC: odrzucono token ID (iss/aud/exp/iat/nonce/sub)")
            raise bad
        return claims

    def _verify_rs256(self, header: Dict[str, Any], signing_input: bytes, sig: bytes, meta: Dict[str, Any]) -> bool:
        for refresh in (False, True):  # przy nieznanym kid odświeżamy JWKS raz (rotacja kluczy)
            for key in self._candidate_keys(header.get("kid"), meta, refresh):
                try:
                    n = int.from_bytes(_b64url_decode(key["n"]), "big")
                    e = int.from_bytes(_b64url_decode(key["e"]), "big")
                except (KeyError, ValueError, TypeError):
                    continue
                if rs256_verify(n, e, signing_input, sig):
                    return True
        return False

    def _candidate_keys(self, kid: Optional[str], meta: Dict[str, Any], refresh: bool) -> List[Dict[str, Any]]:
        if refresh or not self._jwks:
            if not meta.get("jwks_uri"):
                return []
            try:
                self._jwks = self._get_json(_check_url(meta["jwks_uri"], "jwks_uri"))
            except Exception as exc:
                log.error("OIDC: nie udało się pobrać JWKS: %s", type(exc).__name__)
                return []
        keys = [k for k in self._jwks.get("keys", []) if k.get("kty") == "RSA" and k.get("use", "sig") == "sig"]
        return [k for k in keys if k.get("kid") == kid] if kid else keys

"""Hashowanie haseł (scrypt z biblioteki standardowej).

Format: ``scrypt$<n>$<r>$<p>$<sól_b64>$<hash_b64>``.
Wygenerowanie wpisu do MROLNIK_USERS:  ``python -m src.security.passwords``
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import logging
import secrets

log = logging.getLogger(__name__)

_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32
_MAXMEM = 64 * 1024 * 1024


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str, *, n: int = _N, r: int = _R, p: int = _P) -> str:
    if not password:
        raise ValueError("Hasło nie może być puste")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_DKLEN, maxmem=_MAXMEM)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    """Porównanie w stałym czasie; błędny format hasha = False (z wpisem w logu)."""
    try:
        algo, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if algo != "scrypt":
            raise ValueError("nieobsługiwany algorytm")
        n_i, r_i, p_i = int(n), int(r), int(p)
        if not (2 ** 10 <= n_i <= 2 ** 20 and 1 <= r_i <= 32 and 1 <= p_i <= 16):
            raise ValueError("parametry scrypt poza zakresem")
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
    except (ValueError, TypeError) as exc:
        log.error("Nieprawidłowy format hasha hasła w konfiguracji: %s", exc)
        return False
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n_i, r=r_i, p=p_i, dklen=len(expected), maxmem=_MAXMEM)
    return hmac.compare_digest(dk, expected)


# Hash atrapy: weryfikujemy go dla nieznanych loginów, żeby czas odpowiedzi nie zdradzał, czy konto istnieje.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def _main() -> None:
    login = input("Login: ").strip()
    role = input("Rola [rolnik/doradca/admin] (domyślnie rolnik): ").strip() or "rolnik"
    pw = getpass.getpass("Hasło (min. 12 znaków): ")
    if len(pw) < 12:
        raise SystemExit("Hasło jest za krótkie (min. 12 znaków).")
    if pw != getpass.getpass("Powtórz hasło: "):
        raise SystemExit("Hasła nie są zgodne.")
    print("\nDopisz do MROLNIK_USERS w pliku .env (kolejne konta oddziel przecinkiem):")
    print(f"{login}:{role}:{hash_password(pw)}")


if __name__ == "__main__":
    _main()

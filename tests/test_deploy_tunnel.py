"""Weryfikacja konfiguracji tunelu Cloudflare (scripts/tunnel.ps1, .gitignore, tryb prod/password za proxy 127.0.0.1)."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.security.auth import require_user
from src.security.bootstrap import install_security
from src.security.config import SecurityConfig, parse_users
from src.security.passwords import hash_password

ROOT = Path(__file__).resolve().parents[1]


def test_tunnel_files_present_and_env_tunnel_ignored():
    assert (ROOT / "mrolnik_tunnel.bat").is_file()
    ps1 = (ROOT / "scripts" / "tunnel.ps1").read_text(encoding="utf-8")
    assert "MROLNIK_AUTH_MODE=password" in ps1
    assert "MROLNIK_HOST=127.0.0.1" in ps1
    assert "MROLNIK_FORWARDED_ALLOW_IPS=127.0.0.1" in ps1
    assert "MROLNIK_COOKIE_SECURE=1" in ps1
    assert ".env.tunnel" in ps1
    # Regresja: PowerShell 5.1 dokleja BOM (\ufeff) do pipe stdin – hasło musi iść przez UTF-8 Base64 w env
    assert "MROLNIK_TMP_PW_B64" in ps1
    assert "sys.stdin.read()" not in ps1

    # .env.* w .gitignore chroni .env.tunnel przed wyciekiem
    lines = {l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert ".env.*" in lines


def test_cloudflare_tunnel_prod_password_flow():
    pw = "ZnajomiJuryTest2026!"
    sec_cfg = SecurityConfig(
        host="127.0.0.1",
        auth_mode="password",
        secret_key=secrets.token_urlsafe(48).encode("utf-8"),
        is_prod=True,
        cookie_secure=True,
        users=parse_users(f"jury:rolnik:{hash_password(pw)}"),
    )

    app = FastAPI()
    install_security(app, sec_cfg)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/protected", dependencies=[Depends(require_user)])
    def protected():
        return {"ok": True}

    tunnel_origin = "https://demo-field-test.trycloudflare.com"
    client = TestClient(app, base_url=tunnel_origin, client=("203.0.113.42", 54321))

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/protected").status_code == 401

    assert client.get("/auth/login").status_code == 200
    csrf = client.cookies.get("mrolnik_csrf")
    assert csrf

    resp = client.post(
        "/auth/login",
        data={"login": "jury", "password": pw, "csrf_token": csrf, "next": "/web"},
        headers={"Origin": tunnel_origin},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/web"

    ok = client.get("/api/protected")
    assert ok.status_code == 200 and ok.json() == {"ok": True}

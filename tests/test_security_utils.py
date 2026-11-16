"""Pomocnicze funkcje testów bezpieczeństwa (minimalna aplikacja z warstwą bezpieczeństwa, bez routerów B/C)."""
from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.security.auth import User, require_role, require_user
from src.security.bootstrap import install_security
from src.security.config import PasswordUser, SecurityConfig
from src.security.passwords import hash_password

LOOPBACK = ("127.0.0.1", 50000)
REMOTE = ("192.0.2.10", 50000)
PASSWORD = "bardzo-tajne-haslo-123"
_HASH = hash_password(PASSWORD)


class Item(BaseModel):
    name: str


def make_cfg(**kw) -> SecurityConfig:
    kw.setdefault("auth_mode", "dev")
    kw.setdefault("secret_key", b"k" * 48)
    if kw["auth_mode"] == "password":
        kw.setdefault("users", {
            "jan": PasswordUser("jan", "rolnik", _HASH),
            "ola": PasswordUser("ola", "doradca", _HASH),
        })
    return SecurityConfig(**kw)


def make_app(cfg: SecurityConfig | None = None) -> FastAPI:
    app = FastAPI()
    install_security(app, cfg or make_cfg())

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/data", dependencies=[Depends(require_user)])
    def data():
        return {"ok": True}

    @app.post("/api/echo", dependencies=[Depends(require_user)])
    async def echo(request: Request):
        return {"bytes": len(await request.body())}

    @app.post("/api/item")
    def item(it: Item, user: User = Depends(require_user)):
        return {"name": it.name, "user": user.id}

    @app.get("/api/admin", dependencies=[Depends(require_role("doradca"))])
    def admin():
        return {"ok": True}

    @app.get("/api/boom")
    def boom():
        raise ValueError("sekretny ślad stosu")

    return app


def client(app: FastAPI, addr=LOOPBACK) -> TestClient:
    return TestClient(app, client=addr, raise_server_exceptions=False)


def csrf(c: TestClient) -> dict:
    if not c.cookies.get("mrolnik_csrf"):
        c.get("/api/health")
    return {"X-CSRF-Token": c.cookies.get("mrolnik_csrf")}


def login(c: TestClient, user: str = "jan", password: str = PASSWORD, next_path: str = "/web"):
    c.get("/auth/login")
    return c.post("/auth/login", data={"login": user, "password": password,
                                       "csrf_token": c.cookies.get("mrolnik_csrf"), "next": next_path},
                  follow_redirects=False)

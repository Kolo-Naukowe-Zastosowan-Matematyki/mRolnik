"""Serwer aplikacji mRolnik (FastAPI).

Uruchomienie:  python server.py [--open] [--port 8000]   (zalecane: URUCHOM_MROLNIK.bat)
Domyślnie nasłuchuje tylko na 127.0.0.1. Konfiguracja: plik .env (wzór: .env.example), opis: docs/BEZPIECZENSTWO.md.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import urllib.request
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.core.config import settings
from src.security.auth import require_user
from src.security.bootstrap import install_security

APP_VERSION = "2.1"
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
log = logging.getLogger("mrolnik.server")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "tak", "on")


def _warm_up() -> None:
    try:
        from src.api_service import warm_up_cache
    except (ImportError, AttributeError):
        log.info("Rozgrzewanie bufora pominięte: brak src.api_service.warm_up_cache")
        return
    try:
        warm_up_cache()
    except Exception:
        log.exception("Rozgrzewanie bufora nie powiodło się")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # rozgrzewanie cache wszystkich przykładów w tle – domyślnie włączone; MROLNIK_WARMUP=0 wyłącza; pomijane w testach
    off = os.environ.get("MROLNIK_WARMUP", "1").strip().lower() in ("0", "false", "no", "nie", "off")
    if not off and not os.environ.get("PYTEST_CURRENT_TEST"):
        threading.Thread(target=_warm_up, name="mrolnik-warmup", daemon=True).start()
    yield


def _index_file() -> Path:
    for name in ("index.html", "web.html"):
        path = STATIC_DIR / name
        if path.is_file():
            return path
    raise HTTPException(status_code=404, detail="Brak interfejsu (static/index.html).")


def create_app() -> FastAPI:
    app = FastAPI(
        title="mRolnik API", version=APP_VERSION, lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None if settings.is_prod else "/openapi.json",
    )
    install_security(app)

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "app": "mrolnik", "version": APP_VERSION}

    from src.web import routes_analysis, routes_calc, routes_search

    protected = [Depends(require_user)]
    for module in (routes_analysis, routes_calc, routes_search):
        app.include_router(module.router, dependencies=protected)

    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    @app.get("/web", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_index_file(), headers={"Cache-Control": "no-cache"})

    @app.get("/mobile", include_in_schema=False)
    def mobile() -> RedirectResponse:
        return RedirectResponse("/web", status_code=302)

    return app


# --- uruchamianie z wiersza poleceń ---

def _is_mrolnik_running(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=1.5) as resp:
            return json.loads(resp.read(2048)).get("app") == "mrolnik"
    except (OSError, ValueError):
        return False


def _port_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Serwer mRolnik")
    parser.add_argument("--open", action="store_true", help="otwórz przeglądarkę po starcie")
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()

    host = settings.host
    browse_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    if _is_mrolnik_running(browse_host, args.port):
        url = f"http://{browse_host}:{args.port}/web"
        log.info("mRolnik już działa: %s", url)
        if args.open:
            webbrowser.open(url)
        return
    port = next((p for p in range(args.port, args.port + 20) if _port_free(host, p)), None)
    if port is None:
        log.critical("Brak wolnego portu w zakresie %s–%s.", args.port, args.port + 19)
        sys.exit(1)
    if port != args.port:
        log.warning("Port %s jest zajęty przez inny program – używam portu %s.", args.port, port)
    url = f"http://{browse_host}:{port}/web"
    log.info("Uruchamianie mRolnik: %s (zatrzymanie: CTRL+C)", url)
    if args.open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(
        app, host=host, port=port, log_level="info",
        access_log=False,  # log dostępu uvicorna zawiera pełny query string (dane osobowe) – mamy własny
        server_header=False, proxy_headers=True,
        forwarded_allow_ips=os.environ.get("MROLNIK_FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )


def _setup_logging() -> None:
    """Log na konsolę i do pliku output/logs/mrolnik.log (rotacja 3×2 MB), żeby po zamknięciu okna dało się sprawdzić,
    dlaczego serwer przestał działać. faulthandler zapisuje też awarie bibliotek natywnych (np. segfault)."""
    import faulthandler
    from logging.handlers import RotatingFileHandler

    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    for stream in (sys.stdout, sys.stderr):   # konsola Windows (cp852) nie zna np. „•” – nie przerywamy logu
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format=fmt)
    try:
        log_dir = BASE_DIR / "output" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_dir / "mrolnik.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter(fmt))
        logging.getLogger().addHandler(fh)
        global _CRASH_FILE
        _CRASH_FILE = open(log_dir / "awarie.log", "a", encoding="utf-8")  # noqa: SIM115 – otwarty do końca procesu
        faulthandler.enable(_CRASH_FILE)
    except OSError as exc:
        log.warning("Nie udało się otworzyć pliku logu: %s", exc)


_CRASH_FILE = None

if __name__ == "__main__":
    _setup_logging()

try:
    app = create_app()
except RuntimeError as exc:
    if __name__ != "__main__":
        raise
    log.critical("Nie można uruchomić serwera: %s", exc)
    sys.exit(2)

if __name__ == "__main__":
    main()

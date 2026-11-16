"""Instalacja modułu bezpieczeństwa w aplikacji FastAPI (middleware, obsługa błędów, router /auth)."""
from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.security.auth import LOGIN_URL, AuthRequired, Forbidden, SecurityContext
from src.security.config import SecurityConfig
from src.security.middleware import (
    AccessLogMiddleware, BodySizeLimitMiddleware, CSRFMiddleware, RateLimitMiddleware,
    SecurityHeadersMiddleware, build_csp,
)

log = logging.getLogger(__name__)

_PL_STATUS = {
    400: "Nieprawidłowe żądanie.",
    401: "Wymagane zalogowanie.",
    403: "Brak uprawnień.",
    404: "Nie znaleziono.",
    405: "Metoda niedozwolona.",
    413: "Żądanie jest zbyt duże.",
    429: "Zbyt wiele żądań.",
}


def install_security(app: FastAPI, cfg: Optional[SecurityConfig] = None) -> SecurityContext:
    """Waliduje konfigurację (RuntimeError = serwer nie startuje) i podpina całą warstwę bezpieczeństwa."""
    cfg = cfg or SecurityConfig.from_env()
    ctx = SecurityContext.create(cfg)
    app.state.security = ctx

    # kolejność: ostatnio dodane = najbardziej zewnętrzne
    app.add_middleware(CSRFMiddleware, cfg=cfg)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=cfg.max_body_bytes)
    app.add_middleware(RateLimitMiddleware, cfg=cfg)
    if cfg.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
            max_age=600,
        )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, is_prod=cfg.is_prod, csp=build_csp(cfg.csp_img_hosts))

    _install_handlers(app)

    from src.web import routes_auth
    app.include_router(routes_auth.router)
    log.info("Bezpieczeństwo: tryb=%s host=%s prod=%s CORS=%s", cfg.auth_mode, cfg.host, cfg.is_prod,
             ",".join(cfg.allowed_origins) or "tylko to samo źródło")
    return ctx


def _install_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthRequired)
    async def _auth_required(request: Request, exc: AuthRequired) -> JSONResponse:
        return JSONResponse({"detail": "Wymagane zalogowanie.", "login_url": LOGIN_URL}, status_code=401)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden) -> JSONResponse:
        return JSONResponse({"detail": "Brak uprawnień do tej operacji."}, status_code=403)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg", ""))[:200], "type": e.get("type", "")}
            for e in exc.errors()[:20]
        ]
        return JSONResponse({"detail": "Nieprawidłowe dane wejściowe.", "errors": errors}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        try:
            default_phrase = HTTPStatus(exc.status_code).phrase
        except ValueError:
            default_phrase = ""
        if detail in (None, "", default_phrase) and exc.status_code in _PL_STATUS:
            detail = _PL_STATUS[exc.status_code]
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

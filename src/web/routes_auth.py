"""Endpointy logowania: /auth/me, /auth/login, /auth/callback, /auth/logout. Właściciel: bezpieczeństwo."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from src.security.auth import LOGIN_URL, current_user, get_context, safe_next
from src.security.middleware import check_csrf_value, client_ip
from src.security.pages import login_page, message_page
from src.security.providers import AuthError
from src.security.session import CSRF_COOKIE, CSRF_HEADER, OIDC_FLOW_COOKIE, cookie_header, new_csrf_token

log = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

_CSS = Path(__file__).resolve().parents[1] / "security" / "static" / "login.css"
_FLOW_TTL_S = 600


def _html(content: str, status: int = 200, headers: Optional[Dict[str, str]] = None) -> HTMLResponse:
    return HTMLResponse(content, status_code=status, headers=headers)


async def _read_fields(request: Request) -> Dict[str, str]:
    """Pola formularza (x-www-form-urlencoded) lub JSON – bez zależności python-multipart."""
    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    try:
        if ctype.startswith("application/json"):
            data = json.loads(raw or b"{}")
            return {k: str(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, (str, int))} if isinstance(data, dict) else {}
        parsed = parse_qs(raw.decode("utf-8"), max_num_fields=10)
        return {k: v[0] for k, v in parsed.items()}
    except (ValueError, UnicodeDecodeError):
        return {}


def _wants_json(request: Request) -> bool:
    return request.headers.get("content-type", "").startswith("application/json") or \
        "application/json" in request.headers.get("accept", "")


@router.get("/auth/static/login.css", include_in_schema=False)
def login_css() -> FileResponse:
    return FileResponse(_CSS, media_type="text/css")


@router.get("/auth/me")
def auth_me(request: Request) -> Dict:
    ctx = get_context(request)
    user = current_user(request)
    return {
        "authenticated": user is not None,
        "user": user.to_public() if user else None,
        "auth_mode": ctx.cfg.auth_mode,
        "login_url": LOGIN_URL,
    }


@router.get("/auth/login", include_in_schema=False)
async def login_form(request: Request, next: Optional[str] = None) -> Response:
    ctx = get_context(request)
    target = safe_next(next)
    if current_user(request):
        return RedirectResponse(target, status_code=303)
    if ctx.cfg.auth_mode == "dev":
        # dev działa tylko z tego komputera – tu trafia ktoś łączący się z innego adresu
        return _html(message_page("Brak dostępu", "Tryb deweloperski jest dostępny tylko z komputera, na którym działa serwer.",
                                  link_href="/web", link_text="Strona główna"), 403)
    if ctx.cfg.auth_mode == "oidc":
        try:
            url, flow = await run_in_threadpool(ctx.provider.begin_login, target)
        except AuthError as exc:
            return _html(message_page("Logowanie niedostępne", exc.message, link_href="/web", link_text="Strona główna"), 503)
        resp = RedirectResponse(url, status_code=302)
        resp.headers.append("set-cookie", cookie_header(
            OIDC_FLOW_COOKIE, ctx.flow_signer.dumps(flow, _FLOW_TTL_S), max_age=_FLOW_TTL_S,
            secure=ctx.cfg.cookie_secure, http_only=True, path="/auth"))
        return resp
    token = request.cookies.get(CSRF_COOKIE) or new_csrf_token()
    resp = _html(login_page(csrf_token=token, next_path=target))
    if not request.cookies.get(CSRF_COOKIE):
        resp.headers.append("set-cookie", cookie_header(CSRF_COOKIE, token, max_age=None,
                                                        secure=ctx.cfg.cookie_secure, http_only=False))
    return resp


@router.post("/auth/login", include_in_schema=False)
async def login_submit(request: Request) -> Response:
    ctx = get_context(request)
    if not ctx.provider.interactive_form:
        return JSONResponse({"detail": "Logowanie hasłem jest wyłączone w tym trybie."}, status_code=405)
    fields = await _read_fields(request)
    as_json = _wants_json(request)
    target = safe_next(fields.get("next"))
    login = fields.get("login", "")[:64]
    supplied = fields.get("csrf_token") or request.headers.get(CSRF_HEADER, "")
    csrf = request.cookies.get(CSRF_COOKIE, "")

    def fail(message: str, status: int, headers: Optional[Dict[str, str]] = None) -> Response:
        if as_json:
            return JSONResponse({"detail": message}, status_code=status, headers=headers)
        token = csrf or new_csrf_token()
        resp = _html(login_page(csrf_token=token, next_path=target, error=message, login=login), status, headers)
        if not csrf:
            resp.headers.append("set-cookie", cookie_header(CSRF_COOKIE, token, max_age=None,
                                                            secure=ctx.cfg.cookie_secure, http_only=False))
        return resp

    if not check_csrf_value(request.cookies, supplied):
        return fail("Formularz wygasł. Spróbuj zalogować się ponownie.", 403)
    password = fields.get("password", "")
    if not login or not password or len(password) > 256:
        return fail("Podaj login i hasło.", 400)
    try:
        user = await run_in_threadpool(ctx.provider.authenticate, login, password, client_ip(request.scope))
    except AuthError as exc:
        if exc.retry_after:
            return fail(exc.message, 429, {"Retry-After": str(exc.retry_after)})
        return fail(exc.message, 401)
    request.state.user = user
    resp: Response = JSONResponse({"ok": True, "user": user.to_public(), "next": target}) if as_json \
        else RedirectResponse(target, status_code=303)
    ctx.issue_session(resp, user)
    return resp


@router.get("/auth/callback", include_in_schema=False)
async def login_callback(request: Request) -> Response:
    ctx = get_context(request)
    if ctx.cfg.auth_mode != "oidc":
        return _html(message_page("Nieobsługiwane", "Ten adres jest używany tylko przy logowaniu zewnętrznym."), 404)
    flow = ctx.flow_signer.loads(request.cookies.get(OIDC_FLOW_COOKIE)) or {}
    params = {k: v for k, v in request.query_params.items() if k in ("code", "state", "error")}
    try:
        user = await run_in_threadpool(ctx.provider.complete_login, params, flow)
    except AuthError as exc:
        resp: Response = _html(message_page("Logowanie nieudane", exc.message), 400)
    else:
        request.state.user = user
        resp = RedirectResponse(safe_next(flow.get("next")), status_code=303)
        ctx.issue_session(resp, user)
    resp.headers.append("set-cookie", cookie_header(OIDC_FLOW_COOKIE, "", max_age=0, secure=ctx.cfg.cookie_secure,
                                                    http_only=True, path="/auth"))
    return resp


@router.post("/auth/logout", include_in_schema=False)
async def logout(request: Request) -> Response:
    ctx = get_context(request)
    as_json = _wants_json(request) or bool(request.headers.get(CSRF_HEADER))
    supplied = request.headers.get(CSRF_HEADER) or (await _read_fields(request)).get("csrf_token", "")
    if not check_csrf_value(request.cookies, supplied):
        return JSONResponse({"detail": "Brak lub nieprawidłowy token CSRF. Odśwież stronę i spróbuj ponownie."},
                            status_code=403)
    resp: Response = JSONResponse({"ok": True, "login_url": LOGIN_URL}) if as_json \
        else RedirectResponse(LOGIN_URL, status_code=303)
    ctx.end_session(request, resp)
    return resp

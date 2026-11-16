"""Proste strony HTML modułu logowania (bez inline JS/CSS; dane wstawiane z html.escape)."""
from __future__ import annotations

from html import escape
from typing import Optional

_LAYOUT = """<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title} – mRolnik</title>
<link rel="stylesheet" href="/auth/static/login.css">
</head>
<body>
<a class="skip-link" href="#tresc">Przejdź do treści</a>
<header class="topbar">
  <div class="wrap"><span class="brand">mRolnik</span>
  <span class="tag">Prototyp – nie jest oficjalną usługą administracji</span></div>
</header>
<main id="tresc" class="wrap" tabindex="-1">
<div class="card">
<h1>{title}</h1>
{body}
</div>
</main>
<footer class="wrap footer"><p>mRolnik – asystent pola. Prototyp demonstracyjny.</p></footer>
</body>
</html>
"""


def _page(title: str, body: str) -> str:
    return _LAYOUT.format(title=escape(title), body=body)


def login_page(*, csrf_token: str, next_path: str, error: Optional[str] = None, login: str = "") -> str:
    err = (
        f'<div class="alert" role="alert" id="blad-logowania"><p>{escape(error)}</p></div>' if error else ""
    )
    described = ' aria-describedby="blad-logowania" aria-invalid="true"' if error else ""
    body = f"""{err}
<p class="lead">Zaloguj się, aby zobaczyć swoje pola i analizy.</p>
<form method="post" action="/auth/login" novalidate>
  <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
  <input type="hidden" name="next" value="{escape(next_path)}">
  <div class="field">
    <label for="login">Login</label>
    <input id="login" name="login" type="text" autocomplete="username" required maxlength="64"
      autocapitalize="none" spellcheck="false" value="{escape(login)}"{described}>
  </div>
  <div class="field">
    <label for="password">Hasło</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required maxlength="256"{described}>
  </div>
  <button type="submit" class="btn">Zaloguj się</button>
</form>
<p class="hint">Nie pamiętasz hasła? Skontaktuj się z administratorem systemu.</p>"""
    return _page("Logowanie", body)


def message_page(title: str, message: str, *, link_href: str = "/auth/login", link_text: str = "Wróć do logowania") -> str:
    body = (
        f'<div class="alert" role="alert"><p>{escape(message)}</p></div>'
        f'<p><a class="btn btn-secondary" href="{escape(link_href)}">{escape(link_text)}</a></p>'
    )
    return _page(title, body)

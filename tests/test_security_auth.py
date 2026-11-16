"""Uwierzytelnienie: hasła, tryby dev/password, sesje, role, blokada po nieudanych próbach."""
import pytest

from src.security.auth import safe_next
from src.security.config import SecurityConfig, is_loopback_host, parse_users
from src.security.passwords import hash_password, verify_password
from src.security.providers.password import LoginThrottle
from src.security.session import Signer
from tests.test_security_utils import LOOPBACK, PASSWORD, REMOTE, client, csrf, login, make_app, make_cfg


# --- hasła ---
def test_password_hash_roundtrip():
    h = hash_password("Kwiecień-2026!")
    assert h.startswith("scrypt$16384$8$1$")
    assert verify_password("Kwiecień-2026!", h)
    assert not verify_password("kwiecień-2026!", h)
    assert hash_password("x") != hash_password("x")  # sól


def test_verify_password_bad_format_is_false():
    assert not verify_password("a", "md5$abc")
    assert not verify_password("a", "scrypt$1$1$1$xx$yy")


def test_parse_users():
    h = hash_password("abc")
    users = parse_users(f"Jan:rolnik:{h}, ola:doradca:{h}")
    assert users["jan"].role == "rolnik" and users["ola"].role == "doradca"
    with pytest.raises(ValueError):
        parse_users(f"jan:szef:{h}")


# --- walidacja konfiguracji ---
def test_dev_mode_refused_on_non_loopback_host():
    with pytest.raises(RuntimeError, match="127.0.0.1"):
        SecurityConfig(auth_mode="dev", host="0.0.0.0").validate()


def test_prod_requires_secret_and_forbids_dev():
    with pytest.raises(RuntimeError, match="MROLNIK_SECRET_KEY"):
        SecurityConfig(auth_mode="password", is_prod=True, users=make_cfg(auth_mode="password").users).validate()
    with pytest.raises(RuntimeError, match="dev"):
        SecurityConfig(auth_mode="dev", is_prod=True, secret_key=b"s" * 40).validate()


def test_dev_generates_ephemeral_secret():
    cfg = SecurityConfig(auth_mode="dev").validate()
    assert cfg.secret_ephemeral and len(cfg.secret_key) == 32


def test_password_mode_requires_users_and_saml_is_stub():
    with pytest.raises(RuntimeError, match="MROLNIK_USERS"):
        SecurityConfig(auth_mode="password").validate()
    with pytest.raises(RuntimeError, match="saml"):
        make_app(make_cfg(auth_mode="saml"))


def test_loopback_detection():
    assert is_loopback_host("127.0.0.1") and is_loopback_host("localhost") and is_loopback_host("::1")
    assert not is_loopback_host("0.0.0.0") and not is_loopback_host("192.168.1.5")


# --- tryb dev ---
def test_dev_mode_works_on_loopback():
    c = client(make_app(), LOOPBACK)
    assert c.get("/api/data").status_code == 200
    me = c.get("/auth/me").json()
    assert me["authenticated"] and me["user"]["id"] == "lokalny" and me["auth_mode"] == "dev"


def test_dev_mode_denies_remote_client():
    c = client(make_app(), REMOTE)
    r = c.get("/api/data")
    assert r.status_code == 401 and r.json()["login_url"] == "/auth/login"


# --- tryb password ---
@pytest.fixture
def pw_client():
    return client(make_app(make_cfg(auth_mode="password")), REMOTE)


def test_password_mode_401_without_session(pw_client):
    r = pw_client.get("/api/data")
    assert r.status_code == 401
    assert r.json() == {"detail": "Wymagane zalogowanie.", "login_url": "/auth/login"}
    assert pw_client.get("/auth/me").json()["authenticated"] is False


def test_login_page_is_polish_and_has_no_inline_script_or_style(pw_client):
    r = pw_client.get("/auth/login?next=/web")
    assert r.status_code == 200 and '<html lang="pl">' in r.text
    assert "<script" not in r.text and "style=" not in r.text and "<style" not in r.text
    assert 'href="/auth/static/login.css"' in r.text and 'autocomplete="current-password"' in r.text
    css = pw_client.get("/auth/static/login.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")


def test_login_success_sets_secure_session_and_redirects(pw_client):
    r = login(pw_client, next_path="/web?x=1")
    assert r.status_code == 303 and r.headers["location"] == "/web?x=1"
    cookies = r.headers.get_list("set-cookie")
    sess = next(c for c in cookies if c.startswith("mrolnik_session="))
    assert "HttpOnly" in sess and "SameSite=Lax" in sess
    assert any(c.startswith("mrolnik_csrf=") for c in cookies)  # rotacja tokenu CSRF
    assert pw_client.get("/api/data").status_code == 200
    assert pw_client.get("/auth/me").json()["user"] == {"id": "local:jan", "name": "jan", "role": "rolnik"}


def test_session_cookie_secure_flag_in_prod():
    c = client(make_app(make_cfg(auth_mode="password", is_prod=True, cookie_secure=True)), REMOTE)
    r = login(c)
    assert all("Secure" in v for v in r.headers.get_list("set-cookie"))


def test_login_wrong_password_and_open_redirect(pw_client):
    r = login(pw_client, password="zle")
    assert r.status_code == 401 and "Nieprawidłowy login lub hasło" in r.text
    r = login(pw_client, next_path="https://zly.example/")
    assert r.headers["location"] == "/web"


def test_login_requires_csrf_form_token(pw_client):
    pw_client.get("/auth/login")
    r = pw_client.post("/auth/login", data={"login": "jan", "password": PASSWORD, "csrf_token": "x"})
    assert r.status_code == 403


def test_login_json_variant(pw_client):
    h = csrf(pw_client)
    r = pw_client.post("/auth/login", json={"login": "ola", "password": PASSWORD}, headers=h)
    assert r.status_code == 200 and r.json()["user"]["role"] == "doradca"


def test_lockout_after_repeated_failures(pw_client):
    for _ in range(5):
        assert login(pw_client, password="zle").status_code == 401
    r = login(pw_client)  # poprawne hasło, ale konto/IP chwilowo zablokowane
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_throttle_backoff_grows():
    t = LoginThrottle(free_attempts=2, base_s=10, max_s=100)
    t.failure(["k"])
    assert t.retry_after(["k"]) == 0
    t.failure(["k"])
    first = t.retry_after(["k"])
    t.failure(["k"])
    assert 0 < first <= 10 < t.retry_after(["k"]) <= 20
    t.success(["k"])
    assert t.retry_after(["k"]) == 0


def test_logout_revokes_session(pw_client):
    login(pw_client)
    old = pw_client.cookies.get("mrolnik_session")
    r = pw_client.post("/auth/logout", headers=csrf(pw_client))
    assert r.status_code == 200 and r.json()["ok"]
    assert pw_client.get("/api/data").status_code == 401
    pw_client.cookies.set("mrolnik_session", old)  # ponowne użycie starego ciasteczka
    assert pw_client.get("/api/data").status_code == 401


def test_logout_requires_csrf(pw_client):
    login(pw_client)
    assert pw_client.post("/auth/logout").status_code == 403


def test_require_role(pw_client):
    login(pw_client, "jan")
    assert pw_client.get("/api/admin").status_code == 403
    c2 = client(pw_client.app, REMOTE)
    login(c2, "ola")
    assert c2.get("/api/admin").status_code == 200


def test_tampered_session_rejected(pw_client):
    login(pw_client)
    tok = pw_client.cookies.get("mrolnik_session")
    payload, sig = tok.split(".")
    pw_client.cookies.set("mrolnik_session", payload[:-2] + "AA." + sig)
    assert pw_client.get("/api/data").status_code == 401


def test_signer_expiry_and_purpose():
    s = Signer(b"x" * 32, "session")
    assert s.loads(s.dumps({"a": 1}, 60))["a"] == 1
    assert s.loads(s.dumps({"a": 1}, -1)) is None
    assert Signer(b"x" * 32, "oidc-flow").loads(s.dumps({"a": 1}, 60)) is None


@pytest.mark.parametrize("value,expected", [
    ("/web", "/web"), ("/web?a=1", "/web?a=1"), ("//zly.example", "/web"), ("https://zly.example", "/web"),
    ("/\\zly.example", "/web"), ("/auth/login", "/web"), (None, "/web"), ("web", "/web"),
])
def test_safe_next(value, expected):
    assert safe_next(value) == expected

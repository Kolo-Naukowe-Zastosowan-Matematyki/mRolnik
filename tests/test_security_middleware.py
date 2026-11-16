"""Nagłówki, CORS, CSRF, limit żądań, limit body, obsługa błędów."""
from src.security.middleware import CSP
from tests.test_security_utils import client, csrf, make_app, make_cfg


def test_security_headers_and_exact_csp():
    r = client(make_app()).get("/api/health")
    assert r.status_code == 200
    h = r.headers
    assert h["content-security-policy"] == CSP
    assert "frame-ancestors 'self' https://erolnik.gov.pl https://*.gov.pl" in CSP
    assert "script-src 'self';" in CSP and "unsafe-inline" not in CSP
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["permissions-policy"] == "geolocation=(self), camera=(), microphone=()"
    assert "x-frame-options" not in h
    assert "strict-transport-security" not in h  # tylko w prod
    assert h["cache-control"] == "no-store"


def test_hsts_only_in_prod():
    r = client(make_app(make_cfg(auth_mode="password", is_prod=True))).get("/api/health")
    assert r.headers["strict-transport-security"].startswith("max-age=31536000")


def test_cors_without_allowed_origins_gives_no_acao():
    r = client(make_app()).get("/api/health", headers={"Origin": "https://zly.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_rejects_foreign_origin_and_allows_listed():
    c = client(make_app(make_cfg(allowed_origins=["https://erolnik.gov.pl"])))
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-CSRF-Token"}
    bad = c.options("/api/echo", headers={"Origin": "https://zly.example", **pre})
    assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers
    ok = c.options("/api/echo", headers={"Origin": "https://erolnik.gov.pl", **pre})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "https://erolnik.gov.pl"
    assert ok.headers["access-control-allow-credentials"] == "true"


def test_csrf_cookie_is_set_and_readable_by_js():
    r = client(make_app()).get("/api/health")
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("mrolnik_csrf=") and "HttpOnly" not in cookie and "SameSite=Lax" in cookie


def test_csrf_required_on_post():
    c = client(make_app())
    assert c.post("/api/echo", content=b"x").status_code == 403
    assert c.post("/api/echo", content=b"x", headers={"X-CSRF-Token": "zly"}).status_code == 403
    r = c.post("/api/echo", content=b"x", headers=csrf(c))
    assert r.status_code == 200 and r.json() == {"bytes": 1}


def test_post_from_foreign_origin_rejected_even_with_token():
    c = client(make_app())
    h = csrf(c)
    r = c.post("/api/echo", content=b"x", headers={**h, "Origin": "https://zly.example"})
    assert r.status_code == 403
    assert c.post("/api/echo", content=b"x", headers={**h, "Origin": "http://testserver"}).status_code == 200


def test_rate_limit_returns_429_with_retry_after():
    c = client(make_app(make_cfg(rate_rules={"default": (3.0, 0.01)})))
    codes = [c.get("/api/health").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = c.get("/api/health")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert "Zbyt wiele żądań" in r.json()["detail"]


def test_stricter_rule_for_specific_path():
    c = client(make_app(make_cfg(rate_rules={"default": (100.0, 10.0), "/api/data": (2.0, 0.01)})))
    assert [c.get("/api/data").status_code for _ in range(3)] == [200, 200, 429]
    assert c.get("/api/health").status_code == 200


def test_rate_limit_is_per_ip():
    app = make_app(make_cfg(rate_rules={"default": (1.0, 0.01)}))
    assert client(app, ("127.0.0.1", 1)).get("/api/health").status_code == 200
    assert client(app, ("127.0.0.1", 1)).get("/api/health").status_code == 429
    assert client(app, ("127.0.0.2", 1)).get("/api/health").status_code == 200


def test_body_limit_content_length_413():
    c = client(make_app(make_cfg(max_body_bytes=1024)))
    r = c.post("/api/echo", content=b"x" * 2048, headers=csrf(c))
    assert r.status_code == 413 and "zbyt duże" in r.json()["detail"]


def test_body_limit_chunked_413_also_for_pydantic_endpoint():
    c = client(make_app(make_cfg(max_body_bytes=1024)))
    h = {**csrf(c), "Content-Type": "application/json"}

    def gen():
        yield b'{"name": "'
        for _ in range(20):
            yield b"a" * 200
        yield b'"}'

    assert c.post("/api/echo", content=gen(), headers=h).status_code == 413
    assert c.post("/api/item", content=gen(), headers=h).status_code == 413
    ok = c.post("/api/item", json={"name": "pszenica"}, headers=h)
    assert ok.status_code == 200 and ok.json()["name"] == "pszenica"


def test_unhandled_exception_returns_polish_json_without_trace():
    r = client(make_app()).get("/api/boom")
    assert r.status_code == 500
    body = r.json()
    assert "wewnętrzny błąd" in body["detail"] and body["error_id"]
    assert "sekretny" not in r.text and "Traceback" not in r.text
    assert r.headers["content-security-policy"] == CSP


def test_validation_error_is_polish_and_does_not_echo_input():
    c = client(make_app())
    r = c.post("/api/item", json={"name": {"<script>": 1}}, headers=csrf(c))
    assert r.status_code == 422
    assert r.json()["detail"] == "Nieprawidłowe dane wejściowe." and "<script>" not in r.text


def test_unknown_route_404_polish():
    r = client(make_app()).get("/nie-ma")
    assert r.status_code == 404 and r.json()["detail"] == "Nie znaleziono."


def test_access_log_has_no_query_string(caplog):
    caplog.set_level("INFO", logger="mrolnik.access")
    client(make_app()).get("/api/health?q=Jan+Kowalski+dzialka")
    text = " ".join(rec.getMessage() for rec in caplog.records)
    assert "/api/health" in text and "Kowalski" not in text


def test_csp_img_hosts_configurable():
    from src.security.middleware import build_csp
    assert "https://integracja.gugik.gov.pl" in CSP and "https://mapy.geoportal.gov.pl" in CSP
    r = client(make_app(make_cfg(csp_img_hosts=["https://wms.example.gov.pl"]))).get("/api/health")
    assert r.headers["content-security-policy"] == build_csp(["https://wms.example.gov.pl"])
    assert "img-src 'self' data: blob: https://mapy.geoportal.gov.pl" in r.headers["content-security-policy"]
    assert "https://wms.example.gov.pl;" in r.headers["content-security-policy"]


def test_csp_img_hosts_rejects_injection():
    import pytest
    for bad in ["https://x.pl; script-src *", "http://x.pl", "https://x.pl/sciezka", "*"]:
        with pytest.raises(RuntimeError):
            make_cfg(csp_img_hosts=[bad]).validate()

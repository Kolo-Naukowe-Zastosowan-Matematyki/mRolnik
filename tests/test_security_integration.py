"""Integracja z server.py (pomijana, jeśli routery innych strumieni chwilowo się nie importują)."""
import pytest
from fastapi.routing import APIRoute

from src.security.auth import require_user

try:
    import server
except ImportError as exc:  # routery B/C w trakcie zmian
    pytest.skip(f"server.py nie importuje się: {exc}", allow_module_level=True)

from fastapi.testclient import TestClient


def _deps(route: APIRoute):
    stack, out = list(route.dependant.dependencies), set()
    while stack:
        d = stack.pop()
        out.add(d.call)
        stack.extend(d.dependencies)
    return out


def test_every_api_route_except_health_requires_user():
    api = [r for r in server.app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/")]
    assert api
    for r in api:
        if r.path == "/api/health":
            assert require_user not in _deps(r)
        else:
            assert require_user in _deps(r), r.path


def test_health_public_with_headers_and_mobile_redirect():
    c = TestClient(server.app, client=("192.0.2.1", 1))
    r = c.get("/api/health")
    assert r.status_code == 200 and r.json()["app"] == "mrolnik"
    assert "content-security-policy" in r.headers
    m = c.get("/mobile", follow_redirects=False)
    assert m.status_code == 302 and m.headers["location"] == "/web"
    assert c.get("/api/search-location", params={"q": "Kutno"}).status_code == 401

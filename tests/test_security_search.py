"""Wyszukiwarka: walidacja q, sanityzacja nazw z OSM, granice Polski, cache, polityka Nominatim."""
import pytest
from fastapi import Depends

from src.security.auth import require_user
from src.web import routes_search
from tests.test_security_utils import LOOPBACK, REMOTE, client, make_app, make_cfg

EVIL = '<img src=x onerror="alert(1)">Wólka\u0000‮ Duża'


@pytest.fixture
def app():
    a = make_app(make_cfg(rate_limit_enabled=False))
    a.include_router(routes_search.router, dependencies=[Depends(require_user)])
    routes_search._CACHE.clear()
    return a


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(url, params, timeout):
        seen.append((url, dict(params)))
        if "photon" in url:
            return {"features": [
                {"properties": {"name": EVIL, "county": "<b>pow. x</b>", "state": "mazowieckie"},
                 "geometry": {"coordinates": [21.0, 52.2]}},
                {"properties": {"name": "Berlin"}, "geometry": {"coordinates": [13.4, 52.5]}},  # poza Polską
                {"properties": {"name": "Zła liczba"}, "geometry": {"coordinates": ["nan", "x"]}},
            ]}
        return []

    monkeypatch.setattr(routes_search, "_http_get_json", fake)
    return seen


def test_search_sanitizes_display_name(app, calls):
    r = client(app).get("/api/search-location", params={"q": "Wólka Duża"})
    assert r.status_code == 200
    res = r.json()["results"]
    assert len(res) == 1  # Berlin i błędne współrzędne odrzucone
    name = res[0]["display_name"]
    assert "<" not in name and ">" not in name and "\u0000" not in name and "‮" not in name
    assert name.startswith('img src=x onerror="alert(1)"Wólka Duża')
    assert res[0]["lat"] == 52.2 and res[0]["lon"] == 21.0
    assert calls[0][1]["q"] == "Wólka Duża"


def test_search_result_is_cached(app, calls):
    c = client(app)
    c.get("/api/search-location", params={"q": "Wólka Duża"})
    r = c.get("/api/search-location", params={"q": "wólka  duża"})
    assert r.json()["cached"] is True and len(calls) == 1


@pytest.mark.parametrize("q", ["", "a" * 101, "\u0000\u0001", "   "])
def test_invalid_q_returns_422(app, calls, q):
    assert client(app).get("/api/search-location", params={"q": q}).status_code == 422
    assert calls == []


def test_missing_q_returns_422(app):
    assert client(app).get("/api/search-location").status_code == 422


def test_search_requires_user(app):
    assert client(app, REMOTE).get("/api/search-location", params={"q": "Kutno"}).status_code == 401


def test_local_index_without_network(app, calls):
    r = client(app, LOOPBACK).get("/api/search-location", params={"q": "Kutno"})
    assert r.json()["source"] == "lokalny" and r.json()["results"][0]["lat"] == 52.265
    assert calls == []


def test_parcel_id_goes_to_uldk_and_is_sanitized(app, calls, monkeypatch):
    import sys
    import types

    seen = []
    fake_mod = types.ModuleType("src.data.cadastre")

    def fetch_parcel_by_id(pid):
        seen.append(pid)
        return {"parcel_id": pid, "commune": "Zaniemyśl<script>", "county": "średzki", "area_ha": 1.234,
                "centroid_lat": 52.2, "centroid_lon": 17.2}

    fake_mod.fetch_parcel_by_id = fetch_parcel_by_id
    monkeypatch.setitem(sys.modules, "src.data.cadastre", fake_mod)
    r = client(app).get("/api/search-location", params={"q": "302505_5.0014.114/1"})
    res = r.json()["results"][0]
    assert seen == ["302505_5.0014.114/1"] and res["is_parcel"] and res["parcel_id"] == "302505_5.0014.114/1"
    assert "<" not in res["display_name"] and "1.23 ha" in res["display_name"]
    # identyfikator z próbą doklejenia parametru ULDK nie jest traktowany jak działka
    client(app).get("/api/search-location", params={"q": "302505_5.0014.114&request=GetParcelByXY"})
    assert len(seen) == 1


def test_nominatim_fallback_throttle_and_user_agent(monkeypatch):
    sleeps, seen = [], []
    monkeypatch.setattr(routes_search.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(routes_search, "_nominatim_last", 0.0)

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"display_name": "Osada <svg onload=x>", "lat": "53.0", "lon": "18.0"}]

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.append((url, params, headers, timeout))
        return Resp()

    monkeypatch.setattr(routes_search.requests, "get", fake_get)
    first = routes_search._search_nominatim("Osada")
    routes_search._search_nominatim("Osada")
    assert first[0]["display_name"] == "Osada svg onload=x"
    assert sleeps and 0 < sleeps[-1] <= 1.0  # drugie żądanie odczekało do 1 s od poprzedniego
    url, params, headers, timeout = seen[0]
    assert url == routes_search.NOMINATIM_URL and params["countrycodes"] == "pl" and timeout <= 5
    assert headers["User-Agent"].startswith("mRolnik/2.1 (+")


def test_photon_error_is_logged_not_raised(monkeypatch, caplog):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("brak sieci")

    monkeypatch.setattr(routes_search.requests, "get", boom)
    assert routes_search._search_photon("Osada") == []
    assert "Photon niedostępny" in caplog.text


def test_clean_name_limits_length():
    assert len(routes_search.clean_name("a" * 1000)) == 200
    assert routes_search.clean_name("  a\n\tb  ") == "a b"


def test_parcel_county_prefix_not_duplicated(app, calls, monkeypatch):
    import sys
    import types

    fake_mod = types.ModuleType("src.data.cadastre")
    fake_mod.fetch_parcel_by_id = lambda pid: {"parcel_id": pid, "commune": "Zaniemyśl", "county": "powiat średzki",
                                               "area_ha": 0.91, "centroid_lat": 52.2, "centroid_lon": 17.2}
    monkeypatch.setitem(sys.modules, "src.data.cadastre", fake_mod)
    res = client(app).get("/api/search-location", params={"q": "302505_5.0014.114/1"}).json()["results"][0]
    assert "pow. średzki" in res["display_name"] and "powiat" not in res["display_name"]

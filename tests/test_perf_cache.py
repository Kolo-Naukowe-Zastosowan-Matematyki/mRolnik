"""Przyspieszenia bez zmiany danych: miesiące zakończone z cache, ponawianie przy limicie CDSE, jedno pobranie naraz."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import numpy as np

from src.data import history, sentinel
from src.data.cadastre import parse_uldk_response
_real_sleep = time.sleep

from tests.test_data_support import FakeResp, cdse_settings, clean_state, http, ndvi_png, uldk_text  # noqa: F401


def _parcel():
    return parse_uldk_response(uldk_text(52.2020, 17.2012))


def _token(http):
    http.add("openid-connect/token", lambda u, kw: FakeResp(200, {"access_token": "T", "expires_in": 600}), "POST")


def _item(ym, ndvi):
    def st(v):
        return {"bands": {"B0": {"stats": {"mean": v, "sampleCount": 100, "noDataCount": 0}}}}
    return {"interval": {"from": f"{ym}-01T00:00:00Z"},
            "outputs": {"ndvi": st(ndvi), "ndmi": st(0.2), "ndwi": st(-0.4), "evi": st(0.5), "bare": st(0.0), "cnt": st(1.0)}}


def _months(a, b):
    return history._months_list(datetime.strptime(a, "%Y-%m").date(), datetime.strptime(b, "%Y-%m").date())


def test_indices_next_day_fetches_only_open_months(http, monkeypatch):
    """Drugiego dnia z CDSE pobieramy tylko miesiące jeszcze otwarte; wynik identyczny jak przy pełnym pobraniu."""
    cdse_settings(monkeypatch, True)
    _token(http)
    bodies = []

    def stats(u, kw):
        body = kw["json"]
        bodies.append(body)
        frm = body["aggregation"]["timeRange"]["from"][:7]
        to = body["aggregation"]["timeRange"]["to"][:7]
        return FakeResp(200, {"data": [_item(ym, 0.5 + int(ym[5:]) / 100) for ym in _months(frm, to) if ym != "2025-02"]})
    http.add("/api/v1/statistics", stats, "POST")
    p = _parcel()
    monkeypatch.setattr(history, "utcnow", lambda: datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc))
    first = history.monthly_indices(p)
    assert len(bodies) == 1 and bodies[0]["aggregation"]["timeRange"]["from"].startswith("2023-12-01")
    assert [r["month"] for r in first["months"]] == [m for m in _months("2023-12", "2026-11") if m != "2025-02"]

    monkeypatch.setattr(history, "utcnow", lambda: datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc))
    history._indices_cache.clear()          # nowy dzień = nowy klucz całego wyniku
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 7 * 3600)   # miesiące otwarte (TTL 6 h) wygasły
    second = history.monthly_indices(p)
    assert len(bodies) == 2
    # październik zamknięty (ponad 6 dni po końcu miesiąca) → z CDSE tylko bieżący listopad
    assert bodies[1]["aggregation"]["timeRange"]["from"].startswith("2026-11-01")
    assert second["months"] == first["months"]


def test_indices_error_month_is_not_cached_as_empty(http, monkeypatch):
    cdse_settings(monkeypatch, True)
    _token(http)
    calls = []

    def stats(u, kw):
        calls.append(1)
        data = [_item("2026-05", 0.7)]
        if len(calls) == 1:
            data.append({"interval": {"from": "2026-06-01T00:00:00Z"}, "error": {"type": "EXECUTION_ERROR"}})
        else:
            data.append(_item("2026-06", 0.6))
        return FakeResp(200, {"data": data})
    http.add("/api/v1/statistics", stats, "POST")
    p = _parcel()
    history.monthly_indices(p, start="2026-05", end="2026-06")
    history._indices_cache.clear()
    r = history.monthly_indices(p, start="2026-05", end="2026-06")
    assert len(calls) == 2
    assert [m["month"] for m in r["months"]] == ["2026-05", "2026-06"]


def test_sh_post_retries_rate_limit(http, monkeypatch):
    cdse_settings(monkeypatch, True)
    _token(http)
    seq = [429, 429, 200]

    def proc(u, kw):
        code = seq.pop(0)
        return FakeResp(code, {"ok": True} if code == 200 else {"error": "rate"})
    http.add("/api/v1/process", proc, "POST")
    r = sentinel._sh_post(sentinel.PROCESS_URL, {})
    assert r.status_code == 200 and not seq


def test_sh_post_gives_up_after_rate_limit_retries(http, monkeypatch):
    cdse_settings(monkeypatch, True)
    _token(http)
    http.add("/api/v1/process", lambda u, kw: FakeResp(429, {"error": "rate"}), "POST")
    try:
        sentinel._sh_post(sentinel.PROCESS_URL, {})
        raise AssertionError("powinien być SourceError")
    except sentinel.SourceError:
        pass
    assert http.count("/process") == 1 + sentinel.RATE_LIMIT_RETRIES


def test_concurrent_same_frame_is_fetched_once(http, monkeypatch):
    cdse_settings(monkeypatch, True)
    _token(http)
    png = ndvi_png(np.full((8, 8), 0.6))

    def slow(u, kw):
        _real_sleep(0.2)
        return FakeResp(200, text="", content=png)
    http.add("/api/v1/process", slow, "POST")
    p = _parcel()
    out = []
    ts = [threading.Thread(target=lambda: out.append(history.monthly_frame(p, "2025-06", "ndvi"))) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert http.count("/process") == 1
    assert len(out) == 3 and all(o["image"] == out[0]["image"] for o in out)



# ------------------------------------------------------------------ wspólny katalog scen dla gospodarstwa
def _feat(fid, dt, cc, lon0, lat0, d=0.5):
    return {"id": fid, "geometry": {"type": "Polygon", "coordinates": [[[lon0, lat0], [lon0 + d, lat0], [lon0 + d, lat0 + d],
                                                                          [lon0, lat0 + d], [lon0, lat0]]]},
            "properties": {"datetime": dt, "eo:cloud_cover": cc}}


def test_catalog_features_follows_pages(http, monkeypatch):
    cdse_settings(monkeypatch, True)
    _token(http)
    pages = {None: {"features": [_feat("a", "2026-11-12T10:00:00Z", 10.0, 17.0, 52.0)], "context": {"next": 100}},
             100: {"features": [_feat("b", "2026-11-14T10:00:00Z", 50.0, 17.0, 52.0)], "context": {}}}
    http.add("/catalog/1.0.0/search", lambda u, kw: FakeResp(200, pages[kw["json"].get("next")]), "POST")
    feats = sentinel.catalog_features((17.1, 52.1, 17.3, 52.3), *sentinel.current_window())
    assert [f["id"] for f in feats] == ["a", "b"] and http.count("/catalog") == 2
    assert "geometry" in http.calls[-1][2]["json"]["fields"]["include"]


def test_scenes_for_keeps_only_scenes_over_the_parcel():
    p = _parcel()   # ok. 52,20 N, 17,20 E
    feats = [_feat("nad", "2026-11-12T10:00:00Z", 10.0, 17.0, 52.0),
             _feat("obok", "2026-11-12T10:00:05Z", 90.0, 18.5, 52.0),       # inna kafelka tego samego dnia, nie nad działką
             _feat("daleko", "2026-11-13T10:00:00Z", 30.0, 20.0, 50.0)]
    sc = sentinel.scenes_for(p, feats)
    assert sc == {"2026-11-12": {"datetime": "2026-11-12T10:00:00Z", "cloud_cover": 10.0}}
    assert sc == sentinel._scenes_by_day([feats[0]])
    no_geom = [{"id": "x", "properties": {"datetime": "2026-11-12T10:00:00Z"}}]
    assert sentinel.scenes_for(p, no_geom) is None       # bez geometrii nie zgadujemy – katalog dla samej działki


def test_analyze_current_uses_shared_catalog(http, monkeypatch):
    from concurrent.futures import Future
    cdse_settings(monkeypatch, True)
    _token(http)
    stats = {"data": [{"interval": {"from": "2026-11-12T00:00:00Z"}, "outputs": {"ndvi": {"bands": {"B0": {"stats": {
        "min": 0.3, "max": 0.85, "mean": 0.72, "stDev": 0.06, "sampleCount": 400, "noDataCount": 10}}}}}}]}
    http.add("/api/v1/statistics", lambda u, kw: FakeResp(200, stats), "POST")
    http.add("/api/v1/process", lambda u, kw: FakeResp(200, text="", content=ndvi_png(np.full((10, 10), 0.72))), "POST")
    http.add("/catalog/1.0.0/search", lambda u, kw: FakeResp(200, {"features": []}), "POST")
    fut: Future = Future()
    fut.set_result([_feat("nad", "2026-11-12T10:03:00Z", 8.0, 17.0, 52.0)])
    res = sentinel.analyze_current(_parcel(), sentinel.current_window(), fut)
    assert http.count("/catalog") == 0
    assert res["provenance"]["observed_at"] == "2026-11-12T10:03:00Z"

"""Sentinel-2 / CDSE: token, katalog, statystyki, raster, brak konfiguracji."""
from __future__ import annotations

import base64
import io
from datetime import datetime, timezone

import numpy as np
import pytest
from PIL import Image

from src.data import sentinel
from src.data.cadastre import parse_uldk_response
from tests.test_data_support import FakeResp, cdse_settings, clean_state, http, ndvi_png, uldk_text  # noqa: F401

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def parcel():
    return parse_uldk_response(uldk_text(52.2020, 17.2012))


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(sentinel, "utcnow", lambda: NOW)


def _stats_item(day: str, mean: float, valid: float, n: int = 100):
    return {"interval": {"from": f"{day}T00:00:00Z", "to": f"{day}T23:59:59Z"},
            "outputs": {"ndvi": {"bands": {"B0": {"stats": {
                "min": mean - 0.2, "max": mean + 0.1, "mean": mean, "stDev": 0.05,
                "sampleCount": n, "noDataCount": int(round(n * (1 - valid)))}}}}}}


def _install_cdse(http, stats_days, png_values=None, monthly=None):
    http.add("openid-connect/token", lambda u, kw: FakeResp(200, {"access_token": "TOKEN", "expires_in": 600}), "POST")
    http.add("/catalog/1.0.0/search", lambda u, kw: FakeResp(200, {"features": [
        {"id": "S2A_1", "properties": {"datetime": "2026-11-12T10:03:21Z", "eo:cloud_cover": 12.5}},
        {"id": "S2B_2", "properties": {"datetime": "2026-11-14T10:01:00Z", "eo:cloud_cover": 64.0}},
    ]}), "POST")

    def stats(u, kw):
        body = kw["json"]
        assert kw["headers"]["Authorization"] == "Bearer TOKEN"
        assert body["aggregation"]["resx"] == 10
        assert "SCL" in body["aggregation"]["evalscript"]
        if body["aggregation"]["aggregationInterval"]["of"] == "P1M":
            return FakeResp(200, {"data": monthly or []})
        return FakeResp(200, {"data": stats_days})

    http.add("/api/v1/statistics", stats, "POST")
    if png_values is not None:
        http.add("/api/v1/process", lambda u, kw: FakeResp(200, text="", content=ndvi_png(png_values)), "POST")


def test_no_credentials_gives_null_ndvi_and_brak(monkeypatch, http, parcel):
    cdse_settings(monkeypatch, configured=False)
    res = sentinel.analyze_current(parcel)
    assert res["ndvi"] is None and res["layer"] is None
    assert res["provenance"]["kind"] == "brak"
    assert "CDSE_CLIENT_ID" in res["provenance"]["note"]
    assert http.calls == []  # żadnych zapytań, żadnego pseudo-NDVI
    assert sentinel.monthly_history(parcel) is None


def test_happy_path_picks_latest_clear_scene(monkeypatch, http, parcel):
    cdse_settings(monkeypatch)
    raster = np.full((20, 20), 0.7, dtype=np.float32)
    raster[:5, :] = 0.1          # 25% gleby
    raster[5:9, :] = 0.35        # 20% stresu
    raster[0, 0] = np.nan        # chmura / poza działką
    _install_cdse(http, [_stats_item("2026-11-14", 0.30, 0.40), _stats_item("2026-11-12", 0.62, 0.92)], raster)
    res = sentinel.analyze_current(parcel)
    nd = res["ndvi"]
    assert nd["mean"] == pytest.approx(0.62)
    assert nd["acquired_on"] == "2026-11-12"     # 14.11 odrzucony (40% pikseli ważnych)
    assert nd["valid_pct"] == pytest.approx(92.0)
    assert nd["bare_soil_pct"] == pytest.approx(100 * 99 / 399 * 0 + 100 * (5 * 20 - 1) / 399, abs=0.2)
    assert nd["healthy_pct"] + nd["stress_pct"] + nd["bare_soil_pct"] == pytest.approx(100, abs=0.2)
    prov = res["provenance"]
    assert prov["kind"] == "pomiar" and prov["observed_at"] == "2026-11-12T10:03:21Z"
    assert prov["cloud_cover_pct"] == 12.5 and prov["resolution"] == "10 m"
    layer = res["layer"]
    assert layer["image"].startswith("data:image/png;base64,")
    img = Image.open(io.BytesIO(base64.b64decode(layer["image"].split(",", 1)[1])))
    assert img.mode == "RGBA" and img.getpixel((0, 0))[3] == 0   # brak danych = przezroczysty
    (s, w), (n, e) = layer["bounds"]
    assert s < parcel.centroid_lat < n and w < parcel.centroid_lon < e
    assert http.count("openid-connect/token") == 1  # token z cache dla kolejnych zapytań
    # drugi raz – z cache, bez nowych zapytań
    before = len(http.calls)
    sentinel.analyze_current(parcel)
    assert len(http.calls) == before


def test_all_cloudy_returns_brak(monkeypatch, http, parcel):
    cdse_settings(monkeypatch)
    _install_cdse(http, [_stats_item("2026-11-14", 0.3, 0.2), _stats_item("2026-11-01", 0.3, 0.5)])
    res = sentinel.analyze_current(parcel)
    assert res["ndvi"] is None and res["provenance"]["kind"] == "brak"
    assert "chmur" in res["provenance"]["note"]


def test_cdse_error_returns_brak(monkeypatch, http, parcel):
    cdse_settings(monkeypatch)
    http.add("openid-connect/token", lambda u, kw: FakeResp(401, {"error": "invalid_client"}), "POST")
    res = sentinel.analyze_current(parcel)
    assert res["ndvi"] is None and res["provenance"]["kind"] == "brak"


def test_token_refresh_on_401(monkeypatch, http, parcel):
    cdse_settings(monkeypatch)
    _install_cdse(http, [_stats_item("2026-11-12", 0.5, 0.9)], np.full((5, 5), 0.5))
    calls = {"n": 0}
    orig = [r for r in http.routes if "statistics" in r[0]][0][2]

    def flaky(u, kw):
        calls["n"] += 1
        return FakeResp(401, {"error": "expired"}) if calls["n"] == 1 else orig(u, kw)

    http.routes = [r if "statistics" not in r[0] else (r[0], r[1], flaky) for r in http.routes]
    res = sentinel.analyze_current(parcel)
    assert res["ndvi"]["mean"] == 0.5
    assert http.count("openid-connect/token") == 2


def test_monthly_history(monkeypatch, http, parcel):
    cdse_settings(monkeypatch)
    monthly = [_stats_item("2026-06-01", 0.71, 0.95), _stats_item("2026-07-01", 0.55, 0.3), _stats_item("2026-05-01", 0.6, 0.8)]
    _install_cdse(http, [], monthly=monthly)
    h = sentinel.monthly_history(parcel)
    assert [m["month"] for m in h["ndvi_monthly"]] == ["2026-05", "2026-06", "2026-07"]
    assert h["ndvi_monthly"][1]["mean"] == 0.71
    assert h["ndvi_monthly"][2]["mean"] is None   # za mało pikseli bez chmur
    assert h["provenance"]["kind"] == "pomiar"
    body = [kw["json"] for m, u, kw in http.calls if "statistics" in u][0]
    assert 'mosaicking: "ORBIT"' in body["aggregation"]["evalscript"]



def test_monthly_history_concurrent_calls_fetch_once(monkeypatch, http, parcel):
    # rozgrzewanie, pobieranie w tle i przeglądarka pytają naraz o tę samą historię → jedno zapytanie do CDSE
    import threading
    import time
    cdse_settings(monkeypatch)
    _install_cdse(http, [], monthly=[_stats_item("2026-06-01", 0.71, 0.95)])

    def slow(handler):
        def run(u, kw):
            time.sleep(0.2)
            return handler(u, kw)
        return run

    http.routes = [r if "statistics" not in r[0] else (r[0], r[1], slow(r[2])) for r in http.routes]
    out = []
    ts = [threading.Thread(target=lambda: out.append(sentinel.monthly_history(parcel))) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(out) == 3 and all(o and o["ndvi_monthly"][0]["mean"] == 0.71 for o in out)
    assert http.count("statistics") == 1

def test_palette_and_decode_roundtrip():
    vals = np.array([[-0.5, 0.1, 0.3], [0.45, 0.6, np.nan]], dtype=np.float32)
    dec = sentinel.decode_ndvi_png(ndvi_png(vals))
    assert np.isnan(dec[1, 2])
    assert np.allclose(dec[np.isfinite(dec)], vals[np.isfinite(vals)], atol=0.01)
    assert sentinel.NDVI_PALETTE[0]["from"] == -1.0


def _stats_item_cnt(day: str, mean: float, valid: float, inside: int = 195, outside: int = 3061):
    """Kształt odpowiedzi z wyjściem "cnt" (dataMask = zasięg działki): sampleCount obejmuje bbox działki."""
    return {"interval": {"from": f"{day}T00:00:00Z", "to": f"{day}T23:59:59Z"},
            "outputs": {"ndvi": {"bands": {"B0": {"stats": {"min": mean - 0.1, "max": mean + 0.1, "mean": mean, "stDev": 0.05,
                                                          "sampleCount": inside + outside, "noDataCount": outside}}}},
                        "cnt": {"bands": {"B0": {"stats": {"mean": valid, "sampleCount": inside + outside,
                                                         "noDataCount": outside}}}}}}


def test_valid_fraction_counts_only_pixels_inside_parcel(monkeypatch, http, parcel):
    """Udział bezchmurnych pikseli liczony względem działki, nie jej bbox (wcześniej max ~6%)."""
    cdse_settings(monkeypatch)
    _install_cdse(http, [_stats_item_cnt("2026-11-12", 0.40, 0.30), _stats_item_cnt("2026-11-05", 0.23, 1.0)],
                  np.full((5, 5), 0.23))
    res = sentinel.analyze_current(parcel)
    assert res["ndvi"]["acquired_on"] == "2026-11-05" and res["ndvi"]["valid_pct"] == 100.0
    rows = sentinel.parse_stats({"data": [_stats_item_cnt("2026-11-05", 0.23, 1.0)]})
    assert rows[0]["samples"] == 195
    # dzień bez przelotu: wszystkie piksele poza zasięgiem → 0 próbek, pomijany
    assert sentinel.parse_stats({"data": [_stats_item_cnt("2026-11-06", 0.0, 0.0, inside=0)]})[0]["samples"] == 0


def test_catalog_accepts_geojson_and_daily_intervals_start_at_midnight(monkeypatch, http, parcel):
    cdse_settings(monkeypatch)
    _install_cdse(http, [_stats_item_cnt("2026-11-12", 0.6, 0.9)], np.full((5, 5), 0.6))
    sentinel.analyze_current(parcel)
    cat = [kw for m, u, kw in http.calls if "catalog" in u][0]
    assert cat["headers"]["Accept"] == "application/geo+json"   # application/json → HTTP 406 w CDSE
    st = [kw["json"] for m, u, kw in http.calls if "statistics" in u][0]
    assert st["aggregation"]["timeRange"]["from"].endswith("T00:00:00Z")
    assert "cnt" in st["aggregation"]["evalscript"]


def _ndvi_ndmi_png(ndvi: np.ndarray, ndmi: np.ndarray, ndwi: np.ndarray = None) -> bytes:
    """Raster 3-kanałowy jak z EVALSCRIPT_PNG: R = NDVI, G = NDMI, B = NDWI (0 = brak)."""
    def enc(v):
        return np.where(np.isfinite(v), 1 + np.round((np.clip(v, -1, 1) + 1) / 2 * 254), 0).astype(np.uint8)
    blue = enc(ndwi) if ndwi is not None else np.zeros(ndvi.shape, dtype=np.uint8)
    rgb = np.stack([enc(ndvi), enc(ndmi), blue], axis=-1)
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


def test_ndmi_layer_and_true_color_failure_is_only_warning(monkeypatch, http, parcel):
    cdse_settings(monkeypatch)
    nd = np.full((10, 10), 0.6, dtype=np.float32)
    nm = np.full((10, 10), 0.25, dtype=np.float32)
    nm[0, 0] = np.nan
    _install_cdse(http, [_stats_item("2026-11-12", 0.6, 0.95)])
    nw = np.full((10, 10), -0.5, dtype=np.float32)
    nw[0, :3] = 0.3                                   # 3% pikseli: woda stojąca (NDWI > 0,2)
    png = _ndvi_ndmi_png(nd, nm, nw)

    def process(u, kw):
        if "B02" in kw["json"]["evalscript"]:          # zdjęcie w barwach naturalnych – błąd serwera
            return FakeResp(500, {"error": "x"})
        assert all(b in kw["json"]["evalscript"] for b in ("B8A", "B11", "B03"))
        return FakeResp(200, text="", content=png)

    http.add("/api/v1/process", process, "POST")
    res = sentinel.analyze_current(parcel)
    assert res["ndvi"]["mean"] == pytest.approx(0.6)
    lm = res["layer_ndmi"]
    assert lm["mean"] == pytest.approx(0.25, abs=0.01) and lm["acquired_on"] == "2026-11-12"
    assert lm["legend"][0]["color"] == "#a50026" and "nie jest wilgotność gleby" in lm["note"]
    lw = res["layer_ndwi"]
    assert lw["water_pct"] == pytest.approx(3.0) and lw["wet_pct"] == pytest.approx(3.0)
    assert lw["mean"] == pytest.approx(-0.476, abs=0.01) and "woda stojąca" in lw["legend"][-1]["label"] and "NDMI" in lw["note"]
    assert res["layer_true_color"] is None          # zdjęcie pobierane osobno (etap 2)
    tc = sentinel.true_color_layer(parcel)
    assert tc["layer"] is None and any("barwach naturalnych" in w for w in tc["warnings"])
    dec = sentinel.decode_ndvi_png(png, channel=1)
    assert np.isnan(dec[0, 0]) and dec[1, 1] == pytest.approx(0.25, abs=0.01)

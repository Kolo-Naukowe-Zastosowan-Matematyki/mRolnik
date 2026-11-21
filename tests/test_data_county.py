"""Działka na tle powiatu: granica z ULDK, maska pól uprawnych, rozkład NDVI i brak danych."""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from src.data import county
from tests.test_data_support import FakeResp, cdse_settings, clean_state, http, ndvi_png  # noqa: F401

COUNTY_TXT = "0\n3025|powiat średzki|SRID=4326;MULTIPOLYGON(((17.0 52.1,17.4 52.1,17.4 52.35,17.0 52.35,17.0 52.1)))\n"


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8), mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _install(http, day_values=None, uldk=COUNTY_TXT):
    http.add("uldk.gugik.gov.pl", lambda u, kw: FakeResp(200, text=uldk), "GET")
    http.add("openid-connect/token", lambda u, kw: FakeResp(200, {"access_token": "TOKEN", "expires_in": 600}), "POST")

    def process(u, kw):
        body = kw["json"]
        w, h = body["output"]["width"], body["output"]["height"]
        if "ORBIT" in body["evalscript"]:
            assert body["input"]["data"][0]["dataFilter"]["timeRange"]["from"].startswith("2026-04-15")
            mask = np.full((h, w), 3, dtype=np.uint8)
            mask[:, : w // 4] = 2          # łąki / las – poza porównaniem
            mask[0, :] = 0                  # poza powiatem
            return FakeResp(200, text="", content=_png(mask))
        vals = day_values(h, w) if day_values else np.tile(np.linspace(0.1, 0.9, w, dtype=np.float32), (h, 1))
        return FakeResp(200, text="", content=ndvi_png(vals))

    http.add("/api/v1/process", process, "POST")


def test_parse_county_response_simplifies_geometry():
    r = county.parse_county_response(COUNTY_TXT)
    assert r["teryt"] == "3025" and r["name"] == "powiat średzki"
    assert r["wkt"].startswith("MULTIPOLYGON") or r["wkt"].startswith("POLYGON")
    assert county.parse_county_response("-1 brak wyników\n") is None
    with pytest.raises(ValueError):
        county.parse_county_response("0\nXX|powiat|POINT(1 2)\n")


def test_season_is_last_completed():
    from datetime import date
    assert county.season_for(date(2026, 11, 10)) == 2026
    assert county.season_for(date(2026, 7, 1)) == 2025


def test_compare_counts_only_cropland():
    mask = np.full((40, 40), 3, dtype=np.uint8)
    mask[:, :20] = 2
    ndvi = np.tile(np.linspace(0.0, 1.0, 40, dtype=np.float32), (40, 1))
    ndvi[:, :20] = 0.99   # pomijane (nie pole uprawne)
    r = county.compare(mask, ndvi, 0.75)
    assert r["n_px"] == 800 and len(r["segments"]) == county.SEGMENTS
    assert 0.5 <= r["median"] <= 0.8
    assert 40 <= r["rank_pct"] <= 60
    assert county.compare(mask[:10, :], ndvi[:10, :], 0.5) is None   # za mało pikseli


def test_county_ndvi_ok(monkeypatch, http):
    cdse_settings(monkeypatch, configured=True)
    _install(http)
    r = county.county_ndvi(52.2, 17.2, "2026-11-10", 0.8)
    assert r["status"] == "ok", r
    assert r["county"] == {"teryt": "3025", "name": "powiat średzki"}
    assert r["season"] == 2026 and r["provenance"]["kind"] == "pomiar"
    assert r["rank_pct"] >= 80
    assert r["segments"][0]["ndvi"] < r["segments"][-1]["ndvi"]
    assert r["cropland_km2"] > 0
    # drugi raz z pamięci, z inną wartością działki
    n = http.count("/api/v1/process")
    r2 = county.county_ndvi(52.2, 17.2, "2026-11-10", 0.2)
    assert http.count("/api/v1/process") == n and r2["rank_pct"] < r["rank_pct"]


def test_county_ndvi_cloudy_day(monkeypatch, http):
    cdse_settings(monkeypatch, configured=True)
    _install(http, day_values=lambda h, w: np.full((h, w), np.nan, dtype=np.float32))
    r = county.county_ndvi(52.2, 17.2, "2026-11-10", 0.5)
    assert r["status"] == "brak" and "chmur" in r["provenance"]["note"]


def test_county_ndvi_not_configured(monkeypatch, http):
    cdse_settings(monkeypatch, configured=False)
    r = county.county_ndvi(52.2, 17.2, "2026-11-10", 0.5)
    assert r["status"] == "brak" and r["provenance"]["kind"] == "brak"
    assert http.calls == []


def test_county_ndvi_uldk_down(monkeypatch, http):
    cdse_settings(monkeypatch, configured=True)
    r = county.county_ndvi(52.2, 17.2, "2026-11-10", 0.5)
    assert r["status"] == "brak" and "ULDK" in r["provenance"]["note"]


def test_chunk_means_masks_and_thresholds():
    mask = np.full((40, 40), 3, dtype=np.uint8)
    mask[:, :10] = 2
    raw = np.zeros((40, 40, 4), dtype=np.uint8)
    raw[..., 0] = 1 + round((0.6 + 1) / 2 * 254)      # cały miesiąc bezchmurny
    raw[:5, :, 1] = 200                              # za mało pól bez chmur
    raw[..., 3] = 0                                  # brak zdjęć
    r = county.chunk_means(mask, raw, ["2025-05", "2025-06", "2025-07", "2025-08"])
    assert r["2025-05"]["ndvi"] == pytest.approx(0.6, abs=0.01) and r["2025-05"]["n_px"] == 1200
    assert r["2025-06"]["ndvi"] is None and r["2025-08"]["ndvi"] is None


def test_county_monthly_weighted(monkeypatch, http):
    from datetime import date as d_
    cdse_settings(monkeypatch, configured=True)
    second = "0\n3064|powiat poznański|SRID=4326;MULTIPOLYGON(((16.7 52.3,17.0 52.3,17.0 52.5,16.7 52.5,16.7 52.3)))\n"
    http.add("uldk.gugik.gov.pl", lambda u, kw: FakeResp(200, text=second if "16.8" in kw["params"]["xy"] else COUNTY_TXT), "GET")
    http.add("openid-connect/token", lambda u, kw: FakeResp(200, {"access_token": "TOKEN", "expires_in": 600}), "POST")

    def process(u, kw):
        body = kw["json"]
        w, h = body["output"]["width"], body["output"]["height"]
        if "MONTHS" not in body["evalscript"]:
            return FakeResp(200, text="", content=_png(np.full((h, w), 3, dtype=np.uint8)))
        assert "scenes.orbits" in body["evalscript"]
        v = 0.4 if body["input"]["bounds"]["bbox"][0] < 16.9 else 0.8
        rgba = np.full((h, w, 4), 1 + round((v + 1) / 2 * 254), dtype=np.uint8)
        buf = io.BytesIO()
        Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
        return FakeResp(200, text="", content=buf.getvalue())

    http.add("/api/v1/process", process, "POST")
    r = county.county_monthly([(52.2, 17.2, 3.0), (52.4, 16.8, 1.0)], "2025-05", "2025-08")
    assert [c["teryt"] for c in r["counties"]] == ["3025", "3064"]
    assert [m["month"] for m in r["months"]] == ["2025-05", "2025-06", "2025-07", "2025-08"]
    assert r["months"][0]["ndvi"] == pytest.approx(0.8 * 0.75 + 0.4 * 0.25, abs=0.01)
    assert r["provenance"]["kind"] == "pomiar"
    n = http.count("/api/v1/process")
    county.county_monthly([(52.2, 17.2, 3.0), (52.4, 16.8, 1.0)], "2025-05", "2025-08")
    assert http.count("/api/v1/process") == n          # z pamięci

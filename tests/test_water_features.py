"""Cieki i rowy dla fragmentu mapy (src/data/water_features.py, GET /api/water/features) – bez sieci, na próbce cache BDOT10k."""
from __future__ import annotations

import json
import time

import numpy as np
import pytest
import shapely
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Polygon, box

from src.data import bdot10k, parcel_search, water_features as wf
from src.data.cadastre import reproject, transformer_to_2180

BBOX = (17.15, 52.20, 17.20, 52.23)   # ok. 3,4 × 3,3 km
META = {"last_modified": "Mon, 31 Aug 2026 08:19:48 GMT", "fetched_at": "2026-11-19T10:00:00Z", "source_url": "x"}


def _tile_2180():
    return reproject(box(*BBOX), transformer_to_2180)


def _layers():
    t = _tile_2180()
    x0, y0, x1, y1 = t.bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    river = LineString([(x0 - 2000, cy), (x1 + 2000, cy)])                     # wychodzi poza kafel z obu stron
    ditch = LineString([(cx, cy - 500), (cx, cy + 500)])
    far = LineString([(x1 + 5000, cy), (x1 + 6000, cy)])                      # poza kaflem
    lake = Polygon([(cx + 300, cy + 300), (cx + 600, cy + 300), (cx + 600, cy + 600), (cx + 300, cy + 600)])

    def lay(cls, geoms, attrs):
        return bdot10k.Layer("3025", cls, np.array(geoms, dtype=object), attrs, dict(META))
    return {
        "SWRS_L": lay("SWRS_L", [river, far], [{"nazwa": "Brodek", "rodzaj": "strumień, potok lub struga", "identyfikatorMPHP": "185492",
                                                "szerokosc": "2.0"}, {"nazwa": "Daleka"}]),
        "SWKN_L": lay("SWKN_L", [], []),
        "SWRM_L": lay("SWRM_L", [ditch], [{"szerokosc": "1.0"}]),
        "PTWP_A": lay("PTWP_A", [lake], [{"rodzaj": "woda stojąca", "identyfikatorMPHP": "1854379"}]),
    }


@pytest.fixture
def world(monkeypatch):
    wf._CACHE.clear()
    calls = {"ensure": 0}
    county = {"teryt": "3025", "name": "średzki", "wkt": box(16.0, 51.0, 18.5, 53.0).wkt}
    monkeypatch.setattr(parcel_search, "_county_at", lambda lat, lon: county)
    layers = _layers()

    def ensure(teryt, classes, progress=None):
        calls["ensure"] += 1
        assert teryt == "3025" and set(classes) == set(wf.CLASSES)
        return layers
    monkeypatch.setattr(bdot10k, "ensure_layers", ensure)
    return calls


def test_features_clipped_with_attributes(world):
    r = wf.features(BBOX, 15)
    by = {f["properties"]["cls"]: f for f in r["features"]}
    assert set(by) == {"SWRS_L", "SWRM_L", "PTWP_A"}                          # „Daleka” poza kaflem – pominięta
    river = by["SWRS_L"]
    assert river["properties"] == {"key": "3025:SWRS_L:0", "cls": "SWRS_L", "kind": "ciek", "version": "2026-08-31",
                                   "name": "Brodek", "rodzaj": "strumień, potok lub struga", "mphp": "185492", "width_m": 2.0}
    # przycięta do kafla (z zapasem na przeliczenie układów)
    xs = [c[0] for c in river["geometry"]["coordinates"]]
    assert min(xs) >= BBOX[0] - 1e-4 and max(xs) <= BBOX[2] + 1e-4
    assert by["SWRM_L"]["properties"].get("name") is None and by["SWRM_L"]["properties"]["kind"] == "rów melioracyjny"
    assert by["PTWP_A"]["geometry"]["type"] == "Polygon" and by["PTWP_A"]["properties"]["mphp"] == "1854379"
    assert r["provenance"]["kind"] == "pomiar" and "BDOT10k" in r["provenance"]["source"]
    assert r["cached"] is False


def test_features_cached_per_tile_and_zoom(world):
    wf.features(BBOX, 15)
    r2 = wf.features(BBOX, 16)                     # zoom 15 i 16 – osobne wpisy (inne uproszczenie)
    r3 = wf.features(BBOX, 16)
    assert world["ensure"] == 2 and r2["cached"] is False and r3["cached"] is True
    assert wf.features(BBOX, 19)["cached"] is False and wf.features(BBOX, 18)["cached"] is True   # ≥17 – jeden wpis


def test_limits_zoom_and_span(world):
    with pytest.raises(wf.TooLarge):
        wf.features(BBOX, 12)
    with pytest.raises(wf.TooLarge):
        wf.features((17.0, 52.0, 17.2, 52.2), 15)  # ok. 13,7 × 22 km
    assert world["ensure"] == 0


def test_tolerance_is_sub_pixel():
    assert wf.tolerance_m(13, 52.0) < 6.0 and wf.tolerance_m(17, 52.0) < 0.4


def test_route(world):
    from src.web import routes_water
    app = FastAPI()
    app.include_router(routes_water.router)
    c = TestClient(app)
    ok = c.get("/api/water/features", params={"bbox": ",".join(map(str, BBOX)), "zoom": 15})
    assert ok.status_code == 200 and ok.json()["count"] == 3
    assert c.get("/api/water/features", params={"bbox": ",".join(map(str, BBOX)), "zoom": 11}).status_code == 422
    assert c.get("/api/water/features", params={"bbox": "1,2,3", "zoom": 15}).status_code == 422
    assert c.get("/api/water/features", params={"bbox": "2,40,3,41", "zoom": 15}).status_code == 422   # poza Polską


def test_cache_rebuilt_when_water_attributes_change(tmp_path, monkeypatch):
    """Stary wpis cache klasy wód (bez nazw / ID MPHP) jest pobierany ponownie; innych klas to nie dotyczy."""
    monkeypatch.setattr(bdot10k, "CACHE_DIR", tmp_path)
    for cls in ("SWRM_L", "SKJZ_L"):
        bdot10k._write_cache("3025", cls, [shapely.to_wkb(LineString([(0, 0), (1, 1)]))], [{}], {"stored_ts": time.time()})
    assert bdot10k._read_meta("3025", "SWRM_L") is not None
    for cls in ("SWRM_L", "SKJZ_L"):                # symulacja wpisu sprzed zmiany: bez pola "keep"
        _, jp = bdot10k._paths("3025", cls)
        doc = json.load(open(jp, encoding="utf-8"))
        doc.pop("keep")
        json.dump(doc, open(jp, "w", encoding="utf-8"))
    assert bdot10k._read_meta("3025", "SWRM_L") is None        # SWRM_L: dawniej bez atrybutów, teraz szerokość
    assert bdot10k._read_meta("3025", "SKJZ_L") is not None    # drogi bez zmian – cache zostaje

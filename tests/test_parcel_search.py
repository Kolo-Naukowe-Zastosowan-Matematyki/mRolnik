"""Wyszukiwarka działek: BDOT10k (parsowanie GML, odczyt ZIP przez Range, cache), PRNG, NMT, ISOK, kryteria i kolejność etapów.

Bez sieci: małe próbki GML/JSON, ZIP budowany w pamięci i serwowany przez atrapę HTTP z obsługą nagłówka Range.
"""
from __future__ import annotations

import io
import json
import math
import zipfile
from typing import Any, Dict, Optional

import numpy as np
import pytest
import shapely
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, Polygon, box

from src.data import bdot10k, flood, parcel_search as ps, places_prng, terrain
from src.data.cadastre import ParcelData, _build_parcel
from tests.test_data_support import FakeResp, clean_state, http  # noqa: F401

OT = 'xmlns:ot="urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:2.0"'
GMLNS = 'xmlns:gml="http://www.opengis.net/gml/3.2"'


def gml_doc(cls: str, members: str) -> bytes:
    return (f'﻿<?xml version="1.0" encoding="UTF-8"?>\n<gml:FeatureCollection {GMLNS} {OT} gml:id="GUGiK_OT_{cls}">'
            f"{members}</gml:FeatureCollection>").encode("utf-8")


def member(cls: str, attrs: Dict[str, str], geom: str) -> str:
    a = "".join(f"<ot:{k}>{v}</ot:{k}>" for k, v in attrs.items())
    return (f'<gml:featureMember><ot:OT_{cls} gml:id="x"><ot:lokalnyId>1</ot:lokalnyId>{a}'
            f"<ot:geometria>{geom}</ot:geometria></ot:OT_{cls}></gml:featureMember>")


def curve(coords) -> str:
    pl = " ".join(f"{x} {y}" for x, y in coords)
    return (f'<gml:Curve srsName="EPSG:2180" srsDimension="2"><gml:segments><gml:LineStringSegment>'
            f"<gml:posList>{pl}</gml:posList></gml:LineStringSegment></gml:segments></gml:Curve>")


def poly(coords, hole=None) -> str:
    pl = " ".join(f"{x} {y}" for x, y in coords)
    inner = ""
    if hole:
        inner = "<gml:interior><gml:LinearRing><gml:posList>" + " ".join(f"{x} {y}" for x, y in hole) + "</gml:posList></gml:LinearRing></gml:interior>"
    return (f'<gml:Polygon srsName="EPSG:2180"><gml:exterior><gml:LinearRing><gml:posList>{pl}</gml:posList></gml:LinearRing>'
            f"</gml:exterior>{inner}</gml:Polygon>")


def sq(x0, y0, s):
    return [(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s), (x0, y0)]


# ------------------------------------------------------------------ parsowanie GML
def test_parse_class_stream_geometry_and_attributes():
    doc = gml_doc("SWRS_L", member("SWRS_L", {"nazwa": "Kopel", "rodzaj": "rzeka", "szerokosc": "7.0"}, curve([(0, 0), (100, 0), (200, 50)]))
                  + member("SWRS_L", {"nazwa": "Bez geometrii"}, ""))
    wkbs, attrs = bdot10k.parse_class_stream(io.BytesIO(doc), ("nazwa", "rodzaj"))
    assert len(wkbs) == 1                        # obiekt bez geometrii pominięty (nie zgadujemy)
    g = shapely.from_wkb(wkbs[0])
    assert g.geom_type == "LineString" and g.length == pytest.approx(100 + math.hypot(100, 50))
    assert attrs == [{"nazwa": "Kopel", "rodzaj": "rzeka"}]   # tylko zachowywane atrybuty


def test_parse_polygon_with_hole_and_point():
    doc = gml_doc("PTLZ_A", member("PTLZ_A", {"rodzaj": "las"}, poly(sq(0, 0, 100), hole=sq(40, 40, 20))))
    wkbs, _ = bdot10k.parse_class_stream(io.BytesIO(doc), ("rodzaj",))
    g = shapely.from_wkb(wkbs[0])
    assert g.area == pytest.approx(100 * 100 - 20 * 20)
    pt = bdot10k.parse_geometry(__import__("xml.etree.ElementTree", fromlist=["x"]).fromstring(
        f'<gml:Point {GMLNS}><gml:pos>337223.65 489440.31</gml:pos></gml:Point>'))
    assert (pt.x, pt.y) == (337223.65, 489440.31)   # kolejność E N


# ------------------------------------------------------------------ ZIP przez Range + cache
class RangeResp(FakeResp):
    def __init__(self, status, content, headers):
        super().__init__(status, content=content, text="")
        self.headers = headers


def _zip_bytes(files: Dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _serve_zip(http, blob: bytes):
    def handler(url, kw):
        rng = (kw.get("headers") or {}).get("Range", "")
        a, b = rng.split("=")[1].split("-")
        a, b = int(a), min(int(b), len(blob) - 1)
        return RangeResp(206, blob[a:b + 1], {"Content-Range": f"bytes {a}-{b}/{len(blob)}", "ETag": '"e1"',
                                               "Last-Modified": "Mon, 31 Aug 2026 08:19:48 GMT"})
    http.add("opendata.geoportal.gov.pl/bdot10k", handler)


@pytest.fixture
def bdot_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(bdot10k, "CACHE_DIR", tmp_path / "bdot10k")
    bdot10k.reset_memory()
    yield tmp_path
    bdot10k.reset_memory()


def test_ensure_layers_range_download_cache_and_absent_class(http, bdot_tmp):
    pre = "PL.PZGiK.308.9999/BDOT10k/PL.PZGiK.308.9999__OT_"
    blob = _zip_bytes({
        pre + "SWRS_L.xml": gml_doc("SWRS_L", member("SWRS_L", {"nazwa": "Rzeka"}, curve([(0, 0), (1000, 0)]))),
        pre + "RTLW_L.xml": b"x" * 200_000,   # duża klasa, której nie potrzebujemy – nie może być pobrana
    })
    _serve_zip(http, blob)
    lay = bdot10k.ensure_layers("9999", ["SWRS_L", "SKTR_L"])
    assert len(lay["SWRS_L"].geoms) == 1 and lay["SWRS_L"].attrs[0]["nazwa"] == "Rzeka"
    assert len(lay["SKTR_L"].geoms) == 0          # brak klasy w paczce = brak obiektów w powiecie
    first = len(http.calls)
    assert first >= 2
    bdot10k.reset_memory()
    again = bdot10k.ensure_layers("9999", ["SWRS_L", "SKTR_L"])   # z dysku, bez sieci
    assert len(http.calls) == first and len(again["SWRS_L"].geoms) == 1
    prov = again["SWRS_L"].provenance()
    assert prov["kind"] == "pomiar" and "9999" in prov["note"] and prov["observed_at"].startswith("2026-08-31")


def test_ensure_layers_network_error_is_source_error(http, bdot_tmp):
    with pytest.raises(bdot10k.SourceError):
        bdot10k.ensure_layers("9998", ["SWRS_L"])


def _layer(cls, geoms, attrs=None):
    arr = np.array(geoms, dtype=object)
    return bdot10k.Layer("0000", cls, arr, attrs or [{} for _ in geoms], {"fetched_at": "2026-11-18T00:00:00Z"})


def test_nearest_within_radius_and_predicate():
    lay = _layer("SKJZ_L", [LineString([(0, 50), (100, 50)]), LineString([(0, 15), (100, 15)])],
                 [{"kategoriaZarzadzania": "gminna"}, {"kategoriaZarzadzania": "wewnętrzna"}])
    parcel = box(0, 0, 100, 10)
    d, a = bdot10k.nearest([lay], parcel, 500)
    assert d == pytest.approx(5) and a["kategoriaZarzadzania"] == "wewnętrzna"
    d, a = bdot10k.nearest([lay], parcel, 500, lambda x: x["kategoriaZarzadzania"] == "gminna")
    assert d == pytest.approx(40)
    assert bdot10k.nearest([lay], parcel, 3) == (None, None)       # brak obiektu w promieniu – wynik, nie brak danych


def test_covered_fraction():
    lay = _layer("PTLZ_A", [box(0, 0, 50, 100), box(40, 0, 60, 100)])
    assert bdot10k.covered_fraction([lay], box(0, 0, 100, 100)) == pytest.approx(0.6)   # suma bez podwójnego liczenia


# ------------------------------------------------------------------ geometria działki
def test_shape_metrics_square_and_strip():
    m = ps.shape_metrics(box(0, 0, 100, 100))
    assert m["pp"] == pytest.approx(math.pi / 4, abs=1e-3) and m["rect"] == pytest.approx(1.0) and m["elongation"] == pytest.approx(1.0)
    assert m["min_width_m"] == pytest.approx(100, abs=1)
    strip = ps.shape_metrics(box(0, 0, 400, 20))
    assert strip["elongation"] == pytest.approx(20) and strip["min_width_m"] == pytest.approx(20, abs=1) and strip["pp"] < 0.2


def test_merge_groups_adjacent_and_gap():
    geoms = {0: box(0, 0, 100, 100), 1: box(100.5, 0, 200, 100), 2: box(500, 0, 600, 100), 3: box(200, 0, 300, 100)}
    groups = sorted(sorted(g) for g in ps.merge_groups(geoms, gap_m=1.0))
    assert groups == [[0, 1, 3], [2]]


# ------------------------------------------------------------------ ocena i kolejność
def test_overall_unknown_required_is_to_check_not_rejected():
    cfg = {"area": {"required": True}, "slope": {"required": True}, "water": {"required": False}}
    st, score, failed, unknown = ps.overall({"area": ps.crit("ok"), "slope": ps.crit("brak"), "water": ps.crit("fail")}, cfg)
    assert st == "do_sprawdzenia" and failed == [] and unknown == ["Nachylenie terenu"]
    st, *_ = ps.overall({"area": ps.crit("fail"), "slope": ps.crit("brak"), "water": ps.crit("ok")}, cfg)
    assert st == "nie"
    st, score, *_ = ps.overall({"area": ps.crit("ok"), "slope": ps.crit("ok"), "water": ps.crit("fail")}, cfg)
    assert st == "spelnia" and score == pytest.approx(80)       # 4 z 5 pkt (konieczne ×2)


def _parcel(pid: str, x0: float, side: float) -> ParcelData:
    wkt = Polygon(sq(x0 + 380000, 486000, side)).wkt
    return _build_parcel(pid, wkt, "wielkopolskie", "powiat średzki", "Zaniemyśl", "Śnieciska")


def test_run_cheap_filters_first_and_missing_data_not_rejected(monkeypatch):
    parcels = {"302505_5.0001.1": _parcel("302505_5.0001.1", 0, 100), "302505_5.0001.2": _parcel("302505_5.0001.2", 1000, 50)}
    monkeypatch.setattr(ps, "_resolve", lambda it: parcels.get(it.get("parcel_id")))
    calls = []

    def fake_slope(p):
        calls.append(p.parcel_id)
        return {"value": None, "provenance": {"kind": "brak", "note": "NMT niedostępny (test)", "source": "NMT"}}

    monkeypatch.setattr(terrain, "parcel_slope", fake_slope)
    pool = [{"parcel_id": pid, "lat": 52.2, "lon": 17.2, "id_tag": pid[-1], "farm": "F"} for pid in parcels] + \
           [{"parcel_id": "302505_5.0001.9", "lat": 52.2, "lon": 17.2, "id_tag": "brak", "farm": "F"}]
    cfg = {"area": {"required": True, "min_ha": 0.5, "max_ha": None}, "slope": {"required": True, "max_mean_pct": 5, "max_p90_pct": None}}
    job = ps.Job(pool, cfg)
    ps.run(job)
    snap = job.snapshot()
    by = {r["name"]: r for r in snap["results"]}
    assert calls == ["302505_5.0001.1"]                         # mała działka odrzucona tanio – NMT nie był pytany
    assert by["1"]["status"] == "do_sprawdzenia"                # brak danych NMT ≠ odrzucenie
    assert by["2"]["status"] == "nie" and by["2"]["criteria"]["slope"]["status"] == "skipped"
    assert by["brak"]["status"] == "brak_geometrii"
    assert snap["counts"]["do_sprawdzenia"] == 1 and snap["provenance"]["area"]["kind"] == "pomiar"


def test_merge_stage_groups(monkeypatch):
    parcels = {f"302505_5.0001.{i}": _parcel(f"302505_5.0001.{i}", x, 200) for i, x in ((1, 0), (2, 200), (3, 3000))}
    monkeypatch.setattr(ps, "_resolve", lambda it: parcels.get(it.get("parcel_id")))
    pool = [{"parcel_id": pid, "lat": 52.2, "lon": 17.2, "id_tag": pid, "farm": "F"} for pid in parcels]
    job = ps.Job(pool, {"merge": {"required": True, "min_ha": 7}})
    ps.run(job)
    snap = job.snapshot()
    st = {r["parcel_id"]: r["status"] for r in snap["results"]}
    assert st == {"302505_5.0001.1": "spelnia", "302505_5.0001.2": "spelnia", "302505_5.0001.3": "nie"}
    assert len(snap["groups"]) == 1 and snap["groups"][0]["area_ha"] == pytest.approx(8.0)


# ------------------------------------------------------------------ PRNG, NMT, ISOK
PRNG_XML = """<?xml version='1.0' encoding="UTF-8" ?>
<wfs:FeatureCollection xmlns:ms="http://mapserver.gis.umn.edu/mapserver" xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="2" numberReturned="2">
<wfs:member><ms:M1_UrzedoweNazwyMiejscowosci gml:id="M1.1"><ms:msGeometry><gml:Point srsName="urn:ogc:def:crs:EPSG::4326"><gml:pos>52.229385 17.275207</gml:pos></gml:Point></ms:msGeometry>
<ms:IDENTYFIKATORPRNG>132000</ms:IDENTYFIKATORPRNG><ms:IDENTYFIKATORSIMC>0973000</ms:IDENTYFIKATORSIMC><ms:NAZWAGLOWNA>Środa Wielkopolska</ms:NAZWAGLOWNA>
<ms:RODZAJOBIEKTU>miasto</ms:RODZAJOBIEKTU><ms:GMINA>Środa Wielkopolska</ms:GMINA><ms:POWIAT>średzki</ms:POWIAT><ms:WOJEWODZTWO>wielkopolskie</ms:WOJEWODZTWO></ms:M1_UrzedoweNazwyMiejscowosci></wfs:member>
<wfs:member><ms:M1_UrzedoweNazwyMiejscowosci gml:id="M1.2"><ms:msGeometry><gml:Point><gml:pos>52.3 17.3</gml:pos></gml:Point></ms:msGeometry>
<ms:NAZWAGLOWNA>Andrzejpole</ms:NAZWAGLOWNA><ms:RODZAJOBIEKTU>osada</ms:RODZAJOBIEKTU></ms:M1_UrzedoweNazwyMiejscowosci></wfs:member>
</wfs:FeatureCollection>"""


def test_parse_towns_only_cities_lat_lon_order():
    towns = places_prng.parse_towns(PRNG_XML)
    assert [t["name"] for t in towns] == ["Środa Wielkopolska"]
    assert towns[0]["lat"] == pytest.approx(52.229385) and towns[0]["lon"] == pytest.approx(17.275207) and towns[0]["prng_id"] == "132000"


def test_towns_in_bbox_uses_server_filter_and_cache(http):
    places_prng._cache.clear()
    http.add("PRNG/WFS", lambda u, kw: FakeResp(200, text=PRNG_XML))
    a = places_prng.towns_in_bbox((17.1, 52.1, 17.4, 52.3))
    b = places_prng.towns_in_bbox((17.12, 52.12, 17.38, 52.28))        # ta sama komórka siatki 0,1° → cache
    assert a == b and http.count("PRNG/WFS") == 1
    flt = http.calls[0][2]["params"]["filter"]
    assert "RODZAJOBIEKTU" in flt and "miasto" in flt and "<gml:lowerCorner>52.1 17.1</gml:lowerCorner>" in flt


def test_slope_stats_plane_and_nodata():
    y, x = np.mgrid[0:50, 0:50].astype(float)
    dem = 100 + 0.05 * x                       # 5% spadku w kierunku x, siatka 1 m
    mask = np.ones_like(dem, dtype=bool)
    st = terrain.slope_stats(dem, 1.0, mask)
    assert st["mean_pct"] == pytest.approx(5.0, abs=0.01) and st["p90_pct"] == pytest.approx(5.0, abs=0.01)
    dem2 = dem.copy()
    dem2[:, :40] = 0.0                         # 0,0 = luka w NMT → za mało ważnych pikseli
    assert terrain.slope_stats(dem2, 1.0, mask) is None


def _hazard(rp: float, coords, label: str) -> Dict[str, Any]:
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coords]},
            "properties": {"likelihoodOfOccurrence": {"LikelihoodOfOccurrence": {
                "qualitativeLikelihood": label, "quantitativeLikelihood": {"QuantitativeLikelihood": {"returnPeriod": rp}}}},
                "typeOfHazard": {"hazardCategory": {"@title": "fluvial"}}, "validityPeriod": {"endPosition": "2025-12-22"}}}


def test_flood_share_by_scenario(http, monkeypatch):
    flood._cache.clear()
    p = _build_parcel("306401_1.0001.1", Polygon(sq(357000, 505000, 100)).wkt, "wielkopolskie", "Poznań", "Poznań", "x")
    w, s, e, n = p.polygon_wgs84.bounds
    mid = (w + e) / 2
    q1 = [[w - 0.01, s - 0.01], [mid, s - 0.01], [mid, n + 0.01], [w - 0.01, n + 0.01], [w - 0.01, s - 0.01]]   # lewa połowa
    data = {"type": "FeatureCollection", "numberReturned": 1, "numberMatched": 1, "features": [_hazard(100, q1, "scenariusz Q 1% (raz na 100 lat)")]}
    http.add("INSPIRE_NZ_HY_MZPMRP_WFS", lambda u, kw: FakeResp(200, data))
    r = flood.flood_share(p)
    assert r["value"]["q10"] == 0.0 and r["value"]["q1"] == pytest.approx(50, abs=2) and r["value"]["q02"] == pytest.approx(50, abs=2)
    assert r["provenance"]["kind"] == "pomiar" and "2025-12-22" in r["provenance"]["note"]
    params = [c[2]["params"] for c in http.calls if "INSPIRE" in c[1]]
    assert params[0]["outputFormat"] == "application/json"
    s_, w_, *_ = (float(v) for v in params[0]["bbox"].split(","))
    assert 49 < s_ < 55 and 14 < w_ < 25                          # bbox: szerokość, długość
    calls = len(http.calls)
    flood.flood_share(p)
    assert len(http.calls) == calls                               # kafel z cache


def test_flood_service_down_is_missing_not_zero(http):
    flood._cache.clear()
    p = _build_parcel("306401_1.0001.2", Polygon(sq(357000, 505000, 100)).wkt, "", "", "", "")
    r = flood.flood_share(p)
    assert r["value"] is None and r["provenance"]["kind"] == "brak"


# ------------------------------------------------------------------ API
@pytest.fixture
def api_client():
    from src.web import routes_parcelsearch
    app = FastAPI()
    app.include_router(routes_parcelsearch.router)
    return TestClient(app)


def test_api_validation(api_client):
    assert api_client.post("/api/parcel-search", json={"pool": [], "criteria": {"area": {}}}).status_code == 422
    one = [{"lat": 52.2, "lon": 17.2}]
    assert api_client.post("/api/parcel-search", json={"pool": one, "criteria": {}}).status_code == 422            # brak kryteriów
    assert api_client.post("/api/parcel-search", json={"pool": one, "criteria": {"town": {"max_km": 500}}}).status_code == 422
    assert api_client.post("/api/parcel-search", json={"pool": one, "criteria": {"area": {"x": 1}}}).status_code == 422
    assert api_client.post("/api/parcel-search", json={"pool": [{"lat": 10, "lon": 17}], "criteria": {"area": {}}}).status_code == 422
    assert api_client.get("/api/parcel-search/nie-id").status_code == 422
    assert api_client.get("/api/parcel-search/" + "0" * 32).status_code == 404


def test_api_job_roundtrip(api_client, monkeypatch):
    p = _parcel("302505_5.0001.1", 0, 100)
    monkeypatch.setattr(ps, "_resolve", lambda it: p)
    r = api_client.post("/api/parcel-search", json={"pool": [{"lat": 52.2, "lon": 17.2, "parcel_id": "302505_5.0001.1", "id_tag": "A"}],
                                                    "criteria": {"area": {"min_ha": 0.5}, "shape": {"enabled": False}}})
    assert r.status_code == 200
    jid = r.json()["job_id"]
    for _ in range(100):
        d = api_client.get(f"/api/parcel-search/{jid}").json()
        if d["state"] != "running":
            break
        __import__("time").sleep(0.05)
    assert d["state"] == "done" and d["counts"]["spelnia"] == 1 and list(d["criteria"]) == ["area"]
    assert d["results"][0]["criteria"]["area"]["text"] == "1,00 ha"


# ------------------------------------------------------------------ szybki parser = wzorcowy
def _deflate_raw(data: bytes) -> bytes:
    import zlib
    c = zlib.compressobj(6, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def _tricky_doc() -> bytes:
    multicurve = ('<gml:MultiCurve srsName="EPSG:2180"><gml:curveMember>' + curve([(0, 0), (10, 0)]) + '</gml:curveMember>'
                  '<gml:curveMember><gml:LineString><gml:posList>20 0 30 5</gml:posList></gml:LineString></gml:curveMember></gml:MultiCurve>')
    unclosed = poly([(0, 0), (50, 0), (50, 50), (0, 50)])                      # pierścień bez domknięcia
    surface = ('<gml:MultiSurface srsName="EPSG:2180"><gml:surfaceMember>' + poly(sq(0, 0, 10)) + '</gml:surfaceMember>'
               '<gml:surfaceMember>' + poly(sq(20, 0, 10)) + '</gml:surfaceMember></gml:MultiSurface>')
    bowtie = poly([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])               # niepoprawny – make_valid
    members = [
        member("X_A", {"nazwa": "Rz &amp; &quot;Kanał&quot;", "rodzaj": "rzeka"}, curve([(0, 0), (100, 0.4), (200, 0)])),
        member("X_A", {"rodzaj": "las"}, poly(sq(0, 0, 100), hole=sq(40, 40, 20))),
        member("X_A", {"nazwa": "Multi"}, multicurve),
        member("X_A", {"nazwa": "Otwarty"}, unclosed),
        member("X_A", {"nazwa": "Powierzchnia"}, surface),
        member("X_A", {"nazwa": "Kokarda"}, bowtie),
        member("X_A", {"nazwa": "Punkt"}, '<gml:Point srsName="EPSG:2180"><gml:pos>1.5 2.5</gml:pos></gml:Point>'),
        member("X_A", {"nazwa": "Pusty"}, ""),
        member("X_A", {"nazwa": "Za krótki"}, poly([(0, 0), (1, 0), (0, 0)])),
    ]
    # atrybut po geometrii (tak jak w BDOT10k, np. ot:rodzaj za ot:geometria)
    members.append(member("X_A", {}, curve([(0, 0), (5, 5)])).replace("</ot:OT_X_A>", "<ot:rodzaj>po geometrii</ot:rodzaj></ot:OT_X_A>"))
    return gml_doc("X_A", "".join(members))


def test_fast_parser_identical_to_reference():
    from src.data import bdot10k_fast
    doc = _tricky_doc()
    keep = ("nazwa", "rodzaj")
    ref_w, ref_a = bdot10k.parse_class_stream(io.BytesIO(doc), keep)
    fast_w, fast_a = bdot10k_fast.parse_stream_fast(io.BytesIO(doc), keep)
    assert fast_w == ref_w and fast_a == ref_a          # bajt w bajt te same geometrie WKB i te same atrybuty
    assert len(ref_w) == 8 and ref_a[0]["nazwa"] == 'Rz & "Kanał"' and ref_a[-1] == {"rodzaj": "po geometrii"}
    blob, offs, attrs = bdot10k_fast.parse_raw(_deflate_raw(doc), 8, keep)    # ścieżka procesu: surowy deflate z ZIP-a
    assert [blob[offs[i]:offs[i + 1]] for i in range(len(offs) - 1)] == ref_w and attrs == ref_a


def test_fast_parser_chunk_boundaries():
    from src.data import bdot10k_fast
    doc = _tricky_doc().decode("utf-8")
    keep = ("nazwa", "rodzaj")
    whole = bdot10k_fast.parse_members(bdot10k_fast.iter_members([doc]), keep)
    pieces = [doc[i:i + 37] for i in range(0, len(doc), 37)]                   # obiekty rozcięte w dowolnych miejscach
    assert bdot10k_fast.parse_members(bdot10k_fast.iter_members(pieces), keep) == whole


def test_admin_values_teryt_from_parcel_id(monkeypatch):
    p = _parcel("302505_5.0014.114/1", 0, 100)
    monkeypatch.setattr(ps, "_resolve", lambda it: p if it.get("parcel_id") else None)
    r = ps.admin_values([{"lat": 52.2, "lon": 17.2, "parcel_id": "302505_5.0014.114/1"}, {"lat": 52.2, "lon": 17.2}])
    a, b = r["items"]
    assert a["woj"] == "30" and a["powiat"] == "3025" and a["gmina"] == "302505_5" and a["gmina_name"] == "Zaniemyśl"
    assert b == {"i": 1, "resolved": False} and r["resolved"] == 1


# ------------------------------------------------------------------ EGiB WFS (wszystkie działki w obszarze)
def _egib_member(pid: str, x0: float, y0: float, side: float, extra: str = "") -> str:
    # odpowiedź WFS: urn EPSG::2180, kolejność N E
    ring = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side), (x0, y0)]
    pl = " ".join(f"{y} {x}" for x, y in ring)
    return (f'<wfs:member><ms:dzialki gml:id="dzialki.{pid}"><ms:geom><gml:Polygon srsName="urn:ogc:def:crs:EPSG::2180">'
            f"<gml:exterior><gml:LinearRing><gml:posList>{pl}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></ms:geom>"
            f"<ms:ID_DZIALKI>{pid}</ms:ID_DZIALKI><ms:NUMER_DZIALKI>{pid.rsplit('.', 1)[-1]}</ms:NUMER_DZIALKI>"
            f"<ms:NAZWA_OBREBU>ŚNIECISKA</ms:NAZWA_OBREBU><ms:NAZWA_GMINY>Zaniemyśl</ms:NAZWA_GMINY>"
            f"<ms:POLE_EWIDENYJNE>1.0000</ms:POLE_EWIDENYJNE><ms:KLASOUZYTKI_EGIB>RIIIa</ms:KLASOUZYTKI_EGIB>"
            f"<ms:GRUPA_REJESTROWA>1</ms:GRUPA_REJESTROWA>{extra}</ms:dzialki></wfs:member>")


def _egib_page(members: str, nxt: str = "") -> str:
    n = f' next="{nxt}"' if nxt else ""
    return ('<?xml version="1.0" encoding="UTF-8"?><wfs:FeatureCollection xmlns:ms="http://mapserver.gis.umn.edu/mapserver" '
            f'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="unknown"{n}>'
            f"{members}</wfs:FeatureCollection>")


def test_parse_egib_page_axes_fields_and_next():
    from src.data import bdot10k_fast
    page = _egib_page(_egib_member("302505_5.0014.1", 380000, 486000, 100), nxt="https://x/wfs?a=1&amp;STARTINDEX=1000")
    rows, nxt = bdot10k_fast.parse_egib_page(page)
    assert nxt == "https://x/wfs?a=1&STARTINDEX=1000"
    r = rows[0]
    assert set(r) == {"id", "nr", "obreb", "gmina", "pole_ha", "wkb"}          # bez klas użytków i grup rejestrowych
    g = shapely.from_wkb(r["wkb"])
    assert g.bounds == (380000, 486000, 380100, 486100)                     # (N, E) → (E, N)
    assert r["id"] == "302505_5.0014.1" and r["pole_ha"] == 1.0 and r["gmina"] == "Zaniemyśl"


def test_in_unit_teryt():
    assert ps.in_unit("302505_5.0014.1", "gmina", "302505_3") and ps.in_unit("302505_4.0001.1", "gmina", "302505_3")
    assert not ps.in_unit("302109_5.0008.18/2", "gmina", "302505_3")         # sąsiednia gmina (tylko styka się)
    assert ps.in_unit("302502_2.0001.160/1", "gmina", "302502_2") and not ps.in_unit("302502_2.0001.1", "gmina", "302502_3")
    assert ps.in_unit("302502_2.0001.1", "powiat", "3025") and ps.in_unit("302502_2.0001.1", "woj", "30")
    assert not ps.in_unit("302502_2.0001.1", "powiat", "3021")


def test_fetch_tile_paging_dedup_and_cache(http, tmp_path, monkeypatch):
    from src.data import egib
    monkeypatch.setattr(egib, "CACHE_DIR", tmp_path / "egib")
    p1 = _egib_page(_egib_member("302505_5.0014.1", 380000, 486000, 100) + _egib_member("302505_5.0014.2", 380100, 486000, 100),
                    nxt="https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza?STARTINDEX=2")
    p2 = _egib_page(_egib_member("302505_5.0014.2", 380100, 486000, 100) + _egib_member("302505_5.0014.3", 380200, 486000, 100))
    http.add("UslugaZbiorcza", lambda u, kw: FakeResp(200, text=p2 if "STARTINDEX" in u else p1))
    doc = egib.fetch_tile(253, 324)
    assert sorted(r["id"] for r in doc["rows"]) == ["302505_5.0014.1", "302505_5.0014.2", "302505_5.0014.3"]   # duplikat między stronami
    params = http.calls[0][2]["params"]
    T = int(egib.TILE_M)
    assert params["BBOX"] == f"{324 * T},{253 * T},{325 * T},{254 * T},urn:ogc:def:crs:EPSG::2180"   # N, E
    n = len(http.calls)
    egib.fetch_tile(253, 324)
    assert len(http.calls) == n and "KLASOUZYTKI" not in (tmp_path / "egib" / "253_324.json.z").read_bytes().decode("latin-1")


def _egib_job(monkeypatch, rows, hits=10, cfg=None, area=None, zone=None):
    from src.data import egib
    boundary = box(379000, 485000, 384000, 490000)
    monkeypatch.setattr(egib, "admin_boundary", lambda level, code: boundary)
    monkeypatch.setattr(egib, "hits", lambda i, j: hits)
    monkeypatch.setattr(egib, "fetch_tile", lambda i, j: {"rows": rows, "fetched_at": "2026-11-19T07:00:00Z"})
    if zone is not None:
        monkeypatch.setattr(ps, "narrowing_zones", lambda job, b: (b.intersection(zone), ["test"]))
    job = ps.Job([], cfg or {"area": {"required": True, "min_ha": 0.5, "max_ha": None}},
                 area or {"level": "gmina", "code": "302505_3", "name": "Zaniemyśl"})
    ps.run(job)
    return job.snapshot()


def _row(pid, x0, side):
    g = box(x0, 486000, x0 + side, 486000 + side)
    return {"id": pid, "nr": pid.rsplit(".", 1)[-1], "obreb": "Ś", "gmina": "Z", "pole_ha": side * side / 1e4, "wkb": shapely.to_wkb(g, hex=True)}


def test_egib_stage_unit_filter_cheap_prefilter_and_stats(monkeypatch):
    rows = [_row("302505_5.0014.1", 380000, 100), _row("302505_5.0014.2", 380200, 50),       # 1 ha i 0,25 ha
            _row("302109_5.0008.9", 380400, 100)]                                            # inna gmina – pomijana
    snap = _egib_job(monkeypatch, rows)
    assert [r["parcel_id"] for r in snap["results"]] == ["302505_5.0014.1"]
    st = snap["egib_stats"]
    assert st["fetched"] == 3 and st["in_area"] == 2 and st["cheap_rejected"] == 1   # 3 pobrane, 1 z innej gminy, 1 za mała
    assert snap["results"][0]["status"] == "spelnia" and snap["source"] == "egib"
    assert snap["provenance"]["parcel"]["source"].startswith("GUGiK – EGiB WFS")


def test_egib_narrowing_zone_drops_parcels_outside(monkeypatch):
    rows = [_row("302505_5.0014.1", 380000, 100), _row("302505_5.0014.2", 383000, 100)]
    snap = _egib_job(monkeypatch, rows, zone=box(379000, 485000, 381000, 490000))
    assert [r["parcel_id"] for r in snap["results"]] == ["302505_5.0014.1"]


def test_egib_too_big_early_and_during_fetch(monkeypatch):
    # powiat / województwo: szacunek z hits przed pobraniem geometrii – od razu „obszar za duży”
    snap = _egib_job(monkeypatch, [], hits=10_000_000, area={"level": "powiat", "code": "3025", "name": "średzki"})
    assert snap["too_big"]["early"] and snap["results"] == [] and "zawęź" in snap["too_big"]["message"].lower()
    # gmina: bez osobnego szacunku (nie pyta o hits), limit pilnuje pobierania; wyniki do momentu przerwania zostają, z komunikatem
    from src.data import egib
    monkeypatch.setattr(egib, "hits", lambda i, j: (_ for _ in ()).throw(AssertionError("gmina nie powinna pytać o hits")))
    monkeypatch.setattr(ps, "MAX_EGIB", 2)
    monkeypatch.setattr(ps, "BATCH", 2)
    from src.data import egib as eg
    T = eg.TILE_M
    by_tile = {}
    for k, x in enumerate(range(380000, 384000, 600)):         # po jednej działce 1 ha w kolejnych miejscach (różne kafle)
        g = box(x, 486000, x + 100, 486100)
        r = {"id": f"302505_5.0014.{k}", "nr": str(k), "obreb": "Ś", "gmina": "Z", "pole_ha": 1.0, "wkb": shapely.to_wkb(g, hex=True)}
        by_tile.setdefault((int(x // T), int(486000 // T)), []).append(r)
    monkeypatch.setattr(eg, "admin_boundary", lambda level, code: box(379000, 485000, 384000, 490000))
    monkeypatch.setattr(eg, "fetch_tile", lambda i, j: {"rows": by_tile.get((i, j), []), "fetched_at": "2026-11-19T07:00:00Z"})
    monkeypatch.setattr(eg, "CONCURRENCY", 1)
    job = ps.Job([], {"area": {"required": True, "min_ha": 0.5, "max_ha": None}}, {"level": "gmina", "code": "302505_3", "name": "Z"})
    ps.run(job)
    snap = job.snapshot()
    assert snap["too_big"]["partial"] and not snap["too_big"].get("early") and "niepełne" in snap["too_big"]["message"]
    assert 0 < len(snap["results"]) <= 3


def test_api_egib_validation(api_client):
    crit = {"area": {"min_ha": 1}}
    assert api_client.post("/api/parcel-search", json={"source": "egib", "criteria": crit}).status_code == 422          # brak obszaru
    r = api_client.post("/api/parcel-search", json={"source": "egib", "area": {"level": "powiat", "code": "3025"}, "criteria": crit})
    assert r.status_code == 422 and "za duży" in str(r.json())                                                       # powiat bez zawężenia
    assert api_client.post("/api/parcel-search", json={"source": "egib", "area": {"level": "gmina", "code": "30250"}, "criteria": crit}).status_code == 422


def test_units_endpoint_reads_file_without_network(api_client, http, tmp_path, monkeypatch):
    from src.data import egib
    doc = {"source": "GUGiK – Państwowy Rejestr Granic (PRG), jednostki administracyjne", "source_url": "https://prg", "fetched_at": "2026-11-19T18:14:18Z",
           "woj": [{"code": "30", "name": "wielkopolskie"}, {"code": "02", "name": "dolnośląskie"}],
           "powiat": [{"code": "3025", "woj": "30", "name": "powiat średzki"}, {"code": "0218", "woj": "02", "name": "powiat średzki"}],
           "gmina": [{"code": "302505_3", "powiat": "3025", "name": "Zaniemyśl", "kind": "gm. miejsko-wiejska"},
                     {"code": "021801_2", "powiat": "0218", "name": "Kostomłoty", "kind": "gm. wiejska"}]}
    f = tmp_path / "teryt_units.json"
    f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(egib, "UNITS_FILE", str(f))
    monkeypatch.setattr(egib, "_units_file", None)
    r = api_client.get("/api/parcel-search/units")                    # trasa /units nie może trafić do /{job_id}
    assert r.status_code == 200 and [w["code"] for w in r.json()["woj"]] == ["02", "30"] and "powiat" not in r.json()
    d = api_client.get("/api/parcel-search/units", params={"woj": "30", "powiat": "3025"}).json()
    assert d["powiat"] == [{"code": "3025", "name": "powiat średzki"}]                 # tylko powiaty tego województwa
    assert d["gmina"] == [{"code": "302505_3", "name": "Zaniemyśl", "kind": "gm. miejsko-wiejska"}]
    assert "stan na 2026-11-19" in d["provenance"]["note"] and not http.calls        # bez zapytań do sieci
    assert api_client.get("/api/parcel-search/units", params={"woj": "3"}).status_code == 422


def test_step_info_overall_progress_monotonic():
    job = ps.Job([], {"area": {"required": True}}, {"level": "gmina", "code": "302505_3"})
    job.progress("Obszar", "x", 1, 2)
    a = job.step_info()
    job.progress("EGiB", "x", 10, 20)
    job.progress("BDOT10k", "x", 1, 4)          # etapy partii nie przestawiają kroku – liczy się pobieranie kafli
    b = job.step_info()
    job.progress("EGiB", "x", 20, 20)
    job.finishing = True
    c = job.step_info()
    assert (a["n"], a["of"], a["label"]) == (1, 3, "Wyznaczam obszar") and b["label"] == "Pobieram i sprawdzam działki"
    assert a["pct"] < b["pct"] < c["pct"] < 100 and c["label"] == "Kończę"
    mine = ps.Job([{"lat": 52.2, "lon": 17.2}] * 4, {"area": {"required": True}})
    mine.total_expected, mine.final_count = 4, 2
    assert mine.step_info()["label"] == "Sprawdzam działki" and mine.step_info()["pct"] == 25
    job.state = "done"
    assert job.step_info()["pct"] == 100 and job.step_info()["label"] == "Gotowe"


def test_results_index_one_call_limits_and_view(monkeypatch):
    from src.data import result_index, sentinel
    calls = []

    def fake_current(p):
        calls.append(p.parcel_id)
        return {"ndvi": {"mean": 0.61, "acquired_on": "2026-11-12"}, "layer": {"image": "data:image/png;base64,AA==", "bounds": [[0, 0], [1, 1]]},
                "layer_ndmi": None, "layer_ndwi": None, "provenance": {"kind": "pomiar", "source": "Sentinel-2"}, "warnings": []}

    monkeypatch.setattr(sentinel, "analyze_current", fake_current)
    near = [_parcel(f"302505_5.0001.{k}", k * 300, 100) for k in range(3)]
    r = result_index.results_index(near)
    assert len(calls) == 1 and r["count"] == 3 and r["acquired_on"] == "2026-11-12" and r["layers"]["ndvi"]   # jedno zapytanie dla wszystkich
    far = near + [_parcel("302505_5.0001.9", 30_000, 100)]                                                     # ok. 30 km dalej
    big = result_index.results_index(far)
    assert big["too_large"] and big["layers"] is None and len(calls) == 1 and big["provenance"]["kind"] == "brak"
    w, s, e, n = near[0].polygon_wgs84.bounds
    part = result_index.results_index(far, view=(w - 0.01, s - 0.01, e + 0.02, n + 0.01))                    # widoczny fragment mapy
    assert not part["too_large"] and part["count"] >= 1 and len(calls) == 2


def test_index_endpoint_running_job_is_409(api_client, monkeypatch):
    job = ps.Job([{"lat": 52.2, "lon": 17.2}], {"area": {"required": True}})
    monkeypatch.setattr(ps, "get_job", lambda jid: job)
    assert api_client.get(f"/api/parcel-search/{job.id}/index").status_code == 409


def _fake_world(monkeypatch):
    """Sztuczne BDOT10k (rzeka i rów), NMT i powiaty – bez sieci, deterministycznie."""
    from src.data import bdot10k
    river = LineString([(379000, 486500), (384000, 486500)])
    ditch = LineString([(381000, 485000), (381000, 490000)])
    layers = {c: _layer(c, []) for c in bdot10k.CLASSES}
    layers["SWRS_L"] = _layer("SWRS_L", [river], [{"nazwa": "Rzeka"}])
    layers["SWRM_L"] = _layer("SWRM_L", [ditch])
    monkeypatch.setattr(bdot10k, "ensure_layers", lambda t, classes, progress=None: {c: layers[c] for c in classes})
    monkeypatch.setattr(ps, "_counties_for", lambda p, r: ["9999"])
    monkeypatch.setattr(terrain, "parcel_slope", lambda p: {"value": {"mean_pct": round(p.area_ha, 2), "p90_pct": round(2 * p.area_ha, 2)},
                                                             "provenance": {"kind": "pomiar", "source": "NMT", "note": ""}})


def _strip(snap):
    return {r["parcel_id"]: (r["status"], {k: (v["status"], v["text"]) for k, v in r["criteria"].items()}) for r in snap["results"]}


CFG_EQ = {"area": {"required": True, "min_ha": 0.5, "max_ha": None}, "shape": {"required": True, "preset": "any", "min_width_m": 20},
          "water": {"required": True, "op": "le", "dist_m": 400, "kinds": ["rivers", "ditches"]},
          "slope": {"required": True, "max_mean_pct": 3.0, "max_p90_pct": None}, "merge": {"required": False, "min_ha": 2}}


def test_streaming_equals_batch_for_my_parcels(monkeypatch):
    _fake_world(monkeypatch)
    sizes = [100, 60, 150, 200, 40, 120, 180, 90, 250]
    parcels = {f"302505_5.0001.{k}": _parcel(f"302505_5.0001.{k}", k * 400, s) for k, s in enumerate(sizes)}
    parcels["302505_5.0001.4"] = _parcel("302505_5.0001.4", 3 * 400 + 200, 40)       # przylega do .3 – grupa scalenia
    monkeypatch.setattr(ps, "_resolve", lambda it: parcels.get(it.get("parcel_id")))
    pool = [{"parcel_id": pid, "lat": 52.2, "lon": 17.2, "id_tag": pid, "farm": "F"} for pid in parcels]
    out = []
    for batch in (1000, 2):
        monkeypatch.setattr(ps, "BATCH", batch)
        job = ps.Job(list(pool), CFG_EQ)
        ps.run(job)
        job.state = "done"
        out.append(_strip(job.snapshot()))
    assert out[0] == out[1] and len(out[0]) == len(parcels)
    assert {st for st, _ in out[0].values()} >= {"spelnia", "nie"}


def test_streaming_equals_batch_for_egib(monkeypatch):
    _fake_world(monkeypatch)
    from src.data import egib as eg
    T = eg.TILE_M
    by_tile = {}
    for k, x in enumerate(range(379200, 384000, 450)):
        side = 80 + (k % 4) * 40
        g = box(x, 486100 + (k % 3) * 200, x + side, 486100 + (k % 3) * 200 + side)
        r = {"id": f"302505_5.0014.{k}", "nr": str(k), "obreb": "Ś", "gmina": "Z", "pole_ha": None, "wkb": shapely.to_wkb(g, hex=True)}
        by_tile.setdefault((int(x // T), int(g.bounds[1] // T)), []).append(r)
    monkeypatch.setattr(eg, "admin_boundary", lambda level, code: box(379000, 485000, 384000, 490000))
    monkeypatch.setattr(eg, "fetch_tile", lambda i, j: {"rows": by_tile.get((i, j), []), "fetched_at": "2026-11-19T07:00:00Z"})
    monkeypatch.setattr(ps, "narrowing_zones", lambda job, b: (b, []))
    out = []
    for batch in (1000, 2):
        monkeypatch.setattr(ps, "BATCH", batch)
        job = ps.Job([], CFG_EQ, {"level": "gmina", "code": "302505_3", "name": "Z"})
        ps.run(job)
        job.state = "done"
        out.append(_strip(job.snapshot()))
    assert out[0] == out[1] and len(out[0]) > 5


def test_since_returns_only_new_final_rows(monkeypatch):
    _fake_world(monkeypatch)
    parcels = {f"302505_5.0001.{k}": _parcel(f"302505_5.0001.{k}", k * 400, 120) for k in range(5)}
    monkeypatch.setattr(ps, "_resolve", lambda it: parcels.get(it.get("parcel_id")))
    monkeypatch.setattr(ps, "BATCH", 2)
    cfg = {"area": {"required": True, "min_ha": 0.5, "max_ha": None}}
    job = ps.Job([{"parcel_id": pid, "lat": 52.2, "lon": 17.2} for pid in parcels], cfg)
    ps.run(job)
    s1 = job.snapshot(since=0)
    assert s1["cursor"] == 5 and len(s1["results_new"]) == 5 and s1["results"] == [] and s1["counts"]["spelnia"] == 5
    s2 = job.snapshot(since=s1["cursor"], cand_since=s1["cand_cursor"])
    assert s2["results_new"] == [] and s2["candidates_new"] == [] and s2["counts"]["spelnia"] == 5
    # partia w trakcie (wiersz jeszcze nie „final”): ani w liczniku „spełnia”, ani w results_new – tylko jako kandydat
    with job.lock:
        job.rows[4]["final"] = False
    job.final_count = 4
    s3 = job.snapshot(since=4, cand_since=0)
    assert s3["counts"]["spelnia"] == 4 and s3["counts"]["w_toku"] == 1 and s3["results_new"] == []
    assert [c["key"] for c in s3["candidates_new"]] == [job.rows[4]["key"]]


def test_index_clusters_split_large_extent(monkeypatch):
    from src.data import result_index, sentinel
    calls = []
    monkeypatch.setattr(sentinel, "analyze_current", lambda p: calls.append(p.parcel_id) or {
        "ndvi": {"mean": 0.5, "acquired_on": "2026-11-12"}, "layer": {"image": "data:image/png;base64,AA==", "bounds": [[0, 0], [1, 1]]},
        "layer_ndmi": None, "layer_ndwi": None, "provenance": {"kind": "pomiar"}, "warnings": []})
    ps_ = [_parcel(f"302505_5.0001.{k}", x, 100) for k, x in enumerate((0, 2000, 25_000, 26_000, 60_000))]   # ok. 60 km rozrzutu
    cl = result_index.clusters(ps_)
    assert len(cl) == 3 and sorted(c["count"] for c in cl) == [1, 2, 2]
    for c in cl:                                            # każde skupisko ≤ 10 km: indeks bez „przybliż mapę”
        r = result_index.cluster_index(ps_, c["id"])
        assert not r["too_large"] and r["layers"]["ndvi"] and r["count"] == c["count"]
    assert len(calls) == 3                                   # jedno zapytanie na skupisko
    assert result_index.cluster_index(ps_, "999_999")["count"] == 0

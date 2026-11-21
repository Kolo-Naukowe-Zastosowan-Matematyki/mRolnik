"""Linie referencyjne: normy IMGW-PIB, progi wodowskazów (hydro-back), GUS BDL, średnia wieloletnia NDVI (atrapy HTTP)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.data import gus, reference, sentinel
from src.data.cadastre import parse_uldk_response
from src.data.common import PROVENANCE_KINDS
from src.web import routes_analysis
from tests.test_data_support import FakeResp, cdse_settings, clean_state, http, uldk_text  # noqa: F401


def test_normals_nearest_station_and_daily_values():
    n = reference.nearest_normals(52.2020, 17.2012)
    assert n["station"].startswith("Poznań") and n["distance_km"] < 60
    d = reference.daily_normals(["2026-11-15"], n)[0]
    assert d["t_max_normal_c"] == n["tmax_c"][10]
    assert d["precip_normal_mm"] == round(n["precip_mm"][10] / 30, 2)
    blk = reference.climate_block(52.2020, 17.2012, ["2026-11-15", "2026-11-16"])
    assert blk["provenance"]["kind"] == "statystyka" and "1991–2020" in blk["provenance"]["source"]
    cum = reference.cumulative_normal_precip([{"date": "2026-11-15"}, {"date": "2026-11-16"}], blk["daily"])
    assert cum[-1]["cum_precip_normal_mm"] == pytest.approx(2 * d["precip_normal_mm"], abs=0.1)


def test_normals_missing_file_gives_brak(monkeypatch):
    monkeypatch.setattr(reference, "load_normals", lambda: {"stations": []})
    blk = reference.climate_block(52.2, 17.2, ["2026-11-15"])
    assert blk["monthly"] is None and blk["provenance"]["kind"] == "brak" and blk["daily"][0]["t_max_normal_c"] is None


def _hydro_back_payload():
    pts = [{"state": "low", "date": f"2026-11-15T{h:02d}:00:00Z", "value": 170 + h} for h in range(10)]
    return {"operational": pts, "warningValues": [{"key": "2005", "value": 400.0}], "alarmValues": [{"key": "2012", "value": 460.0}],
            "lowValues": [{"key": "2025", "value": 225.0}], "highValues": [{"key": "2025", "value": 328.0}]}


def test_gauge_reference_from_hydro_back(http):
    http.add("hydro-back.imgw.pl/station/hydro/status/data", lambda u, kw: FakeResp(200, _hydro_back_payload()))
    ref = reference.gauge_reference({"id": "152170010", "name": "Śrem", "warning_level_cm": None, "alarm_level_cm": None})
    assert (ref["warning_level_cm"], ref["alarm_level_cm"], ref["low_level_cm"], ref["high_level_cm"]) == (400, 460, 225, 328)
    assert ref["series_7d"][-1]["level_cm"] == 179 and len(ref["series_7d"]) == 4   # co 3 h + ostatni odczyt
    assert ref["provenance"]["kind"] == "pomiar" and ref["state_label"] == "strefa stanów niskich"
    reference.gauge_reference({"id": "152170010", "name": "Śrem"})
    assert http.count("hydro-back") == 1   # cache


def test_gauge_reference_failure_keeps_api_thresholds(http):
    ref = reference.gauge_reference({"id": "152170010", "name": "Śrem", "warning_level_cm": 400.0, "alarm_level_cm": 460.0})
    assert ref["warning_level_cm"] == 400.0 and ref["series_7d"] == [] and ref["provenance"]["kind"] == "pomiar"


def test_soil_reference_is_tabular():
    p = reference.soil_reference_provenance("glina (gleba średnia)", 25.0, 12.0)
    assert p["kind"] == "tabelaryczna" and "nie jest pomiar" in p["note"] and "tabelaryczna" in PROVENANCE_KINDS


@pytest.mark.parametrize("crop,var", [("Pszenica ozima", 4332), ("Jęczmień jary", 4344), ("Pszenżyto ozime", 4356),
                                      ("Kukurydza", 47752), ("Buraki cukrowe", 4374), ("Rzepak ozimy", 4380),
                                      ("Trwały użytek zielony (łąka)", 4386), ("Żyto", 4338), ("Warzywa gruntowe", None)])
def test_gus_crop_mapping(crop, var):
    assert gus.map_crop(crop)[0] == var


def test_gus_voivodeship_resolution():
    assert gus.resolve_voivodeship("Wielkopolskie") == ("wielkopolskie", "30")
    assert gus.resolve_voivodeship("slaskie") == ("śląskie", "24")
    assert gus.resolve_voivodeship("30") == ("wielkopolskie", "30")
    assert gus.resolve_voivodeship("Atlantyda") is None


def _bdl(u, kw):
    if kw["params"]["unit-level"] == 2:
        return FakeResp(200, {"lastUpdate": "2026-06-30T00:00:00", "results": [
            {"id": "023000000000", "name": "WIELKOPOLSKIE", "values": [{"year": str(y), "val": 50 + y - 2014} for y in range(2014, 2026)]}]})
    return FakeResp(200, {"results": [{"id": "000000000000", "name": "POLSKA", "values": [{"year": str(y), "val": 45.0} for y in range(2014, 2026)]}]})


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes_analysis.router)
    return TestClient(app)


def test_yields_endpoint(http, client):
    http.add("bdl.stat.gov.pl/api/v1/data/by-variable/4332", _bdl)
    d = client.get("/api/reference/yields", params={"crop": "Pszenica ozima", "voivodeship": "wielkopolskie"}).json()
    assert d["available"] and d["gus_crop"] == "pszenica" and len(d["years"]) == 10
    assert d["years"][-1] == {"year": 2025, "voivodeship": 61.0, "poland": 45.0}
    assert d["provenance"]["kind"] == "statystyka" and "ozima + jara" in d["note"]
    client.get("/api/reference/yields", params={"crop": "Pszenica jara", "voivodeship": "30"})
    assert http.count("bdl.stat.gov.pl") == 2   # cache 30 dni
    assert client.get("/api/reference/yields", params={"crop": "Pszenica", "voivodeship": "Atlantyda"}).status_code == 422
    u = client.get("/api/reference/yields", params={"crop": "Warzywa gruntowe", "voivodeship": "30"}).json()
    assert u["available"] is False and "nie publikuje" in u["note"]


def test_yields_endpoint_gus_down(http, client):
    d = client.get("/api/reference/yields", params={"crop": "Żyto", "voivodeship": "30"}).json()
    assert d["available"] is False and d["provenance"]["kind"] == "brak"


def test_ndvi_climatology(http, monkeypatch):
    cdse_settings(monkeypatch, configured=True)
    http.add("openid-connect/token", lambda u, kw: FakeResp(200, {"access_token": "T", "expires_in": 600}), "POST")
    items = []
    for y in range(2019, 2026):
        for m in range(1, 13):
            items.append({"interval": {"from": f"{y}-{m:02d}-01T00:00:00Z"}, "outputs": {"ndvi": {"bands": {"B0": {"stats": {
                "mean": 0.3 + m / 100 + (y - 2019) / 1000, "min": 0.1, "max": 0.9, "stDev": 0.05, "sampleCount": 100, "noDataCount": 0}}}}}})
    http.add("/api/v1/statistics", lambda u, kw: FakeResp(200, {"data": items}), "POST")
    p = parse_uldk_response(uldk_text(52.2020, 17.2012))
    res = sentinel.ndvi_climatology(p)
    assert res["period"] == "2019–2025" and res["months"][5]["n_years"] == 7
    assert res["months"][5]["mean"] == pytest.approx(0.363, abs=0.001) and res["provenance"]["kind"] == "statystyka"
    sentinel.ndvi_climatology(p)
    assert http.count("/statistics") == 1


def test_ndvi_climatology_not_configured(monkeypatch):
    cdse_settings(monkeypatch, configured=False)
    res = sentinel.ndvi_climatology(parse_uldk_response(uldk_text(52.2020, 17.2012)))
    assert res["months"] == [] and res["provenance"]["kind"] == "brak"


def test_build_script_parser():
    from scripts.build_imgw_normals import parse_s_m_d
    from collections import defaultdict
    acc, names = defaultdict(lambda: defaultdict(dict)), {}
    row = ['"352160330"', '"POZNAŃ"', '"2000"', '"07"', "30", '""', "24.1", '""', "5", '""', "13.2", '""', "18", '""', "4", '""', "0", '"9"']
    bad = list(row); bad[2] = '"2001"'; bad[6] = ""; bad[7] = '"8"'
    parse_s_m_d(",".join(row) + "\n" + ",".join(bad), acc, names)
    code = "352160330"
    assert acc[code]["tmax_c"][7] == {2000: 24.1} and acc[code]["precip_mm"][7][2000] == 0.0


@pytest.mark.parametrize("crop,var", [("Pszenica ozima", 4859), ("Pszenżyto jare", 4863), ("Jęczmień jary", 4861),
                                      ("Kukurydza", 284047), ("Kukurydza na kiszonkę", None), ("Rzepak ozimy", 4868),
                                      ("Buraki cukrowe", 4867), ("Ziemniaki", 4864), ("Soja", None)])
def test_price_crop_mapping(crop, var):
    assert gus.map_price(crop) == var


def _bdl_price(u, kw):
    if kw["params"]["unit-level"] == 2:
        return FakeResp(200, {"lastUpdate": "2026-05-29T00:00:00", "results": [
            {"id": "023000000000", "name": "WIELKOPOLSKIE", "values": [{"year": "2020", "val": 70.10}, {"year": "2022", "val": 120.0},
                                                                   {"year": "2023", "val": 90.0}, {"year": "2024", "val": 82.70}, {"year": "2025", "val": 82.46}]}]})
    return FakeResp(200, {"results": [{"id": "000000000000", "name": "POLSKA", "values": [{"year": "2024", "val": 82.62}, {"year": "2025", "val": 81.72}]}]})


def test_prices_endpoint(http, client):
    http.add("bdl.stat.gov.pl/api/v1/data/by-variable/4859", _bdl_price)
    d = client.get("/api/reference/prices", params={"crops": "Pszenica ozima|Pszenica jara|Soja", "voivodeship": "30"}).json()
    assert len(d["rows"]) == 2   # dwie pszenice → jedna cena
    w, s = d["rows"]
    assert w["available"] and w["crops"] == ["Pszenica ozima", "Pszenica jara"] and w["scope"] == "województwo"
    assert (w["year"], w["price"], w["prev_price"], w["change"], w["change_pct"]) == (2025, 82.46, 82.7, -0.24, -0.3)
    assert w["poland_price"] == 81.72 and w["unit"] == "zł/dt"
    assert [c["year"] for c in w["changes"]] == [2025, 2024, 2023, 2022, 2021]
    assert w["changes"][2]["change"] == -30.0 and w["changes"][2]["change_pct"] == -25.0
    assert w["changes"][3]["change"] is None and w["changes"][4]["change"] is None      # brak 2021 w GUS
    assert (w["base_year"], w["base_price"]) == (2020, 70.1)
    assert s["available"] is False and "nie publikuje" in s["note"]
    assert d["provenance"]["kind"] == "statystyka" and "4859" in d["provenance"]["note"]
    assert http.count("bdl.stat.gov.pl") == 2
    assert client.get("/api/reference/prices", params={"crops": "Pszenica", "voivodeship": "Atlantyda"}).status_code == 422


def test_prices_endpoint_gus_down(http, client):
    d = client.get("/api/reference/prices", params={"crops": "Rzepak", "voivodeship": "30"}).json()
    assert d["rows"][0]["available"] is False and d["provenance"]["kind"] == "brak"

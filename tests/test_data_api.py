"""Kontrakt 2: kształt odpowiedzi /api/analyze, walidacja, /api/sources/status (bez importu server.py)."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.data import sentinel, soil, weather
from src.web import routes_analysis
from tests.test_data_support import (  # noqa: F401
    FakeResp, cdse_settings, clean_state, http, ndvi_png, open_meteo_forecast, open_meteo_soil_icon, synop_records, uldk_text,
)

NOW = datetime(2026, 11, 15, 12, 50, tzinfo=timezone.utc)
PROV_KEYS = {"source", "source_url", "kind", "observed_at", "fetched_at", "cloud_cover_pct", "resolution", "note"}
PARCEL_KEYS = {"parcel_id", "id_tag", "crop", "soil", "area_ha", "commune", "county", "voivodeship", "centroid", "geojson_coords",
               "ndvi", "soil_moisture", "water_reserve_days", "health_score", "status_label", "risk", "advice_bullets", "layers",
               "history", "provenance", "warnings"}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes_analysis.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    for mod in (weather, soil, sentinel):
        monkeypatch.setattr(mod, "utcnow", lambda: NOW)


def _common_sources(http):
    http.add("uldk.gugik.gov.pl", lambda u, kw: FakeResp(200, text=uldk_text(52.2020, 17.2012)))
    http.add("api.open-meteo.com/v1/forecast", lambda u, kw: FakeResp(
        200, open_meteo_soil_icon() if "hourly" in kw["params"] else open_meteo_forecast()))
    http.add("api/data/synop", lambda u, kw: FakeResp(200, synop_records()))
    http.add("warningsmeteo", lambda u, kw: FakeResp(200, [
        {"nazwa_zdarzenia": "Silny wiatr", "stopien": "1", "obowiazuje_od": "2026-11-15 10:00:00",
         "obowiazuje_do": "2026-11-16 10:00:00", "teryt": ["3025"]}]))


def _check_prov(p):
    assert set(p) == PROV_KEYS
    assert p["kind"] in ("pomiar", "prognoza", "model", "reanaliza", "szacunek", "demo", "brak")


def test_analyze_contract_without_cdse(client, http, monkeypatch):
    cdse_settings(monkeypatch, configured=False)
    _common_sources(http)
    r = client.post("/api/analyze", json={"parcels": [{"lat": 52.2020, "lon": 17.2012, "crop": "Pszenica ozima",
                                                       "soil": "Średnia gliniasta", "id_tag": "Dz. 114/1"}],
                                          "weather_model": "auto"})
    assert r.status_code == 200
    d = r.json()
    assert {"generated_at", "weather", "parcels", "total_area_ha", "map_center", "map_bounds", "sources", "warnings"} <= set(d)
    w = d["weather"]
    assert {"current", "daily", "precip_7d_mm", "precip_14d_mm", "climatic_water_balance_14d_mm", "soil_temp_c", "warnings",
            "provenance"} <= set(w)
    assert w["current"]["station"] == "Poznań (IMGW-PIB)" and w["current"]["wind_ms"] == 5.0
    assert set(w["daily"][0]) == {"date", "rain_mm", "t_max_c", "t_min_c", "et0_mm"}
    assert w["soil_temp_c"] == 5.5
    assert w["warnings"][0]["event"] == "Silny wiatr"  # filtr po powiecie 3025 z TERYT działki
    for k in ("current", "forecast", "warnings"):
        _check_prov(w["provenance"][k])
    p = d["parcels"][0]
    assert PARCEL_KEYS <= set(p)
    assert p["parcel_id"] == "302505_5.0014.114/1" and p["area_ha"] > 0 and len(p["geojson_coords"]) == 5
    assert p["ndvi"] is None and p["health_score"] is None
    assert p["status_label"] == "Brak danych satelitarnych"
    assert p["provenance"]["ndvi"]["kind"] == "brak" and "CDSE_CLIENT_ID" in p["provenance"]["ndvi"]["note"]
    assert p["provenance"]["parcel"]["kind"] == "pomiar" and p["provenance"]["soil_moisture"]["kind"] == "model"
    for v in p["provenance"].values():
        _check_prov(v)
    assert set(p["soil_moisture"]) >= {"surface_pct", "root_zone_pct", "trend_7d"}
    assert isinstance(p["water_reserve_days"], int)
    assert p["layers"] == {"ndvi": None, "ndmi": None, "true_color": None}
    assert p["advice_bullets"] and all(isinstance(a, str) for a in p["advice_bullets"])
    assert "status_color" not in p and "overlay_data_uri" not in p and "mean_ndvi_now" not in p
    assert all({"name", "url", "kind", "licence"} <= set(s) for s in d["sources"])
    assert any("Instytut Meteorologii i Gospodarki Wodnej" in s["licence"] for s in d["sources"])


def test_analyze_with_cdse(client, http, monkeypatch):
    cdse_settings(monkeypatch, configured=True)
    _common_sources(http)
    http.add("openid-connect/token", lambda u, kw: FakeResp(200, {"access_token": "T", "expires_in": 600}), "POST")
    http.add("catalog/1.0.0/search", lambda u, kw: FakeResp(200, {"features": [
        {"properties": {"datetime": "2026-11-12T10:03:00Z", "eo:cloud_cover": 8.0}}]}), "POST")
    stats = {"data": [{"interval": {"from": "2026-11-12T00:00:00Z"}, "outputs": {"ndvi": {"bands": {"B0": {"stats": {
        "min": 0.3, "max": 0.85, "mean": 0.72, "stDev": 0.06, "sampleCount": 400, "noDataCount": 10}}}}}}]}
    http.add("api/v1/statistics", lambda u, kw: FakeResp(200, stats), "POST")
    http.add("api/v1/process", lambda u, kw: FakeResp(200, text="", content=ndvi_png(np.full((10, 10), 0.72))), "POST")
    d = client.post("/api/analyze", json={"parcels": [{"lat": 52.2020, "lon": 17.2012}]}).json()
    p = d["parcels"][0]
    assert p["ndvi"]["mean"] == 0.72 and p["ndvi"]["healthy_pct"] == 100.0
    assert p["health_score"] is not None and p["risk"] in ("Niskie", "Średnie", "Podwyższone", "Wysokie")
    assert p["layers"]["ndvi"]["image"].startswith("data:image/png;base64,")
    assert p["provenance"]["ndvi"]["observed_at"] == "2026-11-12T10:03:00Z"
    # etap 2: historia 24 mies. i zdjęcie nie blokują odpowiedzi – status „pending”, dane z osobnych endpointów
    assert p["history"] is None and p["history_status"] == "pending" and p["layers_pending"] == ["true_color"]
    hist = client.get("/api/history/ndvi", params={"parcel_id": p["parcel_id"]}).json()["history"]
    assert hist["ndvi_monthly"][0] == {"month": "2026-11", "mean": 0.72, "valid_pct": 97.5}
    tc = client.get("/api/layers/true-color", params={"parcel_id": p["parcel_id"]}).json()
    assert tc["layer"]["image"].startswith("data:image/png;base64,") and tc["layer"]["acquired_on"] == "2026-11-12"
    # drugi raz historia jest już w cache → wraca w samej analizie
    d2 = client.post("/api/analyze", json={"parcels": [{"lat": 52.2020, "lon": 17.2012}]}).json()
    assert d2["parcels"][0]["history_status"] == "ready"


def test_uldk_down_no_synthetic_parcel(client, http, monkeypatch):
    cdse_settings(monkeypatch, configured=False)
    r = client.post("/api/analyze", json={"parcels": [{"lat": 52.0, "lon": 19.0}]})
    assert r.status_code == 200
    p = r.json()["parcels"][0]
    assert p["geojson_coords"] == [] and p["area_ha"] is None
    assert p["provenance"]["parcel"]["kind"] == "brak" and p["warnings"]
    assert r.json()["weather"]["provenance"]["forecast"]["kind"] == "brak"


@pytest.mark.parametrize("body", [
    {"parcels": [{"lat": 60.0, "lon": 17.0}]},
    {"parcels": [{"lat": 52.0, "lon": 30.0}]},
    {"parcels": []},
    {"parcels": [{"lat": 52.0, "lon": 17.0}] * 51},
    {"parcels": [{"lat": 52.0, "lon": 17.0, "drought_factor": 1.0}]},
    {"parcels": [{"lat": 52.0, "lon": 17.0}], "weather_model": "imgw"},
    {"parcels": [{"lat": 52.0, "lon": 17.0, "crop": "x" * 121}]},
    {"parcels": [{"lat": 52.0, "lon": 17.0, "parcel_id": "1&request=x"}]},
    {"parcels": [{"lat": 52.0}]},
])
def test_analyze_validation(client, body):
    assert client.post("/api/analyze", json=body).status_code == 422


def test_sources_status(client, monkeypatch):
    from src.core.config import settings as real
    d = client.get("/api/sources/status").json()
    names = [s["name"] for s in d]
    assert any("Sentinel" in n for n in names) and any("ULDK" in n for n in names)
    assert all({"name", "ok", "configured", "last_success", "message"} <= set(s) for s in d)
    assert d[0]["configured"] == real.cdse_configured


def test_hydromelior_endpoints_validate(client):
    assert client.get("/api/hydromelior/network").status_code == 422
    assert client.get("/api/hydromelior/alerts?lat=10&lon=17").status_code == 422
    assert client.post("/api/hydromelior/toggle-gate", json={"gate_id": "Z-1", "status": "closed"}).status_code == 422
    assert client.post("/api/hydromelior/toggle-gate", json={"gate_id": "Z-1", "status": "otwarta", "retaining_cm": 200}).status_code == 422
    r = client.post("/api/hydromelior/toggle-gate", json={"gate_id": "Z-2", "status": "otwarta"})
    assert r.status_code == 200 and r.json()["demo"] is True


def test_advice_when_readily_available_water_exhausted():
    from src.api_service import advice_for
    out = {"soil_moisture": {"surface_pct": 17.2, "field_capacity_pct": 25.0, "available_water_pct": 41}, "water_reserve_days": 0}
    tips = advice_for(out, {"current": {}, "daily": []}, {})
    assert any("Łatwo dostępna woda w glebie wyczerpana (zostało ok. 41% wody użytecznej)" in t for t in tips)


def test_soil_block_exposes_available_water_pct():
    sm, days, _, prov_water, _, _ = soil.soil_block_from(
        {"surface": 0.172, "root": 0.174, "trend_7d": "stabilna", "kind": "model", "source": "x", "note_src": "x"},
        None, "Średnia gliniasta (klasa IIIb)", [])
    assert days == 0 and sm["available_water_pct"] == 41
    assert "Dni do stresu wodnego" in prov_water["note"] and "41% wody użytecznej" in prov_water["note"]


def test_sources_status_check_probes_all_sources(client, http, monkeypatch):
    from src.data.common import ALL_SOURCES
    monkeypatch.delenv("MROLNIK_HYDRO_WFS_URLS", raising=False)
    _common_sources(http)
    http.add("api/data/hydro", lambda u, kw: FakeResp(200, [{"stacja": "Śrem", "rzeka": "Warta", "lat": "52.09", "lon": "17.02",
                                                             "stan_wody": "178"}]))
    http.add("kompozycja_BDOT10k_WMS", lambda u, kw: FakeResp(200, text=(
        "<WMS_Capabilities><Capability><Layer><Layer><Name>row</Name><Title>Rowy</Title></Layer></Layer>"
        "</Capability></WMS_Capabilities>")))
    d = client.get("/api/sources/status", params={"check": "true"}).json()
    by = {s["name"]: s for s in d}
    for name in ALL_SOURCES:
        if "Sentinel" in name:
            continue
        assert by[name]["checked"], name
    assert by[[n for n in ALL_SOURCES if "hydrologiczne" in n][0]]["message"] == "1 posterunków"

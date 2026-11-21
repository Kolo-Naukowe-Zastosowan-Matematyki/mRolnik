"""Testy wspólnego archiwum ERA5 / ERA5-Land + zapasowego NASA POWER (src/data/era5_archive.py).

Sprawdzają:
- porcjowanie roczne w cache (różne zakresy 1/3/5/10 lat i sąsiednie działki współdzielą pobranie),
- dociąganie tylko brakującego ogona dni bez ponownego pobierania zamkniętych lat,
- brak przecieku danych z przyszłości dla dowolnej historycznej `analysis_date` (w tym sprzed 2017 r.),
- obsługę HTTP 429 (czytelny komunikat, bezpiecznik limitu, natychmiastowy fallback na NASA POWER),
- jawny komunikat „brak danych” z powodem (bez maskowania jako „nieprawidłowa odpowiedź serwisu”),
- obsługę opcjonalnego OPEN_METEO_API_KEY (customer-archive-api + redakcja klucza z logów/błędów).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.data import climate, common, era5_archive, history, soil
from src.web import routes_analysis
from tests.test_data_support import FakeResp, clean_state, http  # noqa: F401

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    for mod in (history, climate, soil, routes_analysis):
        monkeypatch.setattr(mod, "utcnow", lambda: NOW)


def _om_water_payload(start_s: str, end_s: str, precip: float = 2.0, sm: float = 0.28):
    a, b = date.fromisoformat(start_s), date.fromisoformat(end_s)
    days = [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]
    return {
        "latitude": 52.2,
        "longitude": 21.0,
        "daily": {"time": days, "precipitation_sum": [precip] * len(days)},
        "hourly": {"time": [d + "T12:00" for d in days], "soil_moisture_7_to_28cm": [sm] * len(days)},
    }


def _power_water_payload(start_s: str, end_s: str, precip: float = 1.5, wet: float = 0.42):
    a = date(int(start_s[:4]), int(start_s[4:6]), int(start_s[6:8]))
    b = date(int(end_s[:4]), int(end_s[4:6]), int(end_s[6:8]))
    days = [(a + timedelta(days=i)).strftime("%Y%m%d") for i in range((b - a).days + 1)]
    return {
        "geometry": {"coordinates": [21.0, 52.2, 110.0]},
        "header": {"fill_value": -999.0},
        "properties": {"parameter": {
            "PRECTOTCORR": {d: precip for d in days},
            "GWETROOT": {d: wet for d in days},
        }},
    }


def _power_climate_payload(start_s: str, end_s: str):
    a = date(int(start_s[:4]), int(start_s[4:6]), int(start_s[6:8]))
    b = date(int(end_s[:4]), int(end_s[4:6]), int(end_s[6:8]))
    days = [(a + timedelta(days=i)) for i in range((b - a).days + 1)]
    keys = [d.strftime("%Y%m%d") for d in days]
    return {
        "geometry": {"coordinates": [17.2, 52.2, 95.0]},
        "header": {"fill_value": -999.0},
        "properties": {"parameter": {
            "T2M_MAX": {k: 16.0 if 4 <= d.month <= 9 else 4.0 for k, d in zip(keys, days)},
            "T2M_MIN": {k: 6.0 if 4 <= d.month <= 9 else -3.0 for k, d in zip(keys, days)},
            "T2M": {k: 11.0 if 4 <= d.month <= 9 else 0.5 for k, d in zip(keys, days)},
            "PRECTOTCORR": {k: 1.8 for k in keys},
        }},
    }


def test_yearly_chunk_cache_reused_across_ranges_and_tail_only_fetch(http):
    seen = []

    def om(u, kw):
        p = kw["params"]
        seen.append((p["start_date"], p["end_date"]))
        return FakeResp(200, _om_water_payload(p["start_date"], p["end_date"]))

    http.add("archive-api.open-meteo.com", om)

    # 1) Pobranie 10 lat dla działki → 1 zapytanie sieciowe
    r10 = history.monthly_climate(52.201, 21.004, months=120)
    assert r10["source"] == "era5"
    assert len(r10["months"]) == 120
    assert r10["months"][0]["month"] == "2016-12"   # nie ucięte przez ARCHIVE_START Sentinel-2 (2017-01)!
    assert r10["months"][-1]["month"] == "2026-11"
    assert r10["months"][0]["soil_moisture_pct"] == 28.0
    assert http.count("archive-api") == 1

    # 2) Węższy zakres (3 lata) i sąsiednia działka w tej samej komórce 0,1° → 0 zapytań sieciowych
    r3 = history.monthly_climate(52.22, 21.03, months=36)
    assert len(r3["months"]) == 36
    assert http.count("archive-api") == 1

    # 3) Zapytanie o nowszy ogon (np. 3 dni później) → dociąga TYLKO brakujące 3 dni
    ext = era5_archive.daily_series(52.201, 21.004, date(2024, 1, 1), date(2026, 11, 18), era5_archive.WATER)
    assert ext["provider"] == "era5"
    assert http.count("archive-api") == 2
    assert seen[-1] == ("2026-11-16", "2026-11-18")


def test_historical_analysis_date_has_no_future_leakage(http):
    def om(u, kw):
        p = kw["params"]
        return FakeResp(200, _om_water_payload(p["start_date"], p["end_date"]))

    http.add("archive-api.open-meteo.com", om)

    # Data analizy sprzed 6 lat → 10-letnie okno sięga 2010 r. i kończy się DOKŁADNIE na 2020-06-15
    ad = date(2020, 6, 15)
    r = history.monthly_climate(52.23, 21.01, months=120, analysis_date=ad)
    assert r["source"] == "era5"
    assert r["data_end"] == "2020-06-15"
    assert r["months"][0]["month"] == "2010-07"
    assert r["months"][-1]["month"] == "2020-06"
    assert r["months"][-1]["complete"] is False
    # 15 dni × 2 mm = 30.0 mm – żaden dzień po 2020-06-15 nie przeciekł
    assert r["months"][-1]["precip_mm"] == 30.0
    assert all(m["month"] <= "2020-06" for m in r["months"])


def test_open_meteo_429_trips_circuit_breaker_and_falls_back_to_nasa_power(http):
    http.add(
        "archive-api.open-meteo.com",
        lambda u, kw: FakeResp(
            429,
            {"error": True, "reason": "Daily API request limit exceeded. Please try again tomorrow."},
            headers={"Retry-After": "22000"},
        ),
    )
    http.add("power.larc.nasa.gov", lambda u, kw: FakeResp(200, _power_water_payload(kw["params"]["start"], kw["params"]["end"])))

    r = history.monthly_climate(52.23, 21.01, months=36)
    assert r["source"] == "nasa_power"
    assert len(r["months"]) == 36
    assert r["months"][0]["soil_moisture_pct"] is None
    assert r["months"][0]["soil_wetness_pct"] == 42.0
    assert r["months"][0]["precip_mm"] > 0
    assert r["provenance"]["precip"]["kind"] == "reanaliza"
    assert "NASA POWER" in r["provenance"]["precip"]["source"]
    assert "HTTP 429" in (r.get("fallback_reason") or "")
    assert http.count("archive-api") == 1
    assert http.count("power.larc.nasa.gov") == 1
    assert era5_archive.om_quota_block() is not None

    # Kolejna działka w innej części Polski nie marnuje już zapytania do Open-Meteo Archive (działa bezpiecznik)
    r2 = history.monthly_climate(50.06, 19.94, months=12)
    assert r2["source"] == "nasa_power"
    assert len(r2["months"]) == 12
    assert http.count("archive-api") == 1
    assert http.count("power.larc.nasa.gov") == 2


def test_both_sources_down_reports_explicit_429_reason_not_generic(http):
    http.add(
        "archive-api.open-meteo.com",
        lambda u, kw: FakeResp(429, {"error": True, "reason": "Daily API request limit exceeded."}, headers={"Retry-After": "18000"}),
    )
    http.add("power.larc.nasa.gov", lambda u, kw: FakeResp(503, {"message": "maintenance"}))

    r = history.monthly_climate(52.23, 21.01, months=12)
    assert r["months"] == []
    prov = r["provenance"]["precip"]
    assert prov["kind"] == "brak"
    assert "nieprawidłowa odpowiedź serwisu" not in prov["note"]
    assert "HTTP 429" in prov["note"]
    assert "HTTP 503" in prov["note"]


def test_climate_summary_falls_back_to_nasa_power_with_hargreaves_et0_and_analysis_date(http):
    http.add(
        "archive-api.open-meteo.com",
        lambda u, kw: FakeResp(429, {"error": True, "reason": "Daily limit"}, headers={"Retry-After": "10000"}),
    )
    http.add("power.larc.nasa.gov", lambda u, kw: FakeResp(200, _power_climate_payload(kw["params"]["start"], kw["params"]["end"])))

    res = climate.climate_summary(52.2, 17.2, analysis_date=date(2022, 8, 15))
    assert res["source"] == "nasa_power"
    assert res["data_end"] == "2022-08-15"
    assert res["years"][0]["year"] == 1991
    assert res["years"][-1]["year"] == 2021   # 2022 niepełny – wykluczony ze statystyk rocznych
    assert all(y["kbw_apr_sep_mm"] is not None for y in res["years"])
    assert res["provenance"]["kind"] == "reanaliza"
    assert "Hargreaves" in res["provenance"]["note"]


def test_open_meteo_commercial_key_uses_customer_host_and_redacts_errors(http, monkeypatch):
    ns = SimpleNamespace(open_meteo_api_key="SECRET_OM_KEY_123", offline_demo=False, contact_email="")
    monkeypatch.setattr(era5_archive, "settings", ns)
    http.add("customer-archive-api.open-meteo.com", lambda u, kw: FakeResp(200, _om_water_payload(kw["params"]["start_date"], kw["params"]["end_date"])))

    res = era5_archive.daily_series(52.2, 21.0, date(2025, 1, 1), date(2025, 1, 10), era5_archive.WATER)
    assert res["provider"] == "era5"
    assert http.count("customer-archive-api.open-meteo.com") == 1
    assert http.calls[0][2]["params"]["apikey"] == "SECRET_OM_KEY_123"
    assert "SECRET_OM_KEY_123" not in common.redact("https://customer-archive-api.open-meteo.com/v1/archive?apikey=SECRET_OM_KEY_123&x=1")


def test_history_climate_endpoint_validates_analysis_date(http):
    http.add("archive-api.open-meteo.com", lambda u, kw: FakeResp(200, _om_water_payload(kw["params"]["start_date"], kw["params"]["end_date"])))
    app = FastAPI()
    app.include_router(routes_analysis.router)
    client = TestClient(app)

    ok = client.get("/api/history/climate", params={"lat": 52.23, "lon": 21.01, "analysis_date": "2021-07-10"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["data_end"] == "2021-07-10"
    assert body["months"][-1]["month"] == "2021-07"

    future = client.get("/api/history/climate", params={"lat": 52.23, "lon": 21.01, "analysis_date": "2026-12-31"})
    assert future.status_code == 422


def test_soil_fallback_respects_era5_quota_block(http):
    # Włącz bezpiecznik HTTP 429
    era5_archive.note_om_error(common.SourceError("HTTP 429", status_code=429, retry_after=7200, reason="Daily limit"))
    http.add("api.open-meteo.com/v1/forecast", lambda u, kw: FakeResp(503))

    with pytest.raises(common.SourceError) as excinfo:
        soil.fetch_soil(52.2, 17.2)
    assert http.count("archive-api.open-meteo.com") == 0
    assert "HTTP 429" in str(excinfo.value)

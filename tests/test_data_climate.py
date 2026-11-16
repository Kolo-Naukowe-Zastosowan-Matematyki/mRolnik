"""Klimat pola (ERA5): wskaźniki roczne, trendy, bieżący sezon – atrapa archiwum Open-Meteo."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.data import climate
from src.web import routes_analysis
from tests.test_data_support import FakeResp, clean_state, http  # noqa: F401

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(climate, "utcnow", lambda: NOW)
    climate._climate_cache.clear()


def synthetic_archive(warming_per_year: float = 0.05):
    """Sinusoidalny rok (zima −2°C, lato 18°C) + ocieplenie; upał > 30°C przez kilka dni lipca od 2010 r."""
    start, end = date(1991, 1, 1), date(2026, 11, 9)
    times, tmax, tmin, tmean, p, et0 = [], [], [], [], [], []
    d = start
    while d <= end:
        doy = d.timetuple().tm_yday
        base = 8 + 10 * math.sin((doy - 105) / 365 * 2 * math.pi) + warming_per_year * (d.year - 1991)
        hot = 14 if (d.year >= 2010 and d.month == 7 and d.day <= 3) else 0
        times.append(d.isoformat())
        tmean.append(round(base, 2)); tmax.append(round(base + 5 + hot, 2)); tmin.append(round(base - 5, 2))
        p.append(1.5); et0.append(round(max(0.2, 1 + base / 6), 2))
        d += timedelta(days=1)
    return {"latitude": 52.2, "longitude": 17.2, "daily": {"time": times, "temperature_2m_max": tmax, "temperature_2m_min": tmin,
                                                          "temperature_2m_mean": tmean, "precipitation_sum": p, "et0_fao_evapotranspiration": et0}}


def test_climate_summary_trends_and_season(http):
    http.add("archive-api.open-meteo.com", lambda u, kw: FakeResp(200, synthetic_archive()))
    r = climate.climate_summary(52.2020, 17.2012)
    assert r["provenance"]["kind"] == "reanaliza" and r["normal_period"] == "1991–2020"
    years = r["years"]
    assert years[0]["year"] == 1991 and years[-1]["year"] == 2025          # 2026 niepełny – pomijamy w statystyce rocznej
    assert r["trends"]["t_mean_c"]["per_decade"] == pytest.approx(0.5, abs=0.05)
    assert r["trends"]["hot_days"]["per_decade"] > 0 and years[-1]["hot_days"] == 3 and years[0]["hot_days"] == 0
    y = years[0]
    assert y["season_start"] < y["season_end"] and 150 < y["season_days"] < 330
    assert y["last_spring_frost_doy"] < 180 < y["first_autumn_frost_doy"]
    assert r["normals"]["t_mean_c"] is not None and r["trends"]["season_days"]["per_decade"] > 0
    s = r["season"]
    assert s["year"] == 2026 and len(s["normal"]["gdd"]) == 183 and len(s["current"]["gdd"]) == 183
    assert s["normal"]["gdd"][-1] > 0 and s["current"]["gdd"][-1] > s["normal"]["gdd"][-1]   # ocieplenie → więcej ciepła niż średnio
    climate.climate_summary(52.2020, 17.2012)
    assert http.count("archive-api") == 1


def test_climate_failure_is_brak(http):
    r = climate.climate_summary(52.2, 17.2)
    assert r["years"] == [] and r["provenance"]["kind"] == "brak"


def test_linear_trend_and_season_helpers():
    t = climate.linear_trend(list(range(2000, 2020)), [10 + 0.1 * i for i in range(20)])
    assert t["per_decade"] == pytest.approx(1.0)
    assert climate.linear_trend([1, 2, 3], [1, 2, 3]) is None              # za mało lat


def test_climate_endpoint(http):
    http.add("archive-api.open-meteo.com", lambda u, kw: FakeResp(200, synthetic_archive(0.0)))
    app = FastAPI()
    app.include_router(routes_analysis.router)
    r = TestClient(app).get("/api/reference/climate", params={"lat": 52.2020, "lon": 17.2012}).json()
    assert len(r["years"]) == 35 and abs(r["trends"]["t_mean_c"]["per_decade"]) < 0.05

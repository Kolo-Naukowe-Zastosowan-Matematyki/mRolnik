"""Testy potoku historycznego (analysis_date), 10-letniej podatności na suszę, ISRIC SoilGrids i crop-aware FAO-56."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from src.data import climate, sentinel, soil
from src.data.cadastre import ParcelData
from src.data.common import SourceError, utcnow
from shapely.geometry import Polygon


def _make_era5_daily(start_year: int = 2012, end_year: int = 2024, spike_date: str | None = None) -> dict:
    """Syntetyczna seria dobowa ERA5: suche lato w latach parzystych, mokre w nieparzystych."""
    d = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    times, tmax, tmin, tmean, precip, et0 = [], [], [], [], [], []
    while d <= end:
        iso = d.isoformat()
        times.append(iso)
        summer = 4 <= d.month <= 9
        dry_year = (d.year % 2 == 0)
        if spike_date and iso == spike_date:
            p = 999.0
        elif summer and dry_year:
            p = 0.2
        else:
            p = 5.0
        e = 4.5 if summer else 0.8
        tx = 28.0 if (summer and dry_year) else (22.0 if summer else 5.0)
        tn = 14.0 if summer else 0.0
        tmax.append(tx)
        tmin.append(tn)
        tmean.append((tx + tn) / 2.0)
        precip.append(p)
        et0.append(e)
        d += timedelta(days=1)
    return {
        "time": times, "tmax": tmax, "tmin": tmin, "tmean": tmean,
        "precip": precip, "et0": et0, "end": end.isoformat(),
        "fetched_at": "2026-01-01T00:00:00Z", "cell": [52.2, 17.2],
    }


def test_weather_as_of_no_future_leakage(monkeypatch):
    as_of = date(2022, 7, 15)
    # 999 mm opadu dzień PO dacie analizy nie może wpłynąć na wynik!
    daily = _make_era5_daily(2012, 2024, spike_date="2022-07-16")
    monkeypatch.setattr(climate, "fetch_daily", lambda lat, lon: daily)

    w = climate.weather_as_of(52.2, 17.2, as_of)
    assert w["daily"] == []
    assert w["precip_60d_mm"] is not None and w["precip_60d_mm"] < 50.0
    assert w["kbw_60d_mm"] is not None and w["kbw_60d_mm"] < -200.0
    assert w["dry_spell_days"] == 60
    assert w["provenance"]["current"]["observed_at"] == "2022-07-15"
    assert all(r["date"] <= "2022-07-15" for r in w["recent_daily"])


def test_drought_climatology_10y_no_future_years(monkeypatch):
    as_of = date(2022, 7, 15)
    daily = _make_era5_daily(2012, 2024)
    monkeypatch.setattr(climate, "fetch_daily", lambda lat, lon: daily)

    dr = climate.drought_climatology_10y(52.2, 17.2, as_of)
    assert dr["available"] is True
    assert dr["period"] == "2012–2021"
    assert dr["years_analyzed"] == 10
    assert all(y <= 2021 for y in dr["drought_years"])
    # Lata parzyste 2012, 2014, 2016, 2018, 2020 są suche → 5/10 = 50%
    assert dr["drought_years_count"] == 5
    assert dr["drought_frequency_pct"] == 50
    assert dr["vulnerability_level"] == "wysoka"
    assert len(dr["worst_months"]) >= 1
    assert dr["provenance"]["observed_at"] == "2022-07-15"


def test_historical_soil_moisture_no_future_leakage(monkeypatch):
    soil._soil_cache.clear()
    as_of = date(2022, 7, 15)

    def fake_http(url, params=None, timeout=10.0, retries=1):
        assert "archive-api.open-meteo.com" in url
        assert params["end_date"] == "2022-07-15"
        # Nawet gdyby API zwróciło godziny po 2022-07-15, parse_soil_hourly musi je odciąć
        return {
            "hourly": {
                "time": [
                    "2022-07-08T12:00",
                    "2022-07-15T12:00",
                    "2022-07-16T12:00",
                ],
                "soil_moisture_0_to_7cm": [0.22, 0.14, 0.49],
                "soil_moisture_7_to_28cm": [0.24, 0.16, 0.49],
                "soil_temperature_0_to_7cm": [18.0, 21.5, 10.0],
            }
        }

    monkeypatch.setattr(soil, "http_get_json", fake_http)
    res = soil.fetch_soil(52.2, 17.2, as_of=as_of)
    assert res["surface"] == pytest.approx(0.14)
    assert res["root"] == pytest.approx(0.16)
    assert res["valid_at"] == "2022-07-15T12:00:00Z"
    assert res["trend_7d"] == "spada"


def test_soilgrids_parsing_and_water_reserve_auto_soil(monkeypatch):
    soil._soilgrids_cache.clear()

    def fake_sg(url, params=None, timeout=8.0, retries=1):
        return {
            "properties": {
                "layers": [
                    {"name": "sand", "depths": [
                        {"label": "0-5cm", "values": {"mean": 840}},
                        {"label": "5-15cm", "values": {"mean": 850}},
                        {"label": "15-30cm", "values": {"mean": 860}},
                    ]},
                    {"name": "silt", "depths": [
                        {"label": "0-5cm", "values": {"mean": 110}},
                        {"label": "5-15cm", "values": {"mean": 100}},
                        {"label": "15-30cm", "values": {"mean": 90}},
                    ]},
                    {"name": "clay", "depths": [
                        {"label": "0-5cm", "values": {"mean": 50}},
                        {"label": "5-15cm", "values": {"mean": 50}},
                        {"label": "15-30cm", "values": {"mean": 50}},
                    ]},
                    {"name": "soc", "depths": [
                        {"label": "0-5cm", "values": {"mean": 90}},
                        {"label": "5-15cm", "values": {"mean": 80}},
                        {"label": "15-30cm", "values": {"mean": 70}},
                    ]},
                ]
            }
        }

    monkeypatch.setattr(soil, "http_get_json", fake_sg)
    sg = soil.soilgrids_properties(52.2, 17.2)
    assert sg["available"] is True
    assert sg["texture_key"] == "piasek"
    assert sg["drought_category"] == "I"
    assert sg["sand_pct"] == pytest.approx(85.33, abs=0.1)
    assert sg["organic_matter_status"] == "niska"
    assert sg["provenance"]["kind"] == "model"

    wr = soil.water_reserve(0.10, 0.10, None, [], soilgrids=sg)
    assert wr["soil_recognized"] is True
    assert wr["soil_source"] == "soilgrids"
    assert wr["soil_key"] == "piasek"
    assert wr["drought_category"] == "I"


def test_soilgrids_timeout_graceful_fallback(monkeypatch):
    soil._soilgrids_cache.clear()
    monkeypatch.setattr(soil, "http_get_json", lambda *a, **kw: (_ for _ in ()).throw(SourceError("timeout")))
    sg = soil.soilgrids_properties(52.25, 17.25)
    assert sg["available"] is False
    assert sg["provenance"]["kind"] == "brak"

    s_mock = {"surface": 0.20, "root": 0.20, "soil_temp_c": 15.0, "valid_at": "2022-07-15T12:00:00Z",
              "fetched_at": "2026-01-01T00:00:00Z", "kind": "reanaliza", "source": "ERA5-Land", "note_src": "ERA5-Land."}
    sm, _, _, _, _, warns = soil.soil_block_from(s_mock, None, None, [], soilgrids=sg)
    assert sm["soil_source"] == "default"
    assert any("glina" in w.lower() for w in warns)


def test_crop_specific_kc_and_depletion_p():
    # Ziemniak ma p=0.35 (wrażliwy na suszę), pszenica ozima ma p=0.55; w lipcu ziemniak ma Kc=1.15 vs pszenica 0.65
    daily = [{"rain_mm": 0.0, "et0_mm": 4.0} for _ in range(10)]
    wr_potato = soil.water_reserve(0.22, 0.22, "glina", daily, crop="ziemniak", as_of=date(2022, 7, 15))
    wr_wheat = soil.water_reserve(0.22, 0.22, "glina", daily, crop="pszenica ozima", as_of=date(2022, 7, 15))
    assert wr_potato["depletion_p"] == 0.35
    assert wr_wheat["depletion_p"] == 0.55
    assert wr_potato["kc"] > wr_wheat["kc"]
    assert wr_potato["days"] < wr_wheat["days"]


def test_sentinel_window_and_cache_key_as_of():
    start, end = sentinel.current_window(as_of=date(2022, 7, 15))
    assert end.date() == date(2022, 7, 15)
    assert start.date() == date(2022, 7, 15) - timedelta(days=sentinel.LOOKBACK_DAYS)

    poly = Polygon([(17.20, 52.20), (17.21, 52.20), (17.21, 52.21), (17.20, 52.21)])
    p = ParcelData(parcel_id="302501_1.0001.1", polygon_wgs84=poly, polygon_2180=poly, area_ha=5.0,
                   centroid_lat=52.205, centroid_lon=17.205, voivodeship="wielkopolskie",
                   county="średzki", commune="Środa Wielkopolska", region="0001")
    k1 = sentinel._current_cache_key(p, as_of=date(2022, 7, 15))
    k2 = sentinel._current_cache_key(p, as_of=date(2021, 6, 10))
    assert k1 != k2
    assert k1.endswith(":2022-07-15")


def test_api_analyze_date_validation():
    from fastapi import FastAPI
    from src.web import routes_analysis

    app = FastAPI()
    app.include_router(routes_analysis.router)
    today = utcnow().date()
    client = TestClient(app)

    # Przyszła data → 422
    r_future = client.post("/api/analyze", json={
        "parcels": [{"lat": 52.2, "lon": 17.2}],
        "analysis_date": (today + timedelta(days=2)).isoformat(),
    })
    assert r_future.status_code == 422

    # Starsza niż 10 lat → 422
    r_old = client.post("/api/analyze", json={
        "parcels": [{"lat": 52.2, "lon": 17.2}],
        "analysis_date": (today - timedelta(days=4000)).isoformat(),
    })
    assert r_old.status_code == 422

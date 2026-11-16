"""IMGW-PIB (synop, ostrzeżenia, hydro), Open-Meteo (prognoza, gleba) i zapas wody."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data import soil, weather
from tests.test_data_support import (  # noqa: F401
    FakeResp, clean_state, http, open_meteo_forecast, open_meteo_soil_icon, synop_records,
)

NOW = datetime(2026, 11, 15, 12, 50, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(weather, "utcnow", lambda: NOW)
    monkeypatch.setattr(soil, "utcnow", lambda: NOW)


def test_nearest_synop_station_for_sniecsika():
    found = weather.nearest_synop(52.2020, 17.2012, synop_records())
    st, rec, dist = found
    assert st.name == "Poznań" and rec["id_stacji"] == "12330"
    assert 25 < dist < 45


def test_nearest_skips_station_without_data():
    # Koło (najbliższa dla tego punktu) nie ma temperatury → następna stacja
    st, rec, _ = weather.nearest_synop(52.20, 18.66, synop_records())
    assert st.name != "Koło"


def test_station_matched_by_name_when_id_differs():
    recs = [dict(synop_records()[0], id_stacji="99999")]
    st, rec, _ = weather.nearest_synop(52.4, 16.9, recs)
    assert st.name == "Poznań"


def test_parse_synop_record():
    cur = weather.parse_synop_record(synop_records()[0])
    assert cur == {"temp_c": 6.4, "humidity_pct": 87, "wind_ms": 5.0, "precip_mm": 0.2, "pressure_hpa": 1012.4}


def test_current_conditions_from_imgw(http):
    http.add("danepubliczne.imgw.pl/api/data/synop", lambda u, kw: FakeResp(200, synop_records()))
    cur, prov, warns = weather.current_conditions(52.2020, 17.2012, None)
    assert cur["temp_c"] == 6.4 and cur["station"] == "Poznań (IMGW-PIB)"
    assert prov["kind"] == "pomiar" and prov["observed_at"] == "2026-11-15T11:00:00Z"
    assert "Instytut Meteorologii" in prov["note"]


def test_current_conditions_fallback_to_open_meteo(http):
    http.add("open-meteo.com/v1/forecast", lambda u, kw: FakeResp(200, open_meteo_forecast()))
    fc = weather.fetch_forecast(52.2, 17.2, "auto")
    cur, prov, warns = weather.current_conditions(52.2, 17.2, fc)  # IMGW niedostępny
    assert cur["temp_c"] == 7.2 and cur["station"] is None
    assert prov["kind"] == "model" and prov["observed_at"] == "2026-11-15T11:45:00Z"
    assert any("IMGW" in w for w in warns)


def test_forecast_parsing_and_balance(http):
    http.add("open-meteo.com/v1/forecast", lambda u, kw: FakeResp(200, open_meteo_forecast(rain=1.0, et0=0.6)))
    w = weather.get_weather(52.2, 17.2, "ecmwf")
    assert len(w["daily"]) == 14 and w["daily"][0]["date"] == "2026-11-15"
    assert w["precip_7d_mm"] == 7.0 and w["precip_14d_mm"] == 14.0
    assert w["climatic_water_balance_14d_mm"] == pytest.approx(5.6)
    assert w["precip_past_7d_mm"] == 7.0
    assert w["provenance"]["forecast"]["kind"] == "prognoza"
    params = [kw for m, u, kw in http.calls if "open-meteo" in u][0]["params"]
    assert params["models"] == "ecmwf_ifs025" and params["past_days"] == 7
    assert "IMGW-PIB Synop" not in str(weather.WEATHER_MODELS)


def test_forecast_failure_gives_nulls(http):
    w = weather.get_weather(52.2, 17.2, "auto")
    assert w["daily"] == [] and w["climatic_water_balance_14d_mm"] is None
    assert w["provenance"]["forecast"]["kind"] == "brak"
    assert w["current"]["temp_c"] is None and w["provenance"]["current"]["kind"] == "brak"


def test_warnings_filtered_by_powiat(http):
    recs = [
        {"id": "1", "nazwa_zdarzenia": "Silny wiatr", "stopien": "2", "prawdopodobienstwo": "80",
         "obowiazuje_od": "2026-11-15 10:00:00", "obowiazuje_do": "2026-11-16 08:00:00", "teryt": ["3025", "3021"], "tresc": "Wiatr do 70 km/h"},
        {"id": "2", "nazwa_zdarzenia": "Oblodzenie", "stopien": "1", "obowiazuje_od": "2026-11-15 10:00:00",
         "obowiazuje_do": "2026-11-16 08:00:00", "teryt": ["1465"]},
        {"id": "3", "nazwa_zdarzenia": "Mgła", "stopien": "1", "obowiazuje_od": "2026-11-14 10:00:00",
         "obowiazuje_do": "2026-11-14 18:00:00", "teryt": ["3025"]},  # wygasło
    ]
    http.add("warningsmeteo", lambda u, kw: FakeResp(200, recs))
    items, prov = weather.warnings_for(52.2, 17.2, powiat_teryt="3025")
    assert [i["event"] for i in items] == ["Silny wiatr"]
    assert items[0]["level"] == 2 and items[0]["valid_to"] == "2026-11-16T08:00:00"
    assert prov["kind"] == "prognoza"
    items_v, _ = weather.warnings_for(52.2, 17.2, voiv_teryt="14")
    assert [i["event"] for i in items_v] == ["Oblodzenie"]


def test_hydro_nearest_with_api_coords_and_table_fallback(http):
    data = [
        {"kod_stacji": "152170010", "nazwa_stacji": "Śrem", "rzeka": "Warta", "lat": "52.09", "lon": "17.02",
         "stan": "182", "stan_data": "2026-11-15 12:00:00"},
        {"kod_stacji": "1", "nazwa_stacji": "Bez lokalizacji", "rzeka": "X", "stan": "100"},
    ]
    http.add("api/data/hydro", lambda u, kw: FakeResp(200, data))
    g, prov = weather.nearest_hydro(52.2020, 17.2012)
    assert g["name"] == "Śrem" and g["level_cm"] == 182 and g["warning_level_cm"] is None
    assert prov["kind"] == "pomiar"
    # czas z API hydro IMGW jest w UTC → ISO z "Z" (zgodnie z hydro-back.imgw.pl)
    assert g["measured_at"] == "2026-11-15T12:00:00Z" and prov["observed_at"] == "2026-11-15T12:00:00Z"
    assert not any("hydro2" in u for m, u, kw in http.calls)
    # API bez współrzędnych → tabela posterunków
    recs = weather.parse_hydro_records([{"id_stacji": "2", "stacja": "Poznań-Most Rocha", "rzeka": "Warta", "stan_wody": "150",
                                         "stan_wody_data_pomiaru": "2026-11-15 11:00:00"}])
    assert recs[0]["coords_source"] == "tabela" and recs[0]["lat"] == pytest.approx(52.405)


def test_hydro_far_gauge_ignored(http):
    http.add("api/data/hydro", lambda u, kw: FakeResp(200, [{"nazwa_stacji": "Tczew", "rzeka": "Wisła", "lat": 54.09, "lon": 18.8, "stan": 300}]))
    g, prov = weather.nearest_hydro(50.0, 20.0)
    assert g is None and prov["kind"] == "brak"


# ----------------------------------------------------------------------------- gleba
def test_soil_parsing_icon_layers_and_trend(http):
    http.add("api.open-meteo.com/v1/forecast", lambda u, kw: FakeResp(200, open_meteo_soil_icon(start_val=0.25, end_val=0.31)))
    s = soil.fetch_soil(52.2, 17.2)
    assert s["root"] == pytest.approx(0.31, abs=0.002) and s["surface"] == pytest.approx(0.31, abs=0.002)
    assert s["trend_7d"] == "rośnie" and s["soil_temp_c"] == 5.5 and s["kind"] == "model"


def test_soil_parsing_ecmwf_names():
    data = {"hourly": {"time": ["2026-11-15T10:00", "2026-11-15T11:00"], "soil_moisture_0_to_7cm": [0.3, 0.32],
                       "soil_moisture_7_to_28cm": [0.28, 0.29], "soil_temperature_0_to_7cm": [4.0, 4.5]}}
    s = soil.parse_soil_hourly(data, now=NOW)
    assert (s["surface"], s["root"], s["soil_temp_c"]) == (0.32, 0.29, 4.5)
    assert s["trend_7d"] is None  # za krótka seria


def test_soil_falls_back_to_era5(http):
    def archive(u, kw):
        assert kw["params"]["models"] == "era5_land"
        return FakeResp(200, {"hourly": {"time": ["2026-11-10T23:00"], "soil_moisture_0_to_7cm": [0.2],
                                         "soil_moisture_7_to_28cm": [0.22], "soil_temperature_0_to_7cm": [3.0]}})
    http.add("archive-api.open-meteo.com", archive)
    s = soil.fetch_soil(52.2, 17.2)
    assert s["kind"] == "reanaliza" and s["root"] == 0.22


def test_soil_classification():
    assert soil.classify_soil("Lekka piaszczysto-gliniasta (klasa IVa)")[0] == "glina_piaszczysta"
    assert soil.classify_soil("Gleba lekka piaszczysta (klasa IV-V)")[0] == "piasek_gliniasty"
    assert soil.classify_soil("Czarnoziem kujawski")[0] == "pyl"
    assert soil.classify_soil("Mada rzeczna")[0] == "glina_pylasta"
    assert soil.classify_soil("Gleba torfowa")[0] == "organiczna"
    assert soil.classify_soil("coś") == ("glina", False)


def test_water_reserve_balance():
    daily = [{"rain_mm": 0.0, "et0_mm": 3.0}] * 14
    # glina: FC 0.25, WP 0.12 → TAW 36.4 mm; θ=0.25 → AW=36.4; próg 18.2 → 6 dni po 3 mm, 7. dzień poniżej
    r = soil.water_reserve(0.25, 0.25, "glina", daily)
    assert r["days"] == 6 and not r["at_least"]
    wet = soil.water_reserve(0.25, 0.25, "glina", [{"rain_mm": 2.0, "et0_mm": 0.5}] * 14)
    assert wet["days"] == 14 and wet["at_least"]
    dry = soil.water_reserve(0.13, 0.13, "glina", daily)
    assert dry["days"] == 0
    assert soil.water_reserve(None, None, "glina", daily)["days"] is None

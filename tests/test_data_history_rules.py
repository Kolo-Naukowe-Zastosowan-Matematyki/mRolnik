"""Historia (wskaźniki Sentinel-2, klatki, opady vs norma) i progi z przepisów (program azotanowy, opryski) – atrapy HTTP."""
from __future__ import annotations

import io
from datetime import date, datetime

import numpy as np
from PIL import Image
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.data import history, rules, sentinel
from src.data.cadastre import parse_uldk_response
from src.hydromelior import hydrography
from src.web import routes_analysis
from tests.test_data_support import FakeResp, cdse_settings, clean_state, http, ndvi_png, uldk_text  # noqa: F401


@pytest.fixture
def parcel():
    return parse_uldk_response(uldk_text(52.2020, 17.2012))


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes_analysis.router)
    return TestClient(app)


def _cdse(http, stats=None, png=None):
    http.add("openid-connect/token", lambda u, kw: FakeResp(200, {"access_token": "T", "expires_in": 600}), "POST")
    if stats is not None:
        http.add("/api/v1/statistics", lambda u, kw: FakeResp(200, {"data": stats}), "POST")
    if png is not None:
        http.add("/api/v1/process", lambda u, kw: FakeResp(200, text="", content=png), "POST")


def _item(ym, ndvi, ndmi, evi, bare, valid=1.0, ndwi=-0.4):
    def st(v):
        return {"bands": {"B0": {"stats": {"mean": v, "sampleCount": 100, "noDataCount": 0}}}}
    return {"interval": {"from": f"{ym}-01T00:00:00Z"},
            "outputs": {"ndvi": st(ndvi), "ndmi": st(ndmi), "ndwi": st(ndwi), "evi": st(evi), "bare": st(bare), "cnt": st(valid)}}


# ------------------------------------------------------------------ historia
def test_monthly_indices_parse_and_cache(http, monkeypatch, parcel):
    cdse_settings(monkeypatch, True)
    _cdse(http, stats=[_item("2026-05", 0.71, 0.25, 0.52, 0.02), _item("2026-06", 0.65, 0.2, 0.45, 0.0, valid=0.3)])
    r = history.monthly_indices(parcel)
    assert r["provenance"]["kind"] == "pomiar"
    may, jun = r["months"]
    assert may == {"month": "2026-05", "ndvi": 0.71, "ndmi": 0.25, "ndwi": -0.4, "evi": 0.52, "bare_pct": 2.0, "valid_pct": 100.0}
    assert jun["ndvi"] is None and jun["valid_pct"] == 30.0          # <50% bez chmur → null, nie zgadujemy
    history.monthly_indices(parcel)
    assert http.count("/statistics") == 1
    assert set(r["indices"]) == {"ndvi", "ndmi", "ndwi", "evi", "bare"}


def test_monthly_indices_without_cdse(monkeypatch, parcel):
    cdse_settings(monkeypatch, False)
    r = history.monthly_indices(parcel)
    assert r["months"] == [] and r["provenance"]["kind"] == "brak"


def test_monthly_frame_colorized(http, monkeypatch, parcel):
    cdse_settings(monkeypatch, True)
    vals = np.full((8, 8), 0.6)
    vals[0, 0] = np.nan
    _cdse(http, png=ndvi_png(vals))
    f = history.monthly_frame(parcel, "2025-06", "ndvi")
    assert f["image"].startswith("data:image/png;base64,") and f["mean"] == pytest.approx(0.6, abs=0.01)
    assert f["bounds"][0][0] < f["bounds"][1][0] and f["provenance"]["kind"] == "pomiar"
    b = history.monthly_frame(parcel, "2025-06", "bare")
    assert b["mean"] == 0.0 and b["legend"][0]["label"].startswith("gleba odsłonięta")
    history.monthly_frame(parcel, "2025-06", "ndvi")
    assert http.count("/process") == 2          # klatka z cache


def test_monthly_frame_photo_true_colour(http, monkeypatch, parcel):
    cdse_settings(monkeypatch, True)
    rgba = np.zeros((8, 8, 4), dtype=np.uint8)
    rgba[..., 1] = 120
    rgba[..., 3] = 255
    rgba[0, 0, 3] = 0
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    _cdse(http, png=buf.getvalue())
    f = history.monthly_frame(parcel, "2025-06", "photo")
    assert f["index"] == "photo" and f["legend"] == [] and f["mean"] is None
    assert f["image"].startswith("data:image/png;base64,") and f["provenance"]["kind"] == "pomiar"
    body = [kw for m, u, kw in http.calls if "/process" in u][-1]["json"]
    assert "B02" in body["evalscript"] and "SCL" in body["evalscript"]
    assert "geometry" not in body["input"]["bounds"]          # zdjęcie z okolicą, nie przycięte do działki
    (s, w), (n, e) = f["bounds"]
    assert (n - s) * 110574 >= 1100 and s < 52.2020 < n
    # kadr szeroki jak mapa (ok. 3,6 : 1), żeby zdjęcie było tłem całego widoku pod wskaźnikami
    import math
    wide = (e - w) * 111320 * math.cos(math.radians((s + n) / 2)) / ((n - s) * 110574)
    assert 2.0 <= wide <= 2.6


def test_monthly_climate_vs_normals(http):
    days = [f"2026-10-{d:02d}" for d in range(1, 32)] + [f"2026-11-{d:02d}" for d in range(1, 10)]
    http.add("archive-api.open-meteo.com", lambda u, kw: FakeResp(200, {
        "daily": {"time": days, "precipitation_sum": [1.0] * len(days)},
        "hourly": {"time": [d + "T00:00" for d in days], "soil_moisture_7_to_28cm": [0.25] * len(days)}}))
    r = history.monthly_climate(52.2020, 17.2012, months=2)
    octo, nov = r["months"][-2:]
    assert octo["precip_mm"] == 31.0 and octo["complete"] is True and octo["soil_moisture_pct"] == 25.0
    assert nov["complete"] is False and nov["precip_normal_mm"] < octo["precip_normal_mm"]   # norma proporcjonalna
    assert r["provenance"]["precip"]["kind"] == "reanaliza" and r["provenance"]["normals"]["kind"] == "statystyka"


def test_history_endpoints(http, monkeypatch, client):
    cdse_settings(monkeypatch, False)
    http.add("uldk.gugik.gov.pl", lambda u, kw: FakeResp(200, text=uldk_text(52.2020, 17.2012)))
    r = client.get("/api/history/indices", params={"parcel_id": "302505_5.0014.114/1"}).json()
    assert r["parcel_id"] == "302505_5.0014.114/1" and r["months"] == []
    assert client.get("/api/history/frame", params={"parcel_id": "302505_5.0014.114/1", "month": "2025-13"}).status_code == 422
    f = client.get("/api/history/frame", params={"parcel_id": "302505_5.0014.114/1", "month": "2025-06", "index": "evi"}).json()
    assert f["image"] is None and f["provenance"]["kind"] == "brak"


# ------------------------------------------------------------------ przepisy
def test_nitrate_deadline_by_gmina():
    assert rules.gmina_code("302505_5.0014.114/1") == "302505"
    assert rules.nitrate_deadline_day("302505")["day"] == 25                 # Zaniemyśl – załącznik nr 3
    assert rules.nitrate_deadline_day(rules._nitrate_lists()["oct15"][0][:6])["day"] == 15
    assert rules.nitrate_deadline_day("999999")["day"] == 20
    assert rules.nitrate_deadline_day(None)["day"] == 20


@pytest.mark.parametrize("today,status", [(date(2026, 5, 10), "dozwolone"), (date(2026, 10, 28), "tylko_stale"),
                                          (date(2026, 2, 10), "luty_warunkowo"), (date(2026, 11, 15), "zakaz"),
                                          (date(2026, 1, 5), "zakaz")])
def test_nitrates_status(today, status):
    s = rules.nitrates_status(today, "302505", "Pszenica ozima")
    assert s["status"] == status and s["provenance"]["kind"] == "przepis"
    assert s["windows"][0]["to"].endswith("-10-25") and s["temp_threshold_c"] == 3.0
    assert rules.nitrates_status(today, "302505", "Jęczmień jary")["temp_threshold_c"] == 5.0


def test_temperature_threshold_run():
    days = [{"date": f"2026-02-{d:02d}", "t_mean_c": t} for d, t in enumerate([2, 4, 4, 4, 4, 2, 4, 4, 4, 4, 4, 6], start=1)]
    r = rules.temperature_threshold_series(days, 3.0)
    assert r["first_5day_run_start"] == "2026-02-07"


def test_spray_windows_against_law():
    now = datetime(2026, 11, 16, 6, 0)
    hourly = [{"time": f"2026-11-16T{h:02d}:00", "wind_ms": w, "gust_ms": None} for h, w in [(6, 3.9), (7, 4.0), (8, 4.5), (9, 6.2), (22, 1.0)]]
    out = rules.spray_windows(hourly, now)
    assert [o["allowed"] for o in out] == ["tak", "tak", "technika_75", "nie", "tak"]
    assert [o["daylight"] for o in out] == [True, True, True, True, False]
    sp = rules.spraying_rules()
    assert sp["wind_max_ms"] == 4.0 and sp["wind_max_drift_reduction_ms"] == 5.0 and "nie określa progu temperatury" in sp["provenance"]["note"]


def test_rules_endpoint_without_forecast(http, client):
    r = client.get("/api/reference/rules", params={"lat": 52.2020, "lon": 17.2012, "parcel_id": "302505_5.0014.114/1", "crop": "Rzepak ozimy"}).json()
    assert r["nitrates"]["gmina_teryt6"] == "302505" and r["nitrates"]["temp_threshold_c"] == 3.0
    assert r["spraying"]["hourly"] == [] and r["spraying"]["forecast_provenance"]["kind"] == "brak"
    assert r["temperature"]["provenance"]["kind"] == "brak"


# ------------------------------------------------------------------ hydrografia (warstwy WMS)
def test_default_wms_layers_are_hydrography(monkeypatch, http):
    monkeypatch.delenv("MROLNIK_HYDRO_WMS_URL", raising=False)
    monkeypatch.delenv("MROLNIK_HYDRO_WMS_LAYERS", raising=False)
    w = hydrography.wms_layers()[0]
    assert "row" in w["layers"] and "rz" in w["layers"] and not any(l.startswith("TPi") for l in w["layers"])
    assert w["crs"] == "EPSG:4326" and http.calls == []


def test_sowing_thresholds_are_odr_advice():
    s = rules.sowing_block([{"date": "2026-04-20", "soil_t_c": 7.0, "hours": 24}, {"date": "2026-04-22", "soil_t_c": 8.4, "hours": 24}],
                           "Kukurydza na ziarno", "2026-04-19", None)
    assert s["crop_threshold"]["min_c"] == 8.0 and s["first_day_at_threshold"] == "2026-04-22"
    assert s["provenance"]["kind"] == "zalecenie" and "nie przepisy" in s["provenance"]["note"]
    assert all(t["url"].startswith("https://") and ("Ośrodek Doradztwa" in t["source"] or "Ośrodka Doradztwa" in t["source"]) for t in s["thresholds"])
    assert rules.sowing_block([], "Buraki cukrowe", "2026-04-19", None)["crop_threshold"] is None


# ------------------------------------------------------------------ historia kilku działek naraz
def _two_parcels(monkeypatch):
    ps = {"302505_5.0014.114/1": parse_uldk_response(uldk_text(52.2020, 17.2012, "302505_5.0014.114/1")),
          "302505_5.0014.115": parse_uldk_response(uldk_text(52.2060, 17.2080, "302505_5.0014.115"))}
    monkeypatch.setattr(routes_analysis, "fetch_parcel_by_id", lambda pid: ps.get(pid))
    return ps


def test_history_indices_for_selected_parcels_uses_one_multipolygon(client, http, monkeypatch):
    cdse_settings(monkeypatch, True)
    ps = _two_parcels(monkeypatch)
    _cdse(http, stats=[_item("2026-05", 0.7, 0.2, 0.5, 0.0)])
    r = client.get("/api/history/indices", params={"parcel_ids": "302505_5.0014.115,302505_5.0014.114/1,302505_5.0014.999"}).json()
    assert r["parcel_ids"] == ["302505_5.0014.114/1", "302505_5.0014.115"] and r["missing_parcel_ids"] == ["302505_5.0014.999"]
    assert r["months"][0]["ndvi"] == 0.7 and http.count("/statistics") == 1
    assert r["provenance"]["note"].startswith("Zaznaczone działki: 2") and "Pominięto 1" in r["provenance"]["note"]
    body = [kw["json"] for m, u, kw in http.calls if "statistics" in u][0]
    assert body["input"]["bounds"]["geometry"]["type"] == "MultiPolygon"
    # cache nie przejmuje dopisku dla kilku działek
    single = history.monthly_indices(ps["302505_5.0014.114/1"])
    assert not (single["provenance"]["note"] or "").startswith("Zaznaczone")


def test_history_frame_for_selected_parcels_covers_all(client, http, monkeypatch):
    cdse_settings(monkeypatch, True)
    _two_parcels(monkeypatch)
    _cdse(http, png=ndvi_png(np.full((8, 8), 0.6)))
    f = client.get("/api/history/frame", params={"month": "2025-06", "index": "ndvi",
                                                 "parcel_ids": "302505_5.0014.114/1,302505_5.0014.115"}).json()
    assert f["image"].startswith("data:image/png;base64,") and len(f["parcel_ids"]) == 2
    (s, w), (n, e) = f["bounds"]
    assert s < 52.2020 < 52.2060 < n and w < 17.2012 < 17.2080 < e


def test_history_frame_refuses_distant_parcels(client, http, monkeypatch):
    """Działki w odległych miejscach (Murowana Goślina i okolice Bełchatowa): bez klatki na pół Polski i bez zapytania."""
    cdse_settings(monkeypatch, True)
    ps = {"302111_5.0008.9/2": parse_uldk_response(uldk_text(52.575, 17.010, "302111_5.0008.9/2")),
          "100303_2.0018.8/2": parse_uldk_response(uldk_text(51.300, 19.250, "100303_2.0018.8/2"))}
    monkeypatch.setattr(routes_analysis, "fetch_parcel_by_id", lambda pid: ps.get(pid))
    _cdse(http, png=ndvi_png(np.full((8, 8), 0.6)))
    for idx in ("ndvi", "photo"):
        f = client.get("/api/history/frame", params={"month": "2025-06", "index": idx, "parcel_ids": ",".join(ps)}).json()
        assert f["image"] is None and f["provenance"]["kind"] == "brak" and "osobno dla każdego miejsca" in f["provenance"]["note"]
    assert http.count("/process") == 0


def test_history_selection_rejects_bad_ids(client):
    assert client.get("/api/history/indices", params={"parcel_ids": "zly; DROP"}).status_code == 422
    many = ",".join(f"302505_5.0014.{i}" for i in range(51))
    assert client.get("/api/history/indices", params={"parcel_ids": many}).status_code == 422


# ------------------------------------------------------------------ zakres dat w Historii
def test_resolve_range_defaults_clamps_and_swaps(monkeypatch):
    from datetime import timezone as tz
    monkeypatch.setattr(history, "utcnow", lambda: datetime(2026, 11, 16, 10, 0, tzinfo=tz.utc))
    s, e = history.resolve_range(None, None)
    assert s == date(2023, 12, 1) and e.date() == date(2026, 11, 16)
    s, e = history.resolve_range("2010-05", "2018-02")
    assert s == date(2017, 1, 1) and e.date() == date(2018, 2, 28)          # archiwum od 2017
    s, e = history.resolve_range("2025-06", "2024-03")
    assert s == date(2024, 3, 1) and e.date() == date(2025, 6, 30)          # odwrócony zakres


def test_history_indices_custom_range_is_one_request(client, http, monkeypatch, parcel):
    cdse_settings(monkeypatch, True)
    monkeypatch.setattr(routes_analysis, "fetch_parcel_by_id", lambda pid: parcel)
    _cdse(http, stats=[_item("2017-01", 0.3, 0.1, 0.2, 0.4), _item("2026-10", 0.6, 0.2, 0.5, 0.0)])
    r = client.get("/api/history/indices", params={"parcel_id": "302505_5.0014.114/1", "start": "2017-01", "end": "2026-10"}).json()
    assert [m["month"] for m in r["months"]] == ["2017-01", "2026-10"] and http.count("/statistics") == 1
    body = [kw["json"] for m, u, kw in http.calls if "statistics" in u][0]
    assert body["aggregation"]["timeRange"]["from"].startswith("2017-01-01")
    assert client.get("/api/history/indices", params={"parcel_id": "302505_5.0014.114/1", "start": "2017-13"}).status_code == 422

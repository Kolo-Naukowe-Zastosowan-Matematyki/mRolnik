"""HydroMelior: hydrografia WFS, bilans wodny, zastawki demo per użytkownik, ekoschemat informacyjny."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data import soil, weather
from src.hydromelior import ecoscheme, hydrography, network, water
from tests.test_data_support import (  # noqa: F401
    FakeResp, cdse_settings, clean_state, http, open_meteo_forecast, open_meteo_soil_icon, uldk_text,
)
from src.data.cadastre import transformer_to_2180

NOW = datetime(2026, 11, 15, 12, 50, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(weather, "utcnow", lambda: NOW)
    monkeypatch.setattr(soil, "utcnow", lambda: NOW)


CAPS = """<?xml version="1.0"?><wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0">
<wfs:FeatureTypeList><wfs:FeatureType><wfs:Name>bdot:OT_SWRS_L</wfs:Name><wfs:Title>Rowy</wfs:Title></wfs:FeatureType>
<wfs:FeatureType><wfs:Name>bdot:OT_BUBD_A</wfs:Name><wfs:Title>Budynki</wfs:Title></wfs:FeatureType></wfs:FeatureTypeList></wfs:WFS_Capabilities>"""


def _gml(lat, lon, swap=False):
    x1, y1 = transformer_to_2180.transform(lon - 0.002, lat)
    x2, y2 = transformer_to_2180.transform(lon + 0.002, lat + 0.001)
    pts = [(y1, x1), (y2, x2)] if swap else [(x1, y1), (x2, y2)]
    pos = " ".join(f"{a:.2f} {b:.2f}" for a, b in pts)
    return f"""<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:bdot="x">
<wfs:member><bdot:OT_SWRS_L><bdot:x_kod>SWRS01</bdot:x_kod><bdot:nazwa>Rów Śnieciski</bdot:nazwa>
<bdot:geometria><gml:LineString><gml:posList>{pos}</gml:posList></gml:LineString></bdot:geometria></bdot:OT_SWRS_L></wfs:member>
</wfs:FeatureCollection>"""


@pytest.mark.parametrize("swap", [False, True])
def test_gml_parse_detects_axis_order(swap):
    x, y = transformer_to_2180.transform(17.2012, 52.2020)
    feats = hydrography.parse_gml_features(_gml(52.2020, 17.2012, swap), (x, y))
    assert len(feats) == 1
    f = feats[0]
    assert f["properties"]["kind"] == "rów melioracyjny" and f["properties"]["name"] == "Rów Śnieciski"
    lon0, lat0 = f["geometry"]["coordinates"][0]
    assert lat0 == pytest.approx(52.2020, abs=1e-4) and lon0 == pytest.approx(17.1992, abs=1e-4)


def test_hydrography_wfs_discovery_and_cache(http, monkeypatch):
    monkeypatch.setenv("MROLNIK_HYDRO_WFS_URLS", "https://example.gov.pl/wfs")

    def wfs(u, kw):
        if kw["params"]["request"] == "GetCapabilities":
            return FakeResp(200, text=CAPS)
        assert kw["params"]["typeNames"] == "bdot:OT_SWRS_L"
        return FakeResp(200, text=_gml(52.2020, 17.2012))
    http.add("example.gov.pl/wfs", wfs)
    feats, prov = hydrography.hydrography_near(52.2020, 17.2012)
    assert len(feats) == 1 and prov["kind"] == "pomiar" and feats[0]["properties"]["demo"] is False
    n = len(http.calls)
    hydrography.hydrography_near(52.2021, 17.2013)
    assert len(http.calls) == n  # cache 30 dni


def test_hydrography_failure_is_brak(http):
    feats, prov = hydrography.hydrography_near(50.0, 20.0)
    assert feats == [] and prov["kind"] == "brak"


def test_network_demo_only_near_sniecsika(http):
    far = network.get_network(50.0, 20.0, "u1")
    assert far["demo"] is False and not any(f["properties"].get("demo") for f in far["features"])
    near = network.get_network(52.2015, 17.2000, "u1")
    assert near["demo"] is True
    demo_feats = [f for f in near["features"] if f["properties"].get("demo")]
    assert demo_feats and all(f["properties"]["demo"] for f in demo_feats)


def test_gate_state_per_user_and_enum():
    g = network.update_gate_status("Z-1", "otwarta", user_id="alice")
    assert g["status"] == "otwarta" and g["retaining_cm"] == 0 and g["demo"] is True
    bob = {x["gate_id"]: x for x in network.demo_gates("bob")}
    assert bob["Z-1"]["status"] == "zamknieta"
    with pytest.raises(ValueError):
        network.update_gate_status("Z-1", "<img src=x onerror=alert(1)>", user_id="alice")
    with pytest.raises(ValueError):
        network.update_gate_status("Z-1", "zamknieta", 151, user_id="alice")
    assert network.update_gate_status("Z-9", "otwarta", user_id="alice") is None


def test_recommend_gate_rules():
    r = water.recommend_gate(wetness=0.4, balance_14d=-20, rain_next3=[0, 1, 0], flood_warning=False, gauge=None)
    assert r["position"] == "closed"
    r = water.recommend_gate(wetness=0.4, balance_14d=-20, rain_next3=[25, 1, 0], flood_warning=False, gauge=None)
    assert r["position"] == "open"
    r = water.recommend_gate(wetness=1.1, balance_14d=5, rain_next3=[0, 0, 0], flood_warning=False, gauge=None)
    assert r["position"] == "open"
    r = water.recommend_gate(wetness=0.7, balance_14d=2, rain_next3=[1, 1, 1], flood_warning=False, gauge=None)
    assert r["position"] == "half"
    r = water.recommend_gate(wetness=None, balance_14d=None, rain_next3=[], flood_warning=False, gauge=None)
    assert r["position"] is None
    r = water.recommend_gate(wetness=0.6, balance_14d=0, rain_next3=[0], flood_warning=False,
                             gauge={"name": "X", "level_cm": 400, "warning_level_cm": 380})
    assert r["position"] == "open"


def test_water_endpoint_shape(http):
    http.add("api.open-meteo.com/v1/forecast", lambda u, kw: FakeResp(
        200, open_meteo_soil_icon() if "hourly" in kw["params"] else open_meteo_forecast()))
    http.add("api/data/hydro", lambda u, kw: FakeResp(200, [{"nazwa_stacji": "Śrem", "rzeka": "Warta", "lat": 52.09, "lon": 17.02,
                                                            "stan": 182, "stan_data": "2026-11-15 12:00:00"}]))
    http.add("warningsmeteo", lambda u, kw: FakeResp(200, []))
    w = water.get_water_status(52.2020, 17.2012, "Średnia gliniasta")
    kinds = [r["kind"] for r in w["water_balance"]]
    assert kinds.count("obs") == 7 and kinds.count("forecast") == 14
    assert set(w["water_balance"][0]) == {"date", "precip_mm", "et0_mm", "kind"}
    g = w["river_gauge"]
    assert {"station", "river", "level_cm", "trend", "observed_at", "warning_level_cm", "alarm_level_cm", "provenance"} <= set(g)
    sm = w["soil_moisture"]
    assert sm["field_capacity_pct"] == 25.0 and sm["wilting_point_pct"] == 12.0 and sm["root_zone_pct"] == 34.0
    assert w["gate_recommendation"]["position"] in ("open", "half", "closed")
    assert w["gate_recommendation"]["provenance"]["kind"] == "model"


def test_ecoscheme_never_fully_met_and_html_escaped(http, monkeypatch):
    cdse_settings(monkeypatch, configured=False)
    http.add("uldk.gugik.gov.pl", lambda u, kw: FakeResp(200, text=uldk_text(52.2020, 17.2012, pid="302505_5.0014.114/1")))
    http.add("api.open-meteo.com/v1/forecast", lambda u, kw: FakeResp(200, open_meteo_forecast(rain=3, et0=0.5)))
    info = ecoscheme.ecoscheme_info(52.2020, 17.2012)
    assert info["informational"] is True and "nie stanowi dowodu" in info["disclaimer"]
    statuses = {c["name"]: c["status"] for c in info["criteria"]}
    assert statuses["Grunt zadeklarowany jako trwały użytek zielony (TUZ)"] == "brak danych"
    assert statuses["Woda utrzymywana na działce przez okres wymagany warunkami ekoschematu"] == "brak danych"
    assert "KRYTERIA SPEŁNIONE" not in str(info).upper() and "NIE da się potwierdzić" in info["summary"]
    html_ok = ecoscheme.render_html(info)
    assert "KRYTERIA SPEŁNIONE" not in html_ok.upper() and "ARiMR SPEŁNIONE" not in html_ok
    info["criteria"][0]["evidence"] = "<script>alert(1)</script>"
    html_doc = ecoscheme.render_html(info)
    assert "DOKUMENT INFORMACYJNY – nie stanowi dowodu ani wniosku do ARiMR" in html_doc
    assert "<script>" not in html_doc and "&lt;script&gt;" in html_doc


def test_hydrography_default_is_wms_only_without_requests(http, monkeypatch):
    monkeypatch.delenv("MROLNIK_HYDRO_WFS_URLS", raising=False)
    feats, prov = hydrography.hydrography_near(52.2020, 17.2012)
    assert feats == [] and prov["kind"] == "brak" and "WMS" in prov["note"]
    assert http.calls == []   # ISOK (401) i skorowidz BDOT10k nie są już odpytywane
    assert "isok" not in " ".join(hydrography.DEFAULT_WFS_URLS)


def test_hydrography_probe_reports_wms_only(http, monkeypatch):
    monkeypatch.delenv("MROLNIK_HYDRO_WFS_URLS", raising=False)
    monkeypatch.delenv("MROLNIK_HYDRO_WMS_URL", raising=False)
    caps = """<?xml version="1.0"?><WMS_Capabilities><Capability><Layer><Name>0</Name>
<Layer><Name>row</Name><Title>Rowy</Title></Layer><Layer><Name>bud</Name><Title>Budynki</Title></Layer></Layer></Capability></WMS_Capabilities>"""
    http.add("kompozycja_BDOT10k_WMS", lambda u, kw: FakeResp(200, text=caps))
    ok, msg = hydrography.probe()
    assert ok and "WMS" in msg and "MROLNIK_HYDRO_WFS_URLS" in msg

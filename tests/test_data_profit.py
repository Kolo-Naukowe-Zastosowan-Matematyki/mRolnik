"""Dane kalkulatora zysku: plon GUS × cena GUS − koszty ODR 2025; optymalny plan przez API (atrapy GUS)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.data import gus, profit_data
from src.web import routes_calc

YIELDS = {"pszenica ozima": 60.0, "pszenica jara": 60.0, "jęczmień ozimy": 50.0, "jęczmień jary": 50.0, "pszenżyto": 45.0,
          "żyto": 35.0, "owies": 35.0, "kukurydza na ziarno": 80.0, "rzepak ozimy": 32.0, "buraki cukrowe": 600.0,
          "ziemniaki": 280.0, "groch": 30.0, "bobik": 30.0, "łubin": 30.0}
PRICES = {"pszenica": 90.0, "jęczmień": 80.0, "pszenżyto": 75.0, "żyto": 70.0, "owies": 70.0, "kukurydza": 85.0, "rzepak": 190.0,
          "buraki cukrowe": 21.0, "ziemniaki": 60.0}


@pytest.fixture
def client(monkeypatch):
    def yields(crop, voiv):
        v = YIELDS[crop]
        return {"available": True, "years": [{"year": 2020 + i, "voivodeship": v + (i - 2), "poland": v} for i in range(6)],
                "provenance": {"source": "GUS"}}

    def prices(crops, voiv):
        return {"rows": [{"crops": [c], "available": True, "price": PRICES[c], "year": 2025, "scope": "województwo"} for c in crops],
                "provenance": {"source": "GUS ceny"}}
    monkeypatch.setattr(gus, "yields_reference", yields)
    monkeypatch.setattr(gus, "prices_reference", prices)
    app = FastAPI()
    app.include_router(routes_calc.router)
    return TestClient(app)


def test_crop_key_mapping():
    assert profit_data.crop_key("Pszenica ozima") == "pszenica_ozima"
    assert profit_data.crop_key("Pszenica jara") == "pszenica_jara"
    assert profit_data.crop_key("Jęczmień jary") == "jeczmien_jary"
    assert profit_data.crop_key("Kukurydza") == "kukurydza" and profit_data.crop_key("Kukurydza na kiszonkę") is None
    assert profit_data.crop_key("Burak cukrowy") == "burak_cukrowy" and profit_data.crop_key("Ziemniak") == "ziemniak"
    assert profit_data.crop_key("Trwały użytek zielony") == "tuz" and profit_data.crop_key("Soja") is None


def test_catalog_uses_last_5_years_and_odr_costs(client):
    d = client.get("/api/calculators/profit/catalog", params={"voivodeship": "wielkopolskie"}).json()
    w = next(c for c in d["crops"] if c["key"] == "pszenica_ozima")
    assert w["yield_dt_ha"] == pytest.approx(61.0)                 # lata 2021–2025: 59…63, bez 2020
    assert w["cost_zl_ha"] == pytest.approx(2715.74 + 2720) and w["price_zl_dt"] == 90.0
    assert w["margin_zl_ha"] == round(61.0 * 90 - 5435.74)
    g = next(c for c in d["crops"] if c["key"] == "groch")
    assert g["price_zl_dt"] == 158.0 and "ODR" in g["price_source"]
    assert "Kalkulacje rolnicze 2025" in d["provenance"]["costs"]["source"]


def test_profit_with_override_and_unknown(client):
    body = {"voivodeship": "wielkopolskie", "parcels": [
        {"id": "a", "area_ha": 2, "crop": "Pszenica ozima"},
        {"id": "b", "area_ha": 1, "crop": "Soja"},
        {"id": "c", "area_ha": 1, "crop": "Soja", "yield_dt_ha": 30, "price_zl_dt": 170, "cost_zl_ha": 4000}]}
    d = client.post("/api/calculators/profit", json=body).json()
    a, b, c = d["rows"]
    assert a["margin_zl"] == round(2 * (61.0 * 90 - 5435.74)) and "margin_zl" not in b and c["margin_zl"] == 1100
    assert d["incomplete"] == 1 and d["total_margin_zl"] == a["margin_zl"] + 1100


def test_optimal_plan_changes_crops(client):
    parcels = [{"id": f"d{i}", "area_ha": 4, "crop": "Pszenica ozima"} for i in range(4)] + [{"id": "tuz", "area_ha": 2, "crop": "Trwały użytek zielony"}]
    d = client.post("/api/calculators/optimal-plan", json={"voivodeship": "wielkopolskie", "parcels": parcels}).json()
    plans = [r["plan"] for r in d["rows"]]
    assert "pszenica_ozima" not in plans[:4] and plans[4] == "tuz" and d["rows"][4]["kept"]
    assert d["feasible"] and len(set(plans[:4])) >= 3                # 16 ha gruntów ornych → GAEC 7
    assert d["total_margin_zl"] > 0 and d["current_margin_zl"] is not None


def test_bad_input(client):
    assert client.post("/api/calculators/profit", json={"voivodeship": "xx", "parcels": [{"id": "a", "area_ha": 1}]}).status_code == 422
    assert client.post("/api/calculators/profit", json={"voivodeship": "wielkopolskie", "parcels": []}).status_code == 422


def test_own_machines_and_allowed(client):
    d = client.get("/api/calculators/profit/catalog", params={"voivodeship": "wielkopolskie", "include_services": "false"}).json()
    assert next(c for c in d["crops"] if c["key"] == "pszenica_ozima")["cost_zl_ha"] == pytest.approx(2715.74)
    parcels = [{"id": "a", "area_ha": 3, "crop": "Pszenica ozima"}, {"id": "b", "area_ha": 3, "crop": "Rzepak ozimy"}]
    d = client.post("/api/calculators/optimal-plan", json={"voivodeship": "wielkopolskie", "parcels": parcels}).json()
    assert "burak_cukrowy" not in d["allowed"] and all(r["plan"] != "burak_cukrowy" for r in d["rows"])
    d = client.post("/api/calculators/optimal-plan", json={"voivodeship": "wielkopolskie", "parcels": parcels,
                                                           "allowed": ["owies", "zyto"]}).json()
    assert {r["plan"] for r in d["rows"]} <= {"owies", "zyto"}

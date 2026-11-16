"""Testy kalkulatora siewu – wartości referencyjne (analiza wymiarowa w src/calculators/sowing.py)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.calculators.sowing import calc_cereal, calc_legume, calc_precision
from src.web import routes_calc


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(routes_calc.router)
    return TestClient(app)


def test_cereal_reference_191():
    r = calc_cereal(400, 45, 95, 99)
    assert r.seed_rate_kg_ha == pytest.approx(191.4, abs=2)
    assert r.plants_per_ha == 4_000_000
    assert r.bags_25kg == 8


def test_cereal_corrections_and_area():
    r = calc_cereal(400, 45, 95, 99, area_ha=10, sowing_date="pozny", soil="lekka", field_conditions="trudne")
    assert r.correction_factor == pytest.approx(1.1 * 1.1 * 1.15, abs=1e-3)
    assert r.total_seed_kg == pytest.approx(r.seed_rate_kg_ha * 10, rel=1e-3)


def test_precision_maize_75x15():
    r = calc_precision("kukurydza", 75, 15, 300, 95, 99)
    assert r.plants_per_ha == 88_889
    assert 26 <= r.seed_rate_kg_ha <= 30
    # jednostka siewna kukurydzy = 50 000 nasion
    assert r.units_per_ha == pytest.approx(r.seeds_per_ha / 50_000, abs=0.01)
    assert 1.7 < r.units_per_ha < 2.0
    assert r.warnings == []


def test_precision_sugar_beet_unit():
    r = calc_precision("burak_cukrowy", 45, 18, 12, 95, 99)
    assert r.plants_per_ha == pytest.approx(123_457, abs=1)
    assert r.seeds_per_unit == 100_000
    assert r.seed_rate_kg_ha < 3


def test_legume_pea_227():
    r = calc_legume(80, 250, 90, 98)
    assert r.seed_rate_kg_ha == pytest.approx(226.8, abs=1.5)
    assert r.plants_per_ha == 800_000


def test_api_sowing_precision(client):
    res = client.post("/api/calculators/sowing", json={"category": "precyzyjny", "crop": "kukurydza",
                                                       "row_spacing_cm": 75, "seed_spacing_cm": 15, "mtz_g": 300,
                                                       "germination_pct": 95, "purity_pct": 99, "area_ha": 2})
    assert res.status_code == 200
    body = res.json()
    assert 26 <= body["seed_rate_kg_ha"] <= 30
    assert body["assumptions"] and isinstance(body["warnings"], list)
    assert body["total_seed_kg"] == pytest.approx(body["seed_rate_kg_ha"] * 2, abs=0.2)


def test_api_sowing_cereal_defaults(client):
    res = client.post("/api/calculators/sowing", json={"category": "zboza"})
    assert res.status_code == 200
    assert res.json()["seed_rate_kg_ha"] == pytest.approx(191.4, abs=2)


@pytest.mark.parametrize("payload", [
    {"category": "zboza", "seeds_m2": -5},
    {"category": "zboza", "mtz_g": 0},
    {"category": "zboza", "germination_pct": 120},
    {"category": "zboza", "soil": "piasek"},
    {"category": "zboza", "unknown_field": 1},
    {"category": "ogrod"},
    {"category": "precyzyjny", "crop": "trawnik"},
    {"category": "precyzyjny", "row_spacing_cm": 0},
    {"category": "straczkowe", "area_ha": -1},
])
def test_api_sowing_validation(client, payload):
    assert client.post("/api/calculators/sowing", json=payload).status_code == 422


def test_api_meta(client):
    res = client.get("/api/calculators/meta")
    assert res.status_code == 200
    body = res.json()
    assert "kukurydza" in body["sowing"]["precision_crops"]
    assert "pszenica_ozima" in body["fertilizer"]["crops"]

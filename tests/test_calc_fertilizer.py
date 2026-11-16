"""Testy kalkulatora nawożenia NPK (wartości orientacyjne, zakresy agronomiczne)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.calculators.fertilizer import CROPS, calc_fertilizer, fertilizer_products
from src.web import routes_calc


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(routes_calc.router)
    return TestClient(app)


def test_wheat_8t_n_in_typical_range():
    r = calc_fertilizer("pszenica_ozima", 8.0)
    assert 120 <= r.n_kg_ha <= 200
    assert r.n_balance["requirement"] == 200
    assert sum(s["n_kg_ha"] for s in r.n_splits) == pytest.approx(r.n_kg_ha, abs=2)
    assert any("OSChR" in a for a in r.assumptions)


def test_wheat_high_yield_clamped_with_warning():
    r = calc_fertilizer("pszenica_ozima", 11.5, nmin_kg_ha=20)
    assert r.n_kg_ha == 200
    assert any("ograniczono" in w for w in r.warnings)


def test_rapeseed_and_maize_ranges():
    assert 140 <= calc_fertilizer("rzepak_ozimy", 4.0).n_kg_ha <= 220
    assert 120 <= calc_fertilizer("kukurydza_ziarno", 10.0).n_kg_ha <= 200
    assert 80 <= calc_fertilizer("burak_cukrowy", 60.0).n_kg_ha <= 160


def test_p2o5_to_superphosphate_conversion():
    simple, _ = fertilizer_products(0, 92, 60, 1.0)
    sp = next(p for p in simple if p.name == "Superfosfat potrójny")
    sk = next(p for p in simple if p.name == "Sól potasowa")
    assert sp.kg_ha == 200          # 92 kg P₂O₅ / 0,46
    assert sk.kg_ha == 100          # 60 kg K₂O / 0,60


def test_saletra_and_npk_variant():
    simple, npk = fertilizer_products(150, 60, 90, 2.0)
    sal = next(p for p in simple if p.name == "Saletra amonowa")
    assert sal.kg_ha == pytest.approx(150 / 0.34, abs=1)
    assert sal.kg_total == pytest.approx(2 * 150 / 0.34, abs=2)
    poli = npk[0]
    assert poli.kg_ha == 400        # 60 kg P₂O₅ / 0,15
    top_n = next(p for p in npk if p.name.startswith("Saletra"))
    assert top_n.kg_ha == pytest.approx((150 - 60) / 0.34, abs=1)


def test_abundance_class_scaling():
    low = calc_fertilizer("pszenica_ozima", 8.0, p_class="niska")
    mid = calc_fertilizer("pszenica_ozima", 8.0, p_class="srednia")
    vhigh = calc_fertilizer("pszenica_ozima", 8.0, p_class="bardzo_wysoka")
    assert low.p2o5_kg_ha > mid.p2o5_kg_ha > vhigh.p2o5_kg_ha == 0
    assert mid.p2o5_kg_ha == pytest.approx(8 * (8 + 3 * 0.5), abs=1)
    removed = calc_fertilizer("pszenica_ozima", 8.0, by_product_removed=True)
    assert removed.p2o5_kg_ha == pytest.approx(8 * 11, abs=1)
    assert removed.k2o_kg_ha > mid.k2o_kg_ha


def test_manure_nitrates_cap_warning():
    r = calc_fertilizer("kukurydza_ziarno", 10.0, organic_type="obornik_bydlecy", organic_dose=40)
    assert any("170" in w for w in r.warnings)
    assert r.organic_n_kg_ha == 200


def test_no_garden_crops():
    for garden in ("trawnik", "rosliny-doniczkowe", "krzewy-ozdobne", "kwiaty"):
        assert garden not in CROPS


def test_api_fertilizer_ok(client):
    res = client.post("/api/calculators/fertilizer", json={"crop": "pszenica_ozima", "expected_yield_t_ha": 8, "area_ha": 5})
    assert res.status_code == 200
    b = res.json()
    assert 120 <= b["n_kg_ha"] <= 200
    assert b["n_total_kg"] == pytest.approx(b["n_kg_ha"] * 5, abs=3)
    assert b["assumptions"] and isinstance(b["warnings"], list)


@pytest.mark.parametrize("payload", [
    {"crop": "trawnik"},
    {"crop": "pszenica_ozima", "area_ha": -1},
    {"crop": "pszenica_ozima", "expected_yield_t_ha": -3},
    {"crop": "pszenica_ozima", "p_class": "ogromna"},
    {"crop": "pszenica_ozima", "nmin_kg_ha": -10},
    {"crop": "pszenica_ozima", "season": "wiosna"},
])
def test_api_fertilizer_validation(client, payload):
    assert client.post("/api/calculators/fertilizer", json=payload).status_code == 422

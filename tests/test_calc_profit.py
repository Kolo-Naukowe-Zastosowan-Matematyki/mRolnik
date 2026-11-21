"""Kalkulator zysku i optymalny plan upraw (czyste funkcje)."""
from src.calculators.profit import CropEcon, ParcelIn, gaec7_check, optimal_plan, parcel_profit

CAT = [
    CropEcon("pszenica_ozima", "Pszenica ozima", 70.0, 90.0, 4000.0, "zboża"),      # 2300 zł/ha
    CropEcon("rzepak_ozimy", "Rzepak ozimy", 33.0, 200.0, 4500.0, "oleiste"),        # 2100
    CropEcon("kukurydza", "Kukurydza na ziarno", 80.0, 80.0, 5000.0, "kukurydza"),   # 1400
    CropEcon("groch", "Groch", 30.0, 110.0, 2400.0, "bobowate", legume=True),        # 900
    CropEcon("bez_danych", "Bez ceny", 30.0, None, 1000.0),
]


def test_parcel_profit():
    r = parcel_profit(2.0, 70, 90, 4000)
    assert r == {"revenue_zl": 12600, "direct_cost_zl": 8000, "margin_zl": 4600, "margin_zl_ha": 2300}


def test_small_farm_only_changes_crop():
    ps = [ParcelIn("a", 3, "pszenica_ozima"), ParcelIn("b", 4, "rzepak_ozimy")]
    r = optimal_plan(ps, CAT)
    plan = {x["pid"]: x["plan"] for x in r.rows}
    assert plan == {"a": "rzepak_ozimy", "b": "pszenica_ozima"} and r.feasible
    assert r.total_margin_zl == 3 * 2100 + 4 * 2300 and r.current_margin_zl == 3 * 2300 + 4 * 2100


def test_large_farm_meets_gaec7():
    ps = [ParcelIn(str(i), 5, "kukurydza") for i in range(6)]        # 30 ha, wszystko by poszło w pszenicę
    r = optimal_plan(ps, CAT)
    plan = [(x["area_ha"], x["plan"]) for x in r.rows]
    assert r.feasible and all(c["ok"] for c in gaec7_check(plan))
    assert len({k for _, k in plan}) >= 3 and "kukurydza" not in {k for _, k in plan}


def test_one_dominant_parcel_reported():
    ps = [ParcelIn("duza", 20, None), ParcelIn("m1", 3, None), ParcelIn("m2", 2, None)]
    r = optimal_plan(ps, CAT)
    assert not r.feasible and "podzielenia" in r.notes[0]


def test_grassland_kept():
    ps = [ParcelIn("tuz", 2, "tuz", arable=False), ParcelIn("a", 3, "pszenica_ozima")]
    r = optimal_plan(ps, CAT)
    assert r.rows[0]["kept"] and r.rows[0]["plan"] == "tuz"

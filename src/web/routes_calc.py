"""Endpointy kalkulatorów agronomicznych (cienka warstwa HTTP nad src/calculators).

Uwierzytelnienie dokłada server.py (Kontrakt 1); tu tylko walidacja wejścia (Pydantic v2).
"""
from __future__ import annotations

from typing import Annotated, List, Literal, Optional, Union

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.calculators.fertilizer import calc_fertilizer, fertilizer_meta
from src.calculators.sowing import calc_cereal, calc_legume, calc_precision, sowing_meta
from src.data import profit_data
from src.data.common import SourceError, friendly

router = APIRouter()

Germination = Annotated[float, Field(ge=50, le=100, description="Zdolność kiełkowania [%]")]
Purity = Annotated[float, Field(ge=50, le=100, description="Czystość nasion [%]")]
Mtz = Annotated[float, Field(gt=0, le=2000, description="Masa tysiąca nasion [g]")]
Area = Annotated[float, Field(gt=0, le=10000, description="Powierzchnia [ha]")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CerealSowing(_Strict):
    category: Literal["zboza"]
    seeds_m2: Annotated[float, Field(gt=0, le=1000, description="Obsada [szt. kiełkujących nasion/m²]")] = 400
    mtz_g: Mtz = 45
    germination_pct: Germination = 95
    purity_pct: Purity = 99
    area_ha: Area = 1.0
    sowing_date: Literal["wczesny", "optymalny", "pozny"] = "optymalny"
    soil: Literal["lekka", "srednia", "ciezka"] = "srednia"
    field_conditions: Literal["optymalne", "przecietne", "trudne"] = "przecietne"


class LegumeSowing(_Strict):
    category: Literal["straczkowe"]
    plants_m2: Annotated[float, Field(gt=0, le=300, description="Docelowa obsada [roślin/m²]")] = 80
    mtz_g: Mtz = 250
    germination_pct: Germination = 90
    purity_pct: Purity = 98
    area_ha: Area = 1.0


class PrecisionSowing(_Strict):
    category: Literal["precyzyjny"]
    crop: Literal["kukurydza", "burak_cukrowy", "slonecznik", "rzepak"] = "kukurydza"
    row_spacing_cm: Annotated[float, Field(ge=10, le=150)] = 75
    seed_spacing_cm: Annotated[float, Field(ge=1, le=100)] = 15
    mtz_g: Mtz = 300
    germination_pct: Germination = 95
    purity_pct: Purity = 99
    area_ha: Area = 1.0


SowingRequest = Annotated[Union[CerealSowing, LegumeSowing, PrecisionSowing], Field(discriminator="category")]


class FertilizerRequest(_Strict):
    crop: Literal["pszenica_ozima", "pszenica_jara", "jeczmien_ozimy", "jeczmien_jary", "zyto", "pszenzyto",
                  "rzepak_ozimy", "kukurydza_ziarno", "kukurydza_kiszonka", "burak_cukrowy", "ziemniak"]
    expected_yield_t_ha: Optional[Annotated[float, Field(gt=0, le=150)]] = None
    area_ha: Area = 1.0
    soil_category: Literal["bardzo_lekka", "lekka", "srednia", "ciezka"] = "srednia"
    nmin_kg_ha: Optional[Annotated[float, Field(ge=0, le=300)]] = None
    forecrop: Literal["zboza", "kukurydza", "rzepak", "burak_lisci", "ziemniak", "straczkowe", "motylkowe_wieloletnie"] = "zboza"
    p_class: Literal["bardzo_niska", "niska", "srednia", "wysoka", "bardzo_wysoka"] = "srednia"
    k_class: Literal["bardzo_niska", "niska", "srednia", "wysoka", "bardzo_wysoka"] = "srednia"
    by_product_removed: bool = False
    organic_type: Literal["brak", "obornik_bydlecy", "gnojowica_bydleca", "gnojowica_swinska", "pomiot_kurzy"] = "brak"
    organic_dose: Annotated[float, Field(ge=0, le=100, description="Dawka nawozu naturalnego [t lub m³ na ha]")] = 0
    soil_ph: Optional[Annotated[float, Field(ge=3.5, le=9.0)]] = None


@router.get("/api/calculators/meta")
def api_calc_meta():
    """Słowniki i wartości domyślne dla formularzy kalkulatorów."""
    return {"sowing": sowing_meta(), "fertilizer": fertilizer_meta()}


@router.post("/api/calculators/sowing")
def api_calc_sowing(req: SowingRequest):
    if isinstance(req, CerealSowing):
        res = calc_cereal(req.seeds_m2, req.mtz_g, req.germination_pct, req.purity_pct, req.area_ha,
                          req.sowing_date, req.soil, req.field_conditions)
    elif isinstance(req, LegumeSowing):
        res = calc_legume(req.plants_m2, req.mtz_g, req.germination_pct, req.purity_pct, req.area_ha)
    else:
        res = calc_precision(req.crop, req.row_spacing_cm, req.seed_spacing_cm, req.mtz_g,
                             req.germination_pct, req.purity_pct, req.area_ha)
    return res.to_dict()


@router.post("/api/calculators/fertilizer")
def api_calc_fertilizer(req: FertilizerRequest):
    return calc_fertilizer(**req.model_dump()).to_dict()


# ------------------------------ Kalkulator zysku i optymalny plan upraw ------------------------------
class ProfitParcel(_Strict):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    area_ha: Area
    crop: Optional[Annotated[str, Field(max_length=120)]] = None
    crop_key: Optional[Literal[tuple(profit_data.CATALOG)]] = None  # type: ignore[valid-type]
    yield_dt_ha: Optional[Annotated[float, Field(gt=0, le=2000)]] = None
    price_zl_dt: Optional[Annotated[float, Field(gt=0, le=5000)]] = None
    cost_zl_ha: Optional[Annotated[float, Field(ge=0, le=100000)]] = None


class ProfitRequest(_Strict):
    voivodeship: Annotated[str, Field(min_length=2, max_length=40)]
    parcels: Annotated[List[ProfitParcel], Field(min_length=1, max_length=50)]
    include_services: bool = True       # False = własne maszyny: tylko koszty bezpośrednie
    allowed: Optional[Annotated[List[Literal[tuple(profit_data.CATALOG)]], Field(max_length=20)]] = None  # type: ignore[valid-type]


def _call(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SourceError as exc:
        raise HTTPException(status_code=503, detail=f"Źródło danych niedostępne ({friendly(exc)}).") from exc


@router.get("/api/calculators/profit/catalog")
def api_profit_catalog(voivodeship: str = Query(..., min_length=2, max_length=40), include_services: bool = True):
    """Uprawy z plonem (GUS, 5 lat), ceną skupu (GUS) i kosztami (ODR 2025) – podstawa kalkulatora zysku."""
    return _call(profit_data.catalog, voivodeship, include_services)


@router.post("/api/calculators/profit")
def api_profit(req: ProfitRequest):
    """Wynik każdej działki: plon × cena − koszty bezpośrednie − usługi (bez dopłat) i suma gospodarstwa."""
    return _call(profit_data.profit, [p.model_dump() for p in req.parcels], req.voivodeship, req.include_services)


@router.post("/api/calculators/optimal-plan")
def api_optimal_plan(req: ProfitRequest):
    """Optymalna konfiguracja upraw na następny sezon: inna uprawa niż obecnie, warunki GAEC 7, największy wynik."""
    return _call(profit_data.plan, [p.model_dump() for p in req.parcels], req.voivodeship, req.include_services, req.allowed)

"""Kalkulator normy wysiewu (czyste funkcje, bez FastAPI).

Wzory (analiza wymiarowa):

* Zboża i strączkowe – obsada podana w szt./m²:
      MTZ [g / 1000 nasion]  →  masa 1 nasienia = MTZ / 1000 g
      g/m²  = szt./m² × MTZ / 1000
      kg/ha = g/m² × 10 000 m²/ha / 1000 g/kg = szt./m² × MTZ / 100
  Korekta na wartość użytkową materiału siewnego: ÷ (zdolność kiełkowania × czystość).
  Przykład: 400 szt./m², MTZ 45 g, 95 %, 99 %  →  400 × 45 / 100 / (0,95 × 0,99) ≈ 191 kg/ha.

* Siew punktowy (kukurydza, burak cukrowy, słonecznik, rzepak) – rozstaw rzędów i odstęp w rzędzie w cm:
      powierzchnia na 1 roślinę = rząd_cm × odstęp_cm  [cm²],  1 ha = 10⁸ cm²
      rośliny/ha = 10⁸ / (rząd_cm × odstęp_cm)          (75 × 15 cm → 88 889 roślin/ha)
      nasiona/ha = rośliny/ha ÷ (kiełkowanie × czystość)
      kg/ha      = nasiona/ha × MTZ / 1000 / 1000 = nasiona/ha × MTZ / 10⁶
  Przykład: kukurydza 75 × 15 cm, MTZ 300 g, 95 %, 99 % → ok. 28 kg/ha, ok. 1,9 jednostki siewnej/ha.

Jednostki siewne (handlowe opakowania nasion do siewu punktowego, typowe w Polsce):
kukurydza 50 000 nasion, burak cukrowy 100 000 nasion, słonecznik 150 000 nasion.
Rzepak hybrydowy sprzedawany jest w różnych opakowaniach – jednostki nie liczymy.

Wartości mają charakter orientacyjny; ostateczną normę ustala się wg zaleceń hodowcy odmiany
(COBORU, katalog odmian) i aktualnych warunków polowych.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

BAG_KG = 25.0
BIGBAG_KG = 500.0

# Współczynniki korekcyjne normy wysiewu zbóż (typowe zalecenia IUNG-PIB / COBORU).
SOWING_DATE_FACTORS: Dict[str, float] = {"wczesny": 0.90, "optymalny": 1.00, "pozny": 1.10}
SOIL_FACTORS: Dict[str, float] = {"lekka": 1.10, "srednia": 1.00, "ciezka": 0.95}
FIELD_CONDITION_FACTORS: Dict[str, float] = {"optymalne": 0.95, "przecietne": 1.00, "trudne": 1.15}

SOWING_DATE_LABELS = {"wczesny": "Wczesny", "optymalny": "Optymalny", "pozny": "Opóźniony"}
SOIL_LABELS = {"lekka": "Lekka", "srednia": "Średnia", "ciezka": "Ciężka"}
FIELD_CONDITION_LABELS = {"optymalne": "Dobre łoże siewne", "przecietne": "Przeciętne", "trudne": "Trudne (bryły, susza, resztki)"}


@dataclass(frozen=True)
class PrecisionCrop:
    label: str
    seeds_per_unit: Optional[int]          # nasion w jednostce siewnej (None = brak jednostki)
    typical_plants_ha: tuple               # (min, max) typowa obsada roślin na ha
    default_row_cm: float
    default_spacing_cm: float
    default_mtz_g: float


PRECISION_CROPS: Dict[str, PrecisionCrop] = {
    "kukurydza": PrecisionCrop("Kukurydza", 50_000, (65_000, 110_000), 75, 15, 300),
    "burak_cukrowy": PrecisionCrop("Burak cukrowy", 100_000, (90_000, 120_000), 45, 18, 12),
    "slonecznik": PrecisionCrop("Słonecznik", 150_000, (45_000, 75_000), 75, 22, 60),
    "rzepak": PrecisionCrop("Rzepak (siew punktowy)", None, (300_000, 600_000), 45, 5, 5),
}

# Typowe obsady i MTZ dla podpowiedzi w formularzu (nie wpływają na obliczenie).
CEREAL_PRESETS = {
    "pszenica_ozima": {"label": "Pszenica ozima", "seeds_m2": 400, "mtz_g": 45},
    "pszenica_jara": {"label": "Pszenica jara", "seeds_m2": 500, "mtz_g": 40},
    "jeczmien_ozimy": {"label": "Jęczmień ozimy", "seeds_m2": 300, "mtz_g": 48},
    "jeczmien_jary": {"label": "Jęczmień jary", "seeds_m2": 350, "mtz_g": 47},
    "zyto": {"label": "Żyto (populacyjne)", "seeds_m2": 300, "mtz_g": 35},
    "pszenzyto": {"label": "Pszenżyto ozime", "seeds_m2": 350, "mtz_g": 48},
    "owies": {"label": "Owies", "seeds_m2": 500, "mtz_g": 35},
}
LEGUME_PRESETS = {
    "groch": {"label": "Groch siewny", "plants_m2": 80, "mtz_g": 250},
    "bobik": {"label": "Bobik", "plants_m2": 50, "mtz_g": 500},
    "lubin_waskolistny": {"label": "Łubin wąskolistny", "plants_m2": 110, "mtz_g": 150},
    "soja": {"label": "Soja", "plants_m2": 70, "mtz_g": 180},
}

COMMON_ASSUMPTIONS = [
    "Wynik orientacyjny – ostateczną normę ustal wg zaleceń hodowcy odmiany i warunków polowych.",
    "Źródła: typowe zalecenia agrotechniczne IUNG-PIB i COBORU (Lista Opisowa Odmian); "
    "zdolność kiełkowania i czystość odczytaj z etykiety kwalifikowanego materiału siewnego.",
]


@dataclass
class SowingResult:
    category: str
    seed_rate_kg_ha: float
    total_seed_kg: float
    bags_25kg: int
    area_ha: float
    seeds_per_m2: float
    seeds_per_ha: int
    plants_per_ha: int
    seed_rate_base_kg_ha: Optional[float] = None
    correction_factor: Optional[float] = None
    plants_per_m2: Optional[float] = None
    units_per_ha: Optional[float] = None
    units_total: Optional[float] = None
    seeds_per_unit: Optional[int] = None
    drill_seed_spacing_cm: Optional[float] = None
    bigbags_500kg: Optional[float] = None
    recommendation: str = ""
    assumptions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _bags(total_kg: float) -> int:
    return int(math.ceil(total_kg / BAG_KG - 1e-9)) if total_kg > 0 else 0


def _seed_value(germination_pct: float, purity_pct: float) -> float:
    """Wartość użytkowa materiału siewnego (ułamek)."""
    return (germination_pct / 100.0) * (purity_pct / 100.0)


def kg_per_ha_from_density(seeds_m2: float, mtz_g: float, germination_pct: float, purity_pct: float) -> float:
    """kg/ha = szt./m² × MTZ / 100 ÷ (kiełkowanie × czystość)."""
    return seeds_m2 * mtz_g / 100.0 / _seed_value(germination_pct, purity_pct)


def calc_cereal(
    seeds_m2: float,
    mtz_g: float,
    germination_pct: float = 95.0,
    purity_pct: float = 99.0,
    area_ha: float = 1.0,
    sowing_date: str = "optymalny",
    soil: str = "srednia",
    field_conditions: str = "przecietne",
) -> SowingResult:
    """Norma wysiewu zbóż z obsady (szt. kiełkujących nasion/m²) i MTZ, z korektami."""
    base = kg_per_ha_from_density(seeds_m2, mtz_g, germination_pct, purity_pct)
    factor = SOWING_DATE_FACTORS[sowing_date] * SOIL_FACTORS[soil] * FIELD_CONDITION_FACTORS[field_conditions]
    rate = base * factor
    total = rate * area_ha
    sown_seeds_m2 = seeds_m2 / _seed_value(germination_pct, purity_pct) * factor

    warnings: List[str] = []
    if rate < 100:
        warnings.append("Norma poniżej 100 kg/ha – nietypowo niska dla zbóż; sprawdź obsadę i MTZ.")
    elif rate > 300:
        warnings.append("Norma powyżej 300 kg/ha – nietypowo wysoka dla zbóż; sprawdź obsadę i MTZ.")
    if germination_pct < 85:
        warnings.append("Zdolność kiełkowania poniżej 85% – niższa niż minimum dla kwalifikowanego materiału siewnego zbóż.")

    if rate < 130:
        rec = "Norma niska – odpowiednia przy wczesnym siewie i odmianach dobrze krzewiących się."
    elif rate > 230:
        rec = "Norma podwyższona (siew opóźniony lub trudne łoże siewne) – zapewni właściwą obsadę kłosów."
    else:
        rec = "Norma w typowym zakresie dla zbóż."

    return SowingResult(
        category="zboza",
        seed_rate_kg_ha=round(rate, 1),
        seed_rate_base_kg_ha=round(base, 1),
        correction_factor=round(factor, 3),
        total_seed_kg=round(total, 1),
        bags_25kg=_bags(total),
        bigbags_500kg=round(total / BIGBAG_KG, 2),
        area_ha=area_ha,
        seeds_per_m2=round(sown_seeds_m2, 1),
        seeds_per_ha=int(round(sown_seeds_m2 * 10_000)),
        plants_per_ha=int(round(seeds_m2 * 10_000)),
        plants_per_m2=seeds_m2,
        recommendation=rec,
        assumptions=[
            "Wzór: kg/ha = obsada [szt./m²] × MTZ [g] ÷ 100 ÷ (kiełkowanie × czystość).",
            f"Korekta: termin × gleba × warunki = {factor:.3f} (typowe współczynniki 0,90–1,15).",
            *COMMON_ASSUMPTIONS,
        ],
        warnings=warnings,
    )


def calc_legume(
    plants_m2: float,
    mtz_g: float,
    germination_pct: float = 90.0,
    purity_pct: float = 98.0,
    area_ha: float = 1.0,
) -> SowingResult:
    """Norma wysiewu roślin strączkowych z docelowej obsady (rośliny/m²) i MTZ."""
    rate = kg_per_ha_from_density(plants_m2, mtz_g, germination_pct, purity_pct)
    total = rate * area_ha
    seeds_m2 = plants_m2 / _seed_value(germination_pct, purity_pct)
    warnings: List[str] = []
    if rate > 400:
        warnings.append("Norma powyżej 400 kg/ha – sprawdź obsadę i MTZ (typowo 150–350 kg/ha dla grochu i bobiku).")
    if plants_m2 > 150:
        warnings.append("Obsada powyżej 150 roślin/m² jest nietypowa dla roślin strączkowych.")
    return SowingResult(
        category="straczkowe",
        seed_rate_kg_ha=round(rate, 1),
        total_seed_kg=round(total, 1),
        bags_25kg=_bags(total),
        area_ha=area_ha,
        seeds_per_m2=round(seeds_m2, 1),
        seeds_per_ha=int(round(seeds_m2 * 10_000)),
        plants_per_ha=int(round(plants_m2 * 10_000)),
        plants_per_m2=plants_m2,
        recommendation="Siej na głębokość 3–5 cm (bobik 6–8 cm); nasiona zaszczep szczepionką bakterii brodawkowych (Rhizobium), jeśli na polu dawno nie było strączkowych.",
        assumptions=[
            "Wzór: kg/ha = obsada [roślin/m²] × MTZ [g] ÷ 100 ÷ (kiełkowanie × czystość).",
            *COMMON_ASSUMPTIONS,
        ],
        warnings=warnings,
    )


def calc_precision(
    crop: str,
    row_spacing_cm: float,
    seed_spacing_cm: float,
    mtz_g: float,
    germination_pct: float = 95.0,
    purity_pct: float = 99.0,
    area_ha: float = 1.0,
) -> SowingResult:
    """Siew punktowy: obsada z rozstawu rzędów i docelowego odstępu roślin w rzędzie."""
    info = PRECISION_CROPS[crop]
    plants_ha = 1e8 / (row_spacing_cm * seed_spacing_cm)
    value = _seed_value(germination_pct, purity_pct)
    seeds_ha = plants_ha / value
    rate = seeds_ha * mtz_g / 1e6
    total = rate * area_ha
    units_ha = seeds_ha / info.seeds_per_unit if info.seeds_per_unit else None

    warnings: List[str] = []
    lo, hi = info.typical_plants_ha
    if plants_ha < lo:
        warnings.append(f"Obsada {plants_ha:,.0f} roślin/ha jest poniżej typowego zakresu dla uprawy ({lo:,}–{hi:,}).".replace(",", " "))
    elif plants_ha > hi:
        warnings.append(f"Obsada {plants_ha:,.0f} roślin/ha jest powyżej typowego zakresu dla uprawy ({lo:,}–{hi:,}).".replace(",", " "))

    assumptions = [
        "Wzór: rośliny/ha = 10⁸ ÷ (rozstaw rzędów [cm] × odstęp w rzędzie [cm]); "
        "nasiona/ha = rośliny/ha ÷ (kiełkowanie × czystość); kg/ha = nasiona/ha × MTZ [g] ÷ 10⁶.",
        "Odstęp w rzędzie traktujemy jako docelowy odstęp roślin; siewnik ustaw na odstęp nasion "
        "pomniejszony o straty (pole „odstęp nasion na siewniku”).",
    ]
    if info.seeds_per_unit:
        assumptions.append(f"Jednostka siewna: {info.seeds_per_unit:,} nasion ({info.label.lower()}).".replace(",", " "))
    assumptions.extend(COMMON_ASSUMPTIONS)

    recs = {
        "kukurydza": "Siej, gdy gleba na głębokości 5 cm ma co najmniej 8–10°C; głębokość 4–6 cm.",
        "burak_cukrowy": "Siej na głębokość 2–3 cm w dobrze odleżałą, wyrównaną glebę.",
        "slonecznik": "Siej, gdy gleba ma co najmniej 8°C; głębokość 4–6 cm.",
        "rzepak": "Siej na głębokość 1,5–2 cm; zadbaj o wilgotne łoże siewne.",
    }
    return SowingResult(
        category="precyzyjny",
        seed_rate_kg_ha=round(rate, 1),
        total_seed_kg=round(total, 1),
        bags_25kg=_bags(total),
        area_ha=area_ha,
        seeds_per_m2=round(seeds_ha / 10_000, 2),
        seeds_per_ha=int(round(seeds_ha)),
        plants_per_ha=int(round(plants_ha)),
        plants_per_m2=round(plants_ha / 10_000, 2),
        units_per_ha=round(units_ha, 2) if units_ha is not None else None,
        units_total=round(units_ha * area_ha, 1) if units_ha is not None else None,
        seeds_per_unit=info.seeds_per_unit,
        drill_seed_spacing_cm=round(seed_spacing_cm * value, 1),
        recommendation=recs.get(crop, ""),
        assumptions=assumptions,
        warnings=warnings,
    )


def sowing_meta() -> dict:
    """Słowniki i wartości domyślne dla formularza (frontend nie duplikuje danych)."""
    return {
        "categories": {"zboza": "Zboża (siew rzędowy)", "straczkowe": "Rośliny strączkowe", "precyzyjny": "Siew punktowy"},
        "cereal_presets": CEREAL_PRESETS,
        "legume_presets": LEGUME_PRESETS,
        "precision_crops": {
            k: {
                "label": v.label,
                "seeds_per_unit": v.seeds_per_unit,
                "typical_plants_ha": list(v.typical_plants_ha),
                "row_cm": v.default_row_cm,
                "spacing_cm": v.default_spacing_cm,
                "mtz_g": v.default_mtz_g,
            }
            for k, v in PRECISION_CROPS.items()
        },
        "sowing_date": SOWING_DATE_LABELS,
        "soil": SOIL_LABELS,
        "field_conditions": FIELD_CONDITION_LABELS,
    }

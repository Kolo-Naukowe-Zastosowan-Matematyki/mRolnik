"""Kalkulator nawożenia NPK na hektar (czyste funkcje, bez FastAPI).

Podejście (uproszczona metoda bilansowa, wg typowych zaleceń IUNG-PIB „Zalecenia nawozowe”):

* Azot:  N = pobranie jednostkowe [kg N/t plonu, z produktem ubocznym] × plon [t/ha]
             − Nmin gleby wiosną (0–60/90 cm) − mineralizacja w okresie wegetacji (rośliny długo rosnące)
             − następczy wpływ przedplonu − równoważnik N z nawozów naturalnych w 1. roku,
  a wynik ograniczamy do typowego zakresu dawek dla uprawy (np. pszenica ozima 120–200 kg N/ha).
* Fosfor i potas (jako P₂O₅ i K₂O): wynos z plonem × współczynnik zależny od klasy zasobności gleby
  (bardzo niska 1,5; niska 1,3; średnia 1,0; wysoka 0,6; bardzo wysoka 0) − składniki z nawozów naturalnych.
* Nawozy handlowe: dawka produktu = potrzeba składnika ÷ zawartość składnika w nawozie
  (np. superfosfat potrójny 46% P₂O₅: 92 kg P₂O₅ → 200 kg nawozu).

Wszystkie współczynniki to wartości typowe, orientacyjne. Wynik NIE zastępuje planu nawozowego
ani analizy gleby (Okręgowa Stacja Chemiczno-Rolnicza, OSChR).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

# Limit azotu z nawozów naturalnych wg programu azotanowego (dyrektywa azotanowa 91/676/EWG).
MANURE_N_CAP_KG_HA = 170.0


@dataclass(frozen=True)
class CropNorm:
    label: str
    yield_unit: str                 # opis jednostki plonu
    yield_range: tuple              # (min, max) typowy plon t/ha – do ostrzeżeń
    default_yield: float
    n_per_t: float                  # kg N / t plonu głównego (z produktem ubocznym)
    p_main: float                   # kg P₂O₅ / t plonu głównego
    p_by: float                     # kg P₂O₅ w produkcie ubocznym na t plonu głównego
    k_main: float                   # kg K₂O / t plonu głównego
    k_by: float                     # kg K₂O w produkcie ubocznym na t plonu głównego
    n_range: tuple                  # (min, max) typowa łączna dawka N kg/ha
    mineralization: float           # dodatkowe N z mineralizacji w sezonie (rośliny długo rosnące)
    by_product: Optional[str]       # nazwa produktu ubocznego
    splits: tuple                   # ((termin, udział), ...)


_CEREAL_SPLITS = (("Start wegetacji wiosną", 0.5), ("Strzelanie w źdźbło (BBCH 30–32)", 0.3), ("Liść flagowy – kłoszenie (BBCH 37–51)", 0.2))
_SPRING_CEREAL_SPLITS = (("Przedsiewnie", 0.6), ("Krzewienie – strzelanie w źdźbło", 0.4))

CROPS: Dict[str, CropNorm] = {
    "pszenica_ozima": CropNorm("Pszenica ozima", "t ziarna/ha", (3, 12), 8.0, 25, 8, 3, 5, 13, (120, 200), 0, "słoma", _CEREAL_SPLITS),
    "pszenica_jara": CropNorm("Pszenica jara", "t ziarna/ha", (2.5, 8), 5.5, 27, 8, 3, 5, 13, (80, 140), 0, "słoma", _SPRING_CEREAL_SPLITS),
    "jeczmien_ozimy": CropNorm("Jęczmień ozimy", "t ziarna/ha", (3, 10), 7.0, 23, 8, 2.5, 6, 15, (100, 160), 0, "słoma", _CEREAL_SPLITS),
    "jeczmien_jary": CropNorm("Jęczmień jary", "t ziarna/ha", (2.5, 8), 5.0, 22, 8, 2.5, 6, 15, (60, 110), 0, "słoma", _SPRING_CEREAL_SPLITS),
    "zyto": CropNorm("Żyto", "t ziarna/ha", (2, 9), 5.5, 23, 8, 3, 6, 17, (60, 120), 0, "słoma", _CEREAL_SPLITS),
    "pszenzyto": CropNorm("Pszenżyto ozime", "t ziarna/ha", (3, 10), 6.5, 24, 8, 3, 6, 15, (90, 150), 0, "słoma", _CEREAL_SPLITS),
    "rzepak_ozimy": CropNorm("Rzepak ozimy", "t nasion/ha", (1.5, 6), 4.0, 55, 18, 12, 10, 40, (140, 220), 0, "słoma",
                             (("Ruszenie wegetacji wiosną", 0.6), ("Początek wzrostu pędu (BBCH 30–32)", 0.4))),
    "kukurydza_ziarno": CropNorm("Kukurydza na ziarno", "t ziarna/ha", (4, 15), 10.0, 25, 8, 4, 5, 20, (120, 200), 30, "słoma",
                                 (("Przedsiewnie", 0.7), ("Pogłównie, faza 4–6 liści", 0.3))),
    "kukurydza_kiszonka": CropNorm("Kukurydza na kiszonkę", "t zielonki/ha", (25, 80), 50.0, 4.0, 1.6, 0, 4.5, 0, (120, 180), 30, None,
                                   (("Przedsiewnie", 0.7), ("Pogłównie, faza 4–6 liści", 0.3))),
    "burak_cukrowy": CropNorm("Burak cukrowy", "t korzeni/ha", (30, 100), 60.0, 4.5, 1.0, 0.6, 2.5, 3.5, (80, 160), 60, "liście",
                              (("Przedsiewnie", 0.6), ("Pogłównie, faza 4–6 liści", 0.4))),
    "ziemniak": CropNorm("Ziemniak", "t bulw/ha", (15, 60), 35.0, 5.0, 1.4, 0.2, 6.0, 1.0, (80, 160), 30, "łęty",
                         (("Przed sadzeniem", 0.7), ("Przed zwarciem rzędów", 0.3))),
}

SOIL_CATEGORIES = {
    "bardzo_lekka": "Bardzo lekka (piasek luźny i słabo gliniasty)",
    "lekka": "Lekka (piasek gliniasty)",
    "srednia": "Średnia (glina lekka, pył)",
    "ciezka": "Ciężka (glina średnia i ciężka, ił)",
}
# Domyślne Nmin wiosną (0–60 cm), gdy brak badania – wartości typowe dla Polski.
DEFAULT_NMIN = {"bardzo_lekka": 30.0, "lekka": 40.0, "srednia": 50.0, "ciezka": 60.0}

ABUNDANCE_CLASSES = {
    "bardzo_niska": ("Bardzo niska", 1.5),
    "niska": ("Niska", 1.3),
    "srednia": ("Średnia", 1.0),
    "wysoka": ("Wysoka", 0.6),
    "bardzo_wysoka": ("Bardzo wysoka", 0.0),
}

FORECROPS = {
    "zboza": ("Zboża", 0.0),
    "kukurydza": ("Kukurydza", 0.0),
    "rzepak": ("Rzepak", 20.0),
    "burak_lisci": ("Burak (liście przyorane)", 30.0),
    "ziemniak": ("Ziemniak", 10.0),
    "straczkowe": ("Strączkowe (groch, bobik, łubin)", 30.0),
    "motylkowe_wieloletnie": ("Motylkowe wieloletnie (koniczyna, lucerna)", 50.0),
}

# Nawozy naturalne: (etykieta, jednostka, kg N/j., kg P₂O₅/j., kg K₂O/j., równoważnik N w 1. roku)
ORGANIC = {
    "brak": ("Brak", "", 0.0, 0.0, 0.0, 0.0),
    "obornik_bydlecy": ("Obornik bydlęcy", "t", 5.0, 3.0, 7.0, 0.3),
    "gnojowica_bydleca": ("Gnojowica bydlęca", "m³", 4.0, 2.0, 5.0, 0.5),
    "gnojowica_swinska": ("Gnojowica świńska", "m³", 5.0, 3.0, 2.5, 0.6),
    "pomiot_kurzy": ("Pomiot kurzy", "t", 20.0, 15.0, 12.0, 0.5),
}
ORGANIC_P_EFF = 0.6   # wykorzystanie P₂O₅ z nawozów naturalnych w 1. roku (typowo)
ORGANIC_K_EFF = 0.8   # wykorzystanie K₂O z nawozów naturalnych w 1. roku (typowo)
# Składniki produktu ubocznego pozostawionego na polu uwalniają się powoli – w bieżącym roku
# uznajemy, że ok. połowa nie jest dostępna (liczymy 50% jego zawartości do wynosu).
BY_PRODUCT_LEFT_SHARE = 0.5

# Nawozy mineralne: zawartość składnika (ułamek masy)
SALETRA_N = 0.34
MOCZNIK_N = 0.46
SUPERFOSFAT_P2O5 = 0.46
SOL_POTASOWA_K2O = 0.60
NPK_15 = 0.15
BAG_KG = 50.0

DISCLAIMER = ("Wynik orientacyjny – nie zastępuje planu nawozowego ani analizy gleby "
              "wykonanej przez Okręgową Stację Chemiczno-Rolniczą (OSChR).")


@dataclass
class Product:
    name: str
    content: str
    kg_ha: float
    kg_total: float
    bags_50kg: int


@dataclass
class FertilizerResult:
    crop: str
    crop_label: str
    area_ha: float
    expected_yield_t_ha: float
    n_kg_ha: float
    p2o5_kg_ha: float
    k2o_kg_ha: float
    n_total_kg: float
    p2o5_total_kg: float
    k2o_total_kg: float
    n_balance: Dict[str, float]
    n_splits: List[Dict[str, object]]
    products_simple: List[Product]
    products_npk: List[Product]
    organic_n_kg_ha: float
    recommendations: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _product(name: str, content: str, kg_ha: float, area_ha: float) -> Product:
    kg_ha = max(0.0, kg_ha)
    total = kg_ha * area_ha
    return Product(name, content, round(kg_ha, 0), round(total, 0), int(math.ceil(total / BAG_KG - 1e-9)) if total > 0 else 0)


def fertilizer_products(n: float, p2o5: float, k2o: float, area_ha: float) -> tuple:
    """Zwraca (wariant nawozów jednoskładnikowych, wariant z polifoską 15-15-15)."""
    simple = [
        _product("Saletra amonowa", "34% N", n / SALETRA_N, area_ha),
        _product("Mocznik (alternatywa dla saletry)", "46% N", n / MOCZNIK_N, area_ha),
        _product("Superfosfat potrójny", "46% P₂O₅", p2o5 / SUPERFOSFAT_P2O5, area_ha),
        _product("Sól potasowa", "60% K₂O", k2o / SOL_POTASOWA_K2O, area_ha),
    ]
    # Wariant NPK: polifoska pokrywa potrzebę P₂O₅ (lub K₂O, jeśli mniejsza), resztę uzupełniamy.
    base = min(p2o5, k2o)
    npk_kg = base / NPK_15 if base > 0 else 0.0
    supplied = npk_kg * NPK_15
    npk = [
        _product("Polifoska / NPK 15-15-15", "15% N, 15% P₂O₅, 15% K₂O", npk_kg, area_ha),
        _product("Saletra amonowa (uzupełnienie N)", "34% N", (n - supplied) / SALETRA_N, area_ha),
        _product("Superfosfat potrójny (uzupełnienie P₂O₅)", "46% P₂O₅", (p2o5 - supplied) / SUPERFOSFAT_P2O5, area_ha),
        _product("Sól potasowa (uzupełnienie K₂O)", "60% K₂O", (k2o - supplied) / SOL_POTASOWA_K2O, area_ha),
    ]
    npk = [p for p in npk if p.kg_ha > 0]
    return simple, npk


def calc_fertilizer(
    crop: str,
    expected_yield_t_ha: Optional[float] = None,
    area_ha: float = 1.0,
    soil_category: str = "srednia",
    nmin_kg_ha: Optional[float] = None,
    forecrop: str = "zboza",
    p_class: str = "srednia",
    k_class: str = "srednia",
    by_product_removed: bool = False,
    organic_type: str = "brak",
    organic_dose: float = 0.0,
    soil_ph: Optional[float] = None,
) -> FertilizerResult:
    norm = CROPS[crop]
    y = norm.default_yield if expected_yield_t_ha is None else expected_yield_t_ha
    warnings: List[str] = []
    recs: List[str] = []

    if not (norm.yield_range[0] <= y <= norm.yield_range[1]):
        warnings.append(f"Plon {y:g} {norm.yield_unit} jest poza typowym zakresem ({norm.yield_range[0]:g}–{norm.yield_range[1]:g}); "
                        "zalecenia mogą być niewiarygodne.")

    # --- azot ---
    nmin_default = nmin_kg_ha is None
    nmin = DEFAULT_NMIN[soil_category] if nmin_default else nmin_kg_ha
    if nmin_default:
        warnings.append(f"Przyjęto domyślne Nmin {nmin:g} kg N/ha dla kategorii gleby – zbadaj azot mineralny wiosną w OSChR.")
    o_label, o_unit, o_n, o_p, o_k, o_eq = ORGANIC[organic_type]
    organic_n_total = o_n * organic_dose
    organic_n_credit = organic_n_total * o_eq
    forecrop_credit = FORECROPS[forecrop][1]
    requirement = norm.n_per_t * y
    n_raw = requirement - nmin - norm.mineralization - forecrop_credit - organic_n_credit
    lo, hi = norm.n_range
    n = n_raw
    if n_raw > hi:
        n = hi
        warnings.append(f"Obliczona dawka N ({n_raw:.0f} kg/ha) przekracza typowy zakres; ograniczono do {hi} kg N/ha.")
    elif n_raw < lo:
        if organic_n_credit + forecrop_credit > 0 or nmin > DEFAULT_NMIN[soil_category]:
            # niska dawka uzasadniona zasobami gleby / nawozem naturalnym – nie podnosimy
            n = max(0.0, n_raw)
            warnings.append(f"Dawka N ({n:.0f} kg/ha) poniżej typowego zakresu {lo}–{hi} kg/ha dzięki zasobom gleby, przedplonowi lub nawozom naturalnym.")
        else:
            n = lo
            warnings.append(f"Obliczona dawka N ({n_raw:.0f} kg/ha) poniżej typowego zakresu; przyjęto dolną granicę {lo} kg N/ha.")

    if organic_n_total > MANURE_N_CAP_KG_HA:
        warnings.append(f"Nawóz naturalny wnosi {organic_n_total:.0f} kg N/ha – program azotanowy dopuszcza maks. "
                        f"{MANURE_N_CAP_KG_HA:.0f} kg N/ha rocznie z nawozów naturalnych. Zmniejsz dawkę.")
    elif organic_n_total > 0:
        recs.append(f"Nawóz naturalny: {organic_n_total:.0f} kg N/ha ogółem (limit programu azotanowego: "
                    f"{MANURE_N_CAP_KG_HA:.0f} kg N/ha/rok), w 1. roku działa ok. {organic_n_credit:.0f} kg N/ha.")

    splits = []
    if n > 0:
        if n <= 60:
            splits = [{"term": norm.splits[0][0], "share_pct": 100, "n_kg_ha": round(n)}]
        else:
            splits = [{"term": t, "share_pct": round(s * 100), "n_kg_ha": round(n * s)} for t, s in norm.splits]
    max_single = max((s["n_kg_ha"] for s in splits), default=0)
    if soil_category in ("bardzo_lekka", "lekka") and max_single > 80:
        warnings.append("Na glebach lekkich pojedyncza dawka N nie powinna przekraczać ok. 60–80 kg N/ha (ryzyko wymycia) – podziel ją na więcej części.")

    # --- fosfor i potas ---
    p_label, p_mult = ABUNDANCE_CLASSES[p_class]
    k_label, k_mult = ABUNDANCE_CLASSES[k_class]
    with_by = by_product_removed and norm.by_product is not None
    by_share = 1.0 if with_by else BY_PRODUCT_LEFT_SHARE
    p_removal = y * (norm.p_main + norm.p_by * by_share)
    k_removal = y * (norm.k_main + norm.k_by * by_share)
    organic_p = o_p * organic_dose * ORGANIC_P_EFF
    organic_k = o_k * organic_dose * ORGANIC_K_EFF
    p2o5 = max(0.0, p_removal * p_mult - organic_p)
    k2o = max(0.0, k_removal * k_mult - organic_k)
    if p_class == "bardzo_wysoka" or k_class == "bardzo_wysoka":
        recs.append("Przy bardzo wysokiej zasobności gleby nawożenie danym składnikiem można pominąć w tym roku; powtórz badanie gleby za 4 lata.")
    if p_class in ("bardzo_niska", "niska") or k_class in ("bardzo_niska", "niska"):
        recs.append("Niska zasobność: nawozy fosforowe i potasowe stosuj przed siewem (jesienią lub wczesną wiosną) i wymieszaj z glebą.")

    if soil_ph is not None and soil_ph < 5.5:
        warnings.append(f"pH gleby {soil_ph:g} – gleba kwaśna, składniki są słabo wykorzystywane. Rozważ wapnowanie (zalecenie OSChR).")

    if crop in ("burak_cukrowy", "ziemniak") and organic_type == "brak":
        recs.append("Rośliny okopowe dobrze reagują na obornik stosowany jesienią pod orkę.")
    if n > 0 and splits and len(splits) > 1:
        recs.append("Azot stosuj w dawkach dzielonych – zmniejsza to straty i ryzyko wymycia.")

    simple, npk = fertilizer_products(n, p2o5, k2o, area_ha)

    assumptions = [
        DISCLAIMER,
        "Źródła: typowe wartości pobrania i dawek wg IUNG-PIB („Zalecenia nawozowe”, metoda bilansowa), "
        "dane rejestrowe COBORU; zawartość składników w nawozach wg deklaracji producentów.",
        f"N = {norm.n_per_t:g} kg N/t × {y:g} t/ha − Nmin {nmin:g} − mineralizacja {norm.mineralization:g} "
        f"− przedplon {forecrop_credit:g} − nawóz naturalny {organic_n_credit:.0f} = {n_raw:.0f} kg N/ha "
        f"(typowy zakres {lo}–{hi}).",
        f"P₂O₅: wynos {p_removal:.0f} kg/ha × {p_mult:g} (zasobność {p_label.lower()}); "
        f"K₂O: wynos {k_removal:.0f} kg/ha × {k_mult:g} (zasobność {k_label.lower()}).",
        ("Wynos uwzględnia zbiór produktu ubocznego (" + norm.by_product + ")." if with_by else
         f"Produkt uboczny ({norm.by_product}) pozostaje na polu – liczono plon główny + 50% składników produktu ubocznego "
         "(powolne uwalnianie w bieżącym roku)." if norm.by_product else
         "Wynos z całą biomasą (zbiór całych roślin)."),
        "Dawka nawozu = potrzeba składnika ÷ zawartość składnika (np. P₂O₅ ÷ 0,46 dla superfosfatu potrójnego).",
        "Przestrzegaj programu azotanowego (m.in. maks. dawki N dla upraw, terminy stosowania, 170 kg N/ha/rok z nawozów naturalnych).",
    ]

    return FertilizerResult(
        crop=crop,
        crop_label=norm.label,
        area_ha=area_ha,
        expected_yield_t_ha=y,
        n_kg_ha=round(n, 0),
        p2o5_kg_ha=round(p2o5, 0),
        k2o_kg_ha=round(k2o, 0),
        n_total_kg=round(n * area_ha, 0),
        p2o5_total_kg=round(p2o5 * area_ha, 0),
        k2o_total_kg=round(k2o * area_ha, 0),
        n_balance={
            "requirement": round(requirement, 0),
            "nmin": round(nmin, 0),
            "mineralization": norm.mineralization,
            "forecrop": forecrop_credit,
            "organic": round(organic_n_credit, 0),
            "raw": round(n_raw, 0),
        },
        n_splits=splits,
        products_simple=simple,
        products_npk=npk,
        organic_n_kg_ha=round(organic_n_total, 0),
        recommendations=recs,
        assumptions=assumptions,
        warnings=warnings,
    )


def fertilizer_meta() -> dict:
    """Słowniki dla formularza (etykiety, zakresy, wartości domyślne)."""
    return {
        "crops": {
            k: {"label": v.label, "yield_unit": v.yield_unit, "yield_range": list(v.yield_range),
                "default_yield": v.default_yield, "n_range": list(v.n_range), "has_by_product": v.by_product is not None,
                "by_product": v.by_product}
            for k, v in CROPS.items()
        },
        "soil_categories": SOIL_CATEGORIES,
        "default_nmin": DEFAULT_NMIN,
        "abundance_classes": {k: v[0] for k, v in ABUNDANCE_CLASSES.items()},
        "forecrops": {k: v[0] for k, v in FORECROPS.items()},
        "organic": {k: {"label": v[0], "unit": v[1]} for k, v in ORGANIC.items()},
        "manure_n_cap_kg_ha": MANURE_N_CAP_KG_HA,
        "disclaimer": DISCLAIMER,
    }

"""Dane do kalkulatora zysku i optymalnego planu upraw.

Dla każdej uprawy z katalogu:
* plon [dt/ha] – średnia z ostatnich 5 lat dla województwa działek (GUS BDL, plony z 1 ha; gdy brak – Polska),
* cena [zł/dt] – ostatnia roczna cena skupu GUS (województwo, a gdy brak – Polska); dla roślin bobowatych GUS nie podaje
  ceny skupu – wtedy cena z kalkulacji ODR (oznaczona),
* koszty [zł/ha] – koszty bezpośrednie i usługi mechaniczne z kalkulacji Pomorskiego ODR 2025 (wariant średniointensywny,
  src/data/odr_costs.json).
Wynik działki = plon × cena − koszty bezpośrednie − usługi (bez dopłat i kosztów ogólnych). Brak danych → uprawa bez
wyniku (nigdy nie zgadujemy).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.calculators.profit import CropEcon, ParcelIn, optimal_plan, parcel_profit
from src.data import gus
from src.data.common import SourceError, friendly, normalize_name, provenance

COSTS_FILE = Path(__file__).with_name("odr_costs.json")
YIELD_YEARS = 5

# klucz → (nazwa, nazwa do plonów GUS, nazwa do cen GUS, grupa, bobowata)
CATALOG: Dict[str, Tuple[str, str, Optional[str], str, bool]] = {
    "pszenica_ozima": ("Pszenica ozima", "pszenica ozima", "pszenica", "zboża", False),
    "pszenica_jara": ("Pszenica jara", "pszenica jara", "pszenica", "zboża", False),
    "jeczmien_ozimy": ("Jęczmień ozimy", "jęczmień ozimy", "jęczmień", "zboża", False),
    "jeczmien_jary": ("Jęczmień jary", "jęczmień jary", "jęczmień", "zboża", False),
    "pszenzyto": ("Pszenżyto", "pszenżyto", "pszenżyto", "zboża", False),
    "zyto": ("Żyto", "żyto", "żyto", "zboża", False),
    "owies": ("Owies", "owies", "owies", "zboża", False),
    "kukurydza": ("Kukurydza na ziarno", "kukurydza na ziarno", "kukurydza", "kukurydza", False),
    "rzepak_ozimy": ("Rzepak ozimy", "rzepak ozimy", "rzepak", "oleiste", False),
    "burak_cukrowy": ("Burak cukrowy", "buraki cukrowe", "buraki cukrowe", "okopowe", False),
    "ziemniak": ("Ziemniak", "ziemniaki", "ziemniaki", "okopowe", False),
    "groch": ("Groch siewny", "groch", None, "bobowate", True),
    "bobik": ("Bobik", "bobik", None, "bobowate", True),
    "lubin": ("Łubin wąskolistny", "łubin", None, "bobowate", True),
}

# uprawy wymagające umowy z odbiorcą – w planie tylko, gdy rolnik je zaznaczy
NEEDS_CONTRACT = {"burak_cukrowy": "Burak cukrowy uprawia się na podstawie umowy kontraktacyjnej z cukrownią."}

NON_ARABLE = ("tuz", "trwalyuzytek", "laka", "laki", "pastwisk", "sad", "wieloletn")


def crop_key(name: Optional[str]) -> Optional[str]:
    """Nazwa uprawy z aplikacji → klucz katalogu (None = nieznana / spoza katalogu, 'tuz' = nie grunt orny)."""
    s = normalize_name(name or "")
    if not s:
        return None
    if any(k in s for k in NON_ARABLE):
        return "tuz"
    jara = "jar" in s
    if "pszenzyt" in s:
        return "pszenzyto"
    if "pszenic" in s:
        return "pszenica_jara" if jara else "pszenica_ozima"
    if "jeczmien" in s or "jeczmie" in s:
        return "jeczmien_jary" if jara else "jeczmien_ozimy" if "ozim" in s else "jeczmien_jary"
    if "zyto" in s:
        return "zyto"
    if "owies" in s:
        return "owies"
    if "kukurydz" in s:
        return None if ("zielonk" in s or "kiszonk" in s) else "kukurydza"
    if "rzepak" in s:
        return None if jara else "rzepak_ozimy"
    if "burak" in s:
        return "burak_cukrowy" if "cukrow" in s or s.startswith("burak") else None
    if "ziemniak" in s:
        return "ziemniak"
    if "groch" in s:
        return "groch"
    if "bobik" in s:
        return "bobik"
    if "lubin" in s:
        return "lubin"
    return None


@lru_cache(maxsize=1)
def load_costs() -> Dict[str, Any]:
    with open(COSTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def costs_provenance() -> Dict[str, Any]:
    src = load_costs()["_source"]
    return provenance(src["name"], src["url"], "statystyka", observed_at=f"{src['dated']}T00:00:00Z", note=src["note"])


def _yield_mean(name: str, voiv: str) -> Tuple[Optional[float], str, Optional[Dict[str, Any]]]:
    try:
        y = gus.yields_reference(name, voiv)
    except (SourceError, ValueError) as exc:
        return None, f"plon GUS niedostępny ({friendly(exc)})", None
    if not y.get("available"):
        return None, y.get("note") or "GUS nie podaje plonu", y.get("provenance")
    rows = y["years"][-YIELD_YEARS:]
    vals = [r["voivodeship"] for r in rows if r["voivodeship"] is not None]
    scope = "województwo"
    if not vals:
        vals = [r["poland"] for r in rows if r["poland"] is not None]
        scope = "Polska"
    if not vals:
        return None, "GUS nie zwrócił plonu", y.get("provenance")
    return round(sum(vals) / len(vals), 1), f"GUS, {scope}, średnia {rows[0]['year']}–{rows[-1]['year']}", y.get("provenance")


def catalog(voivodeship: Optional[str], include_services: bool = True) -> Dict[str, Any]:
    """Uprawy z plonem, ceną, kosztami i wynikiem na 1 ha. Rzuca ValueError dla nieznanego województwa.
    include_services=False – rolnik ma własne maszyny: liczymy tylko koszty bezpośrednie (nadwyżka bezpośrednia ODR)."""
    vv = gus.resolve_voivodeship(voivodeship) if voivodeship else None
    if vv is None:
        raise ValueError("Podaj województwo działek (np. „wielkopolskie”).")
    vname = vv[0]
    costs = load_costs()["crops"]
    price_names = sorted({c[2] for c in CATALOG.values() if c[2]})
    prices = gus.prices_reference(price_names, vname)
    by_price = {}
    for r in prices.get("rows", []):
        for c in r.get("crops", []):
            by_price[c] = r
    out: List[Dict[str, Any]] = []
    yield_prov = None
    for key, (name, yname, pname, group, legume) in CATALOG.items():
        c = costs.get(key)
        if not c:
            continue
        y, y_src, yp = _yield_mean(yname, vname)
        yield_prov = yield_prov or yp
        price, p_src = None, ""
        pr = by_price.get(pname) if pname else None
        if pr and pr.get("available"):
            price = pr["price"]
            p_src = f"GUS, cena skupu {pr['year']}, {'woj. ' + vname if pr.get('scope') == 'województwo' else 'Polska'}"
        elif pname is None:
            price = round(c["odr_price_zl_t"] / 10, 2)
            p_src = "ODR 2025 (GUS nie podaje ceny skupu roślin bobowatych)"
        else:
            p_src = (pr or {}).get("note") or "brak ceny GUS"
        cost = round(c["direct_cost"] + (c["services"] if include_services else 0.0), 2)
        e = CropEcon(key, name, y, price, cost, group, legume)
        out.append({
            "key": key, "name": name, "group": group, "legume": legume,
            "yield_dt_ha": y, "yield_source": y_src, "price_zl_dt": price, "price_source": p_src,
            "direct_cost_zl_ha": c["direct_cost"], "services_zl_ha": c["services"], "cost_zl_ha": cost,
            "cost_parts": {"materiał siewny": c["seed"], "nawozy": c["fertilizers"], "środki ochrony roślin": c["protection"]},
            "cost_source": f"ODR 2025, str. {c['page']}", "contract": NEEDS_CONTRACT.get(key),
            "margin_zl_ha": None if e.margin_ha() is None else round(e.margin_ha(), 0),
        })
    return {"voivodeship": vname, "crops": out, "include_services": include_services,
            "provenance": {"costs": costs_provenance(), "prices": prices.get("provenance"), "yields": yield_prov}}


def _econ(row: Dict[str, Any]) -> CropEcon:
    return CropEcon(row["key"], row["name"], row["yield_dt_ha"], row["price_zl_dt"], row["cost_zl_ha"], row["group"], row["legume"])


def profit(parcels: List[Dict[str, Any]], voivodeship: Optional[str], include_services: bool = True) -> Dict[str, Any]:
    """Kalkulator zysku: działki z uprawą (i opcjonalnie własnym plonem, ceną, kosztami) → wynik każdej i suma."""
    cat = catalog(voivodeship, include_services)
    by_key = {r["key"]: r for r in cat["crops"]}
    rows, total, missing_n = [], 0.0, 0
    for p in parcels:
        key = p.get("crop_key") or crop_key(p.get("crop"))
        ref = by_key.get(key or "")
        y = p.get("yield_dt_ha") if p.get("yield_dt_ha") is not None else (ref or {}).get("yield_dt_ha")
        pr = p.get("price_zl_dt") if p.get("price_zl_dt") is not None else (ref or {}).get("price_zl_dt")
        cost = p.get("cost_zl_ha") if p.get("cost_zl_ha") is not None else (ref or {}).get("cost_zl_ha")
        row = {"id": p.get("id"), "area_ha": p["area_ha"], "crop": p.get("crop"), "crop_key": key,
               "yield_dt_ha": y, "price_zl_dt": pr, "cost_zl_ha": cost}
        if key == "tuz":
            row["note"] = "Trwały użytek zielony – poza kalkulatorem (koszty ODR są dla upraw polowych)."
        if None in (y, pr, cost):
            missing_n += 1
            row.setdefault("note", "Brak danych dla tej uprawy – wpisz plon, cenę i koszty." if ref is None else "Brak plonu albo ceny w GUS – wpisz je.")
        else:
            row.update(parcel_profit(p["area_ha"], y, pr, cost))
            total += row["margin_zl"]
        rows.append(row)
    return {"voivodeship": cat["voivodeship"], "rows": rows, "total_margin_zl": round(total, 0), "incomplete": missing_n,
            "include_services": include_services,
            "catalog": cat["crops"], "provenance": cat["provenance"]}


def plan(parcels: List[Dict[str, Any]], voivodeship: Optional[str], include_services: bool = True,
         allowed: Optional[List[str]] = None) -> Dict[str, Any]:
    """Optymalna konfiguracja upraw na następny sezon (zmianowanie + GAEC 7 + największy wynik).
    allowed – uprawy, które rolnik bierze pod uwagę (domyślnie wszystkie bez tych, które wymagają umowy)."""
    cat = catalog(voivodeship, include_services)
    allow = set(allowed) if allowed else {k for k in CATALOG if k not in NEEDS_CONTRACT}
    names = {r["key"]: r["name"] for r in cat["crops"]}
    ins = []
    for p in parcels:
        key = crop_key(p.get("crop"))
        ins.append(ParcelIn(str(p.get("id")), float(p["area_ha"]), key, arable=key != "tuz"))
    res = optimal_plan(ins, [_econ(r) for r in cat["crops"] if r["key"] in allow])
    rows = []
    for r, p in zip(res.rows, parcels):
        rows.append({**r, "id": p.get("id"), "crop": p.get("crop"), "plan_name": names.get(r["plan"]) if not r["kept"] else p.get("crop")})
    return {"voivodeship": cat["voivodeship"], "rows": rows, "total_margin_zl": res.total_margin_zl,
            "current_margin_zl": res.current_margin_zl, "allowed": sorted(allow), "include_services": include_services, "checks": res.checks, "feasible": res.feasible, "notes": res.notes,
            "catalog": cat["crops"], "provenance": cat["provenance"]}

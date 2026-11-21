"""Plony z 1 ha wg GUS – Bank Danych Lokalnych (BDL), API https://bdl.stat.gov.pl/api/v1 (bez klucza).

Zmienne „plony z 1 ha, ogółem” [dt/ha] z tematów P1423 (zboża, ziemniaki, buraki cukrowe, rzepak, łąki)
i P2138 (kukurydza, soja, słonecznik, strączkowe) – sprawdzone w API 15.11.2026:
  4332 pszenica, 4338 żyto, 4344 jęczmień, 4350 owies, 4356 pszenżyto, 4362 mieszanki zbożowe, 4368 ziemniaki,
  4374 buraki cukrowe, 4380 rzepak i rzepik, 4386 łąki trwałe, 4392 pastwiska trwałe,
  47752 kukurydza na ziarno, 47749 kukurydza na zielonkę, 47769 soja, 47770 ziarno słonecznika, 47740 rośliny strączkowe.
GUS BDL nie rozdziela plonów pszenicy i jęczmienia na formy ozime i jare – pokazujemy plon gatunku łącznie.

Dla każdej zmiennej dwa zapytania (województwa: unit-level=2, Polska: unit-level=0), wynik w cache na 30 dni
(dane roczne, aktualizowane raz w roku). Limit anonimowy API to kilka tysięcy zapytań na tydzień – w praktyce
serwer wykonuje kilkanaście zapytań miesięcznie.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from src.core.cache import TTLCache
from src.data.common import (
    SOURCE_GUS_BDL,
    SourceError,
    friendly,
    http_get_json,
    iso_utc,
    missing,
    normalize_name,
    provenance,
    record_error,
    record_ok,
    to_float,
)
from src.data.imgw_stations import VOIVODESHIP_TERYT

log = logging.getLogger(__name__)

BDL_API = "https://bdl.stat.gov.pl/api/v1"
BDL_PAGE = "https://bdl.stat.gov.pl/bdl/dane/podgrup/temat"
GUS_TTL_S = 30 * 24 * 3600
YEARS = 10
_gus_cache = TTLCache("gus_bdl", max_entries=200, default_ttl=GUS_TTL_S)
_GUS_LOCK = threading.Lock()   # zapytania do BDL po kolei (limit anonimowy)

GUS_CROPS: Dict[int, str] = {
    4332: "pszenica", 4338: "żyto", 4344: "jęczmień", 4350: "owies", 4356: "pszenżyto", 4362: "mieszanki zbożowe",
    4368: "ziemniaki", 4374: "buraki cukrowe", 4380: "rzepak i rzepik", 4386: "łąki trwałe (siano)",
    4392: "pastwiska trwałe", 47752: "kukurydza na ziarno", 47749: "kukurydza na zielonkę", 47769: "soja",
    47770: "ziarno słonecznika", 47740: "rośliny strączkowe",
}

# (fragmenty nazwy po normalizacji, zmienna, uwaga) – kolejność ma znaczenie
_RULES: List[Tuple[Tuple[str, ...], int, str]] = [
    (("pszenzyt",), 4356, ""),
    (("pszenic", "orkisz"), 4332, "GUS BDL publikuje plon pszenicy łącznie (ozima + jara); nie ma osobnej serii dla formy ozimej i jarej."),
    (("jeczmien", "jeczmie"), 4344, "GUS BDL publikuje plon jęczmienia łącznie (ozimy + jary); nie ma osobnej serii dla formy ozimej i jarej."),
    (("zyto",), 4338, ""),
    (("owies",), 4350, ""),
    (("mieszank",), 4362, ""),
    (("rzepak", "rzepik"), 4380, "GUS BDL publikuje plon rzepaku i rzepiku łącznie (formy ozime i jare)."),
    (("kukurydzanazielonk", "kukurydzanakiszonk", "kukurydzakiszonkowa"), 47749, ""),
    (("kukurydz",), 47752, ""),
    (("burakicukrow", "burakcukrow"), 4374, ""),
    (("ziemniak",), 4368, ""),
    (("trwalyuzytekzielony", "tuz", "laka", "laki", "lakow"), 4386, "Dla użytków zielonych GUS podaje plon siana z łąk trwałych."),
    (("pastwisk",), 4392, ""),
    (("soja",), 47769, ""),
    (("slonecznik",), 47770, ""),
    (("straczkow", "groch", "bobik", "lubin", "peluszk", "wyka"), 47740, "GUS podaje plon roślin strączkowych jako grupy."),
]


def map_crop(name: Optional[str]) -> Tuple[Optional[int], str]:
    """Nazwa uprawy z aplikacji → (zmienna GUS BDL | None, uwaga)."""
    s = normalize_name(name or "")
    if not s:
        return None, "Nie podano uprawy."
    for keys, var, note in _RULES:
        if any(normalize_name(k) in s for k in keys):
            if var == 47752 and "ziarn" not in s:
                note = "Przyjęto kukurydzę na ziarno (dla kukurydzy na kiszonkę wpisz „kukurydza na zielonkę”)."
            return var, note
    supported = ", ".join(sorted(set(GUS_CROPS.values())))
    return None, (f"GUS BDL nie publikuje plonów z 1 ha dla uprawy „{name}” na poziomie województw. "
                  f"Dostępne uprawy: {supported}.")


def resolve_voivodeship(value: Optional[str]) -> Optional[Tuple[str, str]]:
    """Nazwa (z/bez polskich znaków, dowolna wielkość liter) albo kod TERYT (2 cyfry) → (nazwa, TERYT)."""
    v = (value or "").strip()
    if not v:
        return None
    if v.isdigit():
        v2 = v[:2].zfill(2)
        for name, t in VOIVODESHIP_TERYT.items():
            if t == v2:
                return name, t
        return None
    nv = normalize_name(v.replace("województwo", "").replace("woj.", ""))
    for name, t in VOIVODESHIP_TERYT.items():
        if normalize_name(name) == nv:
            return name, t
    return None


def _values(rec: Dict[str, Any]) -> Dict[int, float]:
    out = {}
    for v in rec.get("values") or []:
        try:
            y = int(v.get("year"))
        except (TypeError, ValueError):
            continue
        f = to_float(v.get("val"))
        if f is not None:
            out[y] = f
    return out


def fetch_variable(var_id: int) -> Dict[str, Any]:
    """{"voivodeships": {TERYT: {rok: plon}}, "poland": {rok: plon}, "last_update", "fetched_at"} (cache 30 dni)."""
    key = str(int(var_id))
    cached = _gus_cache.get(key)
    if cached:
        return cached
    with _GUS_LOCK:
        cached = _gus_cache.get(key)
        if cached:
            return cached
        try:
            woj = http_get_json(f"{BDL_API}/data/by-variable/{key}",
                                params={"unit-level": 2, "format": "json", "page-size": 100, "lang": "pl"}, timeout=12.0, retries=1)
            pl = http_get_json(f"{BDL_API}/data/by-variable/{key}",
                               params={"unit-level": 0, "format": "json", "lang": "pl"}, timeout=12.0, retries=1)
            if not isinstance(woj, dict) or not isinstance(woj.get("results"), list) or not isinstance(pl, dict) \
                    or not isinstance(pl.get("results"), list) or not pl["results"]:
                raise SourceError("GUS BDL: nieoczekiwany format odpowiedzi")
        except SourceError as exc:
            record_error(SOURCE_GUS_BDL, str(exc))
            raise
        voiv: Dict[str, Dict[int, float]] = {}
        for rec in woj["results"]:
            uid = str(rec.get("id") or "")
            if len(uid) >= 4 and uid[2:4].isdigit():
                voiv[uid[2:4]] = _values(rec)
        if not voiv:
            record_error(SOURCE_GUS_BDL, "brak danych wojewódzkich")
            raise SourceError("GUS BDL: brak danych dla województw")
        res = {"voivodeships": voiv, "poland": _values(pl["results"][0]),
               "last_update": woj.get("lastUpdate"), "fetched_at": iso_utc()}
        record_ok(SOURCE_GUS_BDL, f"zmienna {key}")
        # klucze int → str przy zapisie JSON; ujednolicamy od razu
        res = {**res, "voivodeships": {t: {str(y): v for y, v in d.items()} for t, d in voiv.items()},
               "poland": {str(y): v for y, v in res["poland"].items()}}
        _gus_cache.set(key, res, GUS_TTL_S)
        return res


def yields_reference(crop: Optional[str], voivodeship: Optional[str]) -> Dict[str, Any]:
    """Odpowiedź GET /api/reference/yields. Rzuca ValueError dla nieznanego województwa, SourceError przy awarii GUS."""
    vv = resolve_voivodeship(voivodeship)
    if vv is None:
        raise ValueError("Nieznane województwo – podaj nazwę (np. „wielkopolskie”) albo dwucyfrowy kod TERYT.")
    vname, teryt = vv
    var, note = map_crop(crop)
    base: Dict[str, Any] = {"crop": crop, "gus_crop": GUS_CROPS.get(var) if var else None, "variable_id": var,
                            "unit": "dt/ha", "voivodeship": {"name": vname, "teryt": teryt}, "note": note}
    if var is None:
        return {**base, "available": False, "years": [], "provenance": provenance(
            "GUS – Bank Danych Lokalnych", BDL_PAGE, "brak", note=note)}
    data = fetch_variable(var)
    woj = data["voivodeships"].get(teryt) or {}
    pl = data["poland"]
    years = sorted({int(y) for y in list(woj) + list(pl)})[-YEARS:]
    rows = [{"year": y, "voivodeship": woj.get(str(y)), "poland": pl.get(str(y))} for y in years]

    def mean(key: str) -> Optional[float]:
        vals = [r[key] for r in rows if r[key] is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    last = rows[-1]["year"] if rows else None
    return {**base, "available": bool(rows), "years": rows,
            "voivodeship_mean_dt_ha": mean("voivodeship"), "poland_mean_dt_ha": mean("poland"),
            "last_update": data.get("last_update"),
            "provenance": provenance(
                f"GUS – Bank Danych Lokalnych, zmienna {var}: {GUS_CROPS[var]} – plony z 1 ha (ogółem)", BDL_PAGE, "statystyka",
                observed_at=f"{last}-12-31T00:00:00Z" if last else None, fetched_at=data.get("fetched_at"),
                note=(f"Plon przeciętny z 1 ha [dt/ha] dla województwa {vname} i Polski, lata {years[0]}–{years[-1]}, "
                      "wszystkie gospodarstwa (ogółem). Statystyka publiczna GUS (dane BDL, aktualizacja "
                      f"{str(data.get('last_update') or '')[:10] or 'b.d.'}); 1 dt = 100 kg. {note}".strip()
                      if rows else "GUS BDL nie zwrócił wartości dla tej uprawy."),
            )}


# ------------------------------ Ceny skupu produktów rolnych ------------------------------
# Temat P1458 „Przeciętne ceny skupu produktów rolnych” (dane roczne, zł za 1 dt), województwa i Polska –
# sprawdzone w API 16.11.2026 (ostatni rok z danymi: 2025). Kukurydzy nie ma w P1458 – cena roczna
# kukurydzy jest w temacie P2967 (ceny skupu, dane miesięczne), zmienna „rok” 284047.
PRICE_VARS: Dict[int, Tuple[str, str]] = {
    4859: ("pszenica", "zł/dt"), 4860: ("żyto", "zł/dt"), 4861: ("jęczmień", "zł/dt"),
    4862: ("owies i mieszanki zbożowe", "zł/dt"), 4863: ("pszenżyto", "zł/dt"), 284047: ("kukurydza (ziarno)", "zł/dt"),
    4868: ("rzepak i rzepik", "zł/dt"), 4864: ("ziemniaki", "zł/dt"), 4867: ("buraki cukrowe", "zł/dt"),
}
PRICE_PAGE = "https://bdl.stat.gov.pl/bdl/dane/podgrup/temat/K15/G186/P1458"
MAX_PRICE_CROPS = 12
PRICE_HISTORY_YEARS = 5      # tyle zmian rok do roku pokazuje tabela cen

_PRICE_RULES: List[Tuple[Tuple[str, ...], Optional[int]]] = [
    (("pszenzyt",), 4863),
    (("pszenic", "orkisz"), 4859),
    (("jeczmien", "jeczmie"), 4861),
    (("zyto",), 4860),
    (("owies", "mieszank"), 4862),
    (("rzepak", "rzepik"), 4868),
    (("kukurydzanazielonk", "kukurydzanakiszonk", "kukurydzakiszonkowa"), None),
    (("kukurydz",), 284047),
    (("burakicukrow", "burakcukrow"), 4867),
    (("ziemniak",), 4864),
]


def map_price(name: Optional[str]) -> Optional[int]:
    """Nazwa uprawy z aplikacji → zmienna GUS BDL z ceną skupu (None = GUS nie podaje ceny skupu)."""
    s = normalize_name(name or "")
    if not s:
        return None
    for keys, var in _PRICE_RULES:
        if any(normalize_name(k) in s for k in keys):
            return var
    return None


def _change(cur: Optional[float], prev: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    if cur is None or prev is None:
        return None, None
    diff = round(cur - prev, 2)
    return diff, (round(diff / prev * 100, 1) if prev else None)


def prices_reference(crops: List[str], voivodeship: Optional[str]) -> Dict[str, Any]:
    """Odpowiedź GET /api/reference/prices: ostatnia roczna cena skupu każdej uprawy (województwo, a gdy brak – Polska)
    i zmiana do roku poprzedniego. Uprawy bez ceny w GUS zwracane z available=False. Nigdy nie rzuca dla awarii GUS –
    wiersz dostaje wtedy available=False i uwagę."""
    vv = resolve_voivodeship(voivodeship) if voivodeship else None
    vname, teryt = vv if vv else (None, None)
    rows: List[Dict[str, Any]] = []
    seen: Dict[Optional[int], Dict[str, Any]] = {}
    years_used: List[int] = []
    updated: List[str] = []
    fetched: List[str] = []
    errors = 0
    for crop in crops[:MAX_PRICE_CROPS]:
        var = map_price(crop)
        if var is not None and var in seen:      # np. „Pszenica ozima” i „Pszenica jara” → jedna cena pszenicy
            seen[var]["crops"].append(crop)
            continue
        row: Dict[str, Any] = {"crops": [crop], "variable_id": var, "product": PRICE_VARS[var][0] if var else None,
                               "unit": PRICE_VARS[var][1] if var else None, "available": False}
        if var is None:
            row["note"] = "GUS nie publikuje ceny skupu tej uprawy."
            rows.append(row)
            continue
        seen[var] = row
        rows.append(row)
        try:
            data = fetch_variable(var)
        except SourceError as exc:
            errors += 1
            row["note"] = f"GUS BDL nie odpowiedział ({friendly(exc)})."
            continue
        series = (data["voivodeships"].get(teryt) if teryt else None) or {}
        scope = "województwo"
        if not series:
            series, scope = data["poland"], "Polska"
        ys = sorted(int(y) for y, v in series.items() if v is not None)
        if not ys:
            row["note"] = "GUS BDL nie zwrócił wartości."
            continue
        last = ys[-1]
        cur, prev = series.get(str(last)), series.get(str(last - 1))
        diff, pct = _change(cur, prev)
        pl_cur, pl_prev = data["poland"].get(str(last)), data["poland"].get(str(last - 1))
        pl_diff, pl_pct = _change(pl_cur, pl_prev)
        # zmiany rok do roku za ostatnie PRICE_HISTORY_YEARS lat i cena sprzed tylu lat (np. 2025/2024 … 2021/2020, cena 2020)
        changes = []
        for y in range(last, last - PRICE_HISTORY_YEARS, -1):
            c_diff, c_pct = _change(series.get(str(y)), series.get(str(y - 1)))
            changes.append({"year": y, "prev_year": y - 1, "change": c_diff, "change_pct": c_pct})
        base_year = last - PRICE_HISTORY_YEARS
        row.update(available=True, scope=scope, year=last, price=cur, prev_year=last - 1, prev_price=prev,
                   change=diff, change_pct=pct, poland_price=pl_cur, poland_change_pct=pl_pct,
                   changes=changes, base_year=base_year, base_price=series.get(str(base_year)))
        years_used.append(last)
        if data.get("last_update"):
            updated.append(str(data["last_update"])[:10])
        if data.get("fetched_at"):
            fetched.append(data["fetched_at"])
    ok = [r for r in rows if r["available"]]
    vars_txt = ", ".join(str(r["variable_id"]) for r in ok)
    if ok:
        prov = provenance(
            "GUS – Bank Danych Lokalnych: przeciętne ceny skupu produktów rolnych (P1458; kukurydza – P2967)", PRICE_PAGE, "statystyka",
            observed_at=f"{max(years_used)}-12-31T00:00:00Z", fetched_at=max(fetched) if fetched else None,
            note=(f"Średnia roczna cena skupu (zł za 1 dt = 100 kg) dla województwa {vname or '–'} "
                  f"(gdy brak – dla Polski), zmiany rok do roku za {PRICE_HISTORY_YEARS} lat i cena sprzed {PRICE_HISTORY_YEARS} lat. Zmienne BDL: {vars_txt}. Ceny skupu to ceny płacone "
                  "rolnikom przez podmioty skupujące – cena przy sprzedaży z własnego gospodarstwa może się różnić. "
                  f"Aktualizacja danych GUS: {max(updated) if updated else 'b.d.'}."))
    elif errors:
        prov = missing("GUS – Bank Danych Lokalnych", PRICE_PAGE, "GUS BDL nie odpowiedział – brak cen skupu.")
    else:
        prov = provenance("GUS – Bank Danych Lokalnych", PRICE_PAGE, "brak", note="GUS nie publikuje cen skupu dla tych upraw.")
    return {"voivodeship": {"name": vname, "teryt": teryt} if vv else None, "rows": rows, "provenance": prov}

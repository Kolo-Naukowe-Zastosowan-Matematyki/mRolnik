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
    http_get_json,
    iso_utc,
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

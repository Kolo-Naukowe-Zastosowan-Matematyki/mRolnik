"""Linie referencyjne z danych państwowych do wykresów (pogoda, woda, gleba).

1. Normy klimatyczne IMGW-PIB 1991–2020 (opad miesięczny, średnia Tmax, średnia Tmin) dla najbliższej stacji
   synoptycznej – policzone jednorazowo skryptem scripts/build_imgw_normals.py z publicznego archiwum IMGW-PIB
   i dołączone jako src/data/imgw_normals.json (serwer niczego nie pobiera w trakcie pracy).
2. Stany charakterystyczne wodowskazów IMGW-PIB (ostrzegawczy, alarmowy, niski, wysoki) i przebieg stanu wody
   z 7 dni – serwis hydro-back.imgw.pl (ten sam, z którego korzysta hydro.imgw.pl), cache 30 min.
3. Pojemność polowa i punkt trwałego więdnięcia – wartości TABELARYCZNE (FAO-56, tab. 19) dla klasy uziarnienia,
   a nie pomiar ani dane IUNG-PIB; oznaczane rodzajem proweniencji "tabelaryczna".
"""
from __future__ import annotations

import calendar
import json
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.cache import TTLCache
from src.data.common import (
    SOURCE_IMGW_HYDRO_BACK,
    SourceError,
    haversine_km,
    http_get_json,
    iso_utc,
    missing,
    normalize_name,
    provenance,
    record_error,
    record_ok,
    to_float,
)
from src.data.imgw_stations import SYNOP_STATIONS

log = logging.getLogger(__name__)

NORMALS_PATH = Path(__file__).with_name("imgw_normals.json")
NORMALS_MAX_KM = 150.0
IMGW_ATTRIBUTION = "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy"

HYDRO_BACK_URL = "https://hydro-back.imgw.pl/station/hydro/status/data"
HYDRO_PAGE_URL = "https://hydro.imgw.pl/#/station/hydro/{id}"
HYDRO_BACK_TTL_S = 30 * 60
HYDRO_HOURS = 168
_hydro_back_cache = TTLCache("imgw_hydro_back", max_entries=3000, default_ttl=HYDRO_BACK_TTL_S)

FAO56_URL = "https://www.fao.org/3/x0490e/x0490e0a.htm"


# --------------------------------------------------------------------------------------
# 1. Normy klimatyczne IMGW-PIB 1991–2020
# --------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_normals() -> Dict[str, Any]:
    """Zawartość imgw_normals.json (pusta struktura, gdy pliku brak – wtedy referencja ma kind "brak")."""
    try:
        return json.loads(NORMALS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Brak pliku norm IMGW (%s): %s", NORMALS_PATH, exc)
        return {"stations": []}


def nearest_normals(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Normy najbliższej (≤150 km, bez stacji wysokogórskich) stacji synoptycznej, dla której je policzono."""
    data = load_normals()
    by_wmo = {s.get("wmo_id"): s for s in data.get("stations", [])}
    by_name = {normalize_name(s.get("name", "")): s for s in data.get("stations", [])}
    cands = sorted(((haversine_km(lat, lon, s.lat, s.lon), s) for s in SYNOP_STATIONS if not s.mountain), key=lambda t: t[0])
    for dist, st in cands:
        if dist > NORMALS_MAX_KM:
            break
        rec = by_wmo.get(st.id) or by_name.get(normalize_name(st.name))
        if rec and any(v is not None for v in rec.get("precip_mm", [])):
            return {"station": rec["name"], "station_id": rec["id"], "distance_km": round(dist, 1),
                    "precip_mm": rec["precip_mm"], "tmax_c": rec["tmax_c"], "tmin_c": rec["tmin_c"],
                    "period": data.get("period", "1991–2020")}
    return None


def normals_provenance(n: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = load_normals()
    url = data.get("source_url") or "https://danepubliczne.imgw.pl/"
    if not n:
        return missing("IMGW-PIB – normy klimatyczne 1991–2020", url,
                       "Brak stacji synoptycznej IMGW-PIB z kompletnymi danymi 1991–2020 w promieniu 150 km.")
    return provenance(
        f"IMGW-PIB – norma klimatyczna {n['period']}, stacja {n['station']}", url, "statystyka",
        fetched_at=data.get("built_at"),
        note=(f"Średnia wieloletnia {n['period']} ze stacji synoptycznej {n['station']} ({n['distance_km']:.0f} km od działki): "
              "miesięczna suma opadów, średnia temperatura maksymalna i minimalna. Policzona przez mRolnik z miesięcznych "
              f"danych archiwum IMGW-PIB (≥{data.get('min_years', 24)} lat w każdym miesiącu). Norma dobowa opadu = norma "
              f"miesięczna / liczba dni miesiąca. {IMGW_ATTRIBUTION}."),
    )


def _month_of(day: str) -> Optional[tuple]:
    try:
        d = datetime.strptime(str(day)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return d.year, d.month


def daily_normals(days: List[str], n: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dla każdego dnia: norma Tmax/Tmin miesiąca i dobowa norma opadu (suma miesięczna / liczba dni)."""
    out = []
    for day in days:
        ym = _month_of(day)
        row: Dict[str, Any] = {"date": day, "t_max_normal_c": None, "t_min_normal_c": None, "precip_normal_mm": None}
        if n and ym:
            y, m = ym
            p = n["precip_mm"][m - 1]
            row["t_max_normal_c"] = n["tmax_c"][m - 1]
            row["t_min_normal_c"] = n["tmin_c"][m - 1]
            row["precip_normal_mm"] = None if p is None else round(p / calendar.monthrange(y, m)[1], 2)
        out.append(row)
    return out


def climate_block(lat: float, lon: float, days: List[str]) -> Dict[str, Any]:
    """Blok `reference.climate_normals` do odpowiedzi API: stacja, 12 wartości miesięcznych, wartości dzienne."""
    n = nearest_normals(lat, lon)
    return {
        "station": n["station"] if n else None,
        "station_id": n["station_id"] if n else None,
        "distance_km": n["distance_km"] if n else None,
        "period": (n or {}).get("period", "1991–2020"),
        "monthly": None if not n else {"precip_mm": n["precip_mm"], "tmax_c": n["tmax_c"], "tmin_c": n["tmin_c"]},
        "daily": daily_normals(days, n),
        "provenance": normals_provenance(n),
    }


def cumulative_normal_precip(rows: List[Dict[str, Any]], daily: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Opad narastająco wg normy dla tych samych dni co bilans wodny (do linii przerywanej)."""
    by_day = {d["date"]: d.get("precip_normal_mm") for d in daily}
    cum, out = 0.0, []
    complete = True
    for r in rows:
        v = by_day.get(r["date"])
        if v is None:
            complete = False
        else:
            cum += v
        out.append({"date": r["date"], "precip_normal_mm": v, "cum_precip_normal_mm": round(cum, 1) if complete else None})
    return out


# --------------------------------------------------------------------------------------
# 2. Wodowskazy IMGW-PIB: stany charakterystyczne + 7 dni (hydro-back.imgw.pl)
# --------------------------------------------------------------------------------------
def _threshold(data: Dict[str, Any], key: str) -> Optional[float]:
    vals = data.get(key)
    if isinstance(vals, list) and vals:
        last = vals[-1]
        if isinstance(last, dict):
            return to_float(last.get("value"))
    return None


def parse_hydro_back(data: Any, hours_step: int = 3) -> Dict[str, Any]:
    """Odpowiedź /station/hydro/status/data → progi i seria (co `hours_step` h, ostatni odczyt zawsze)."""
    if not isinstance(data, dict) or not isinstance(data.get("operational"), list):
        raise SourceError("IMGW hydro-back: nieoczekiwany format odpowiedzi")
    pts = []
    for p in data["operational"]:
        if isinstance(p, dict) and to_float(p.get("value")) is not None and p.get("date"):
            pts.append({"t": str(p["date"]), "level_cm": to_float(p["value"]), "state": p.get("state")})
    pts.sort(key=lambda p: p["t"])
    series, last_t = [], None
    for i, p in enumerate(pts):
        try:
            t = datetime.strptime(p["t"][:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            continue
        if last_t is None or (t - last_t).total_seconds() >= hours_step * 3600 - 1 or i == len(pts) - 1:
            series.append(p)
            last_t = t
    return {
        "warning_level_cm": _threshold(data, "warningValues"),
        "alarm_level_cm": _threshold(data, "alarmValues"),
        "low_level_cm": _threshold(data, "lowValues"),
        "high_level_cm": _threshold(data, "highValues"),
        "series_7d": series,
        "state": pts[-1]["state"] if pts else None,
    }


def fetch_hydro_back(station_id: str) -> Dict[str, Any]:
    """Progi i przebieg 7 dni dla wodowskazu (cache 30 min). Rzuca SourceError."""
    sid = "".join(ch for ch in str(station_id) if ch.isdigit())
    if len(sid) != 9:
        raise SourceError("IMGW hydro-back: niepoprawny kod wodowskazu")
    cached = _hydro_back_cache.get(sid)
    if cached:
        return cached
    try:
        res = parse_hydro_back(http_get_json(HYDRO_BACK_URL, params={"id": sid, "hoursInterval": HYDRO_HOURS}, timeout=8.0, retries=1))
    except SourceError as exc:
        record_error(SOURCE_IMGW_HYDRO_BACK, str(exc))
        raise
    res["fetched_at"] = iso_utc()
    record_ok(SOURCE_IMGW_HYDRO_BACK, f"wodowskaz {sid}")
    _hydro_back_cache.set(sid, res, HYDRO_BACK_TTL_S)
    return res


STATE_LABELS = {"low": "strefa stanów niskich", "medium": "strefa stanów średnich", "high": "strefa stanów wysokich",
                "warning": "stan ostrzegawczy przekroczony", "alarm": "stan alarmowy przekroczony"}


def gauge_reference(gauge: Dict[str, Any]) -> Dict[str, Any]:
    """Uzupełnia wodowskaz o progi i serię z hydro-back. Progi z API danych publicznych mają pierwszeństwo,
    hydro-back je uzupełnia (stany niskie/wysokie są tylko tam). Nigdy nie rzuca."""
    out = {"warning_level_cm": gauge.get("warning_level_cm"), "alarm_level_cm": gauge.get("alarm_level_cm"),
           "low_level_cm": None, "high_level_cm": None, "series_7d": [], "state": None, "state_label": None}
    sid = gauge.get("id") or ""
    page = HYDRO_PAGE_URL.format(id=sid) if sid else "https://hydro.imgw.pl/"
    try:
        hb = fetch_hydro_back(sid)
    except SourceError as exc:
        note = ("Progi ostrzegawczy/alarmowy z API danych publicznych IMGW-PIB; przebieg 7 dni i stany niskie/wysokie "
                f"niedostępne (hydro-back.imgw.pl: {exc}).")
        kind = "pomiar" if out["warning_level_cm"] is not None or out["alarm_level_cm"] is not None else "brak"
        out["provenance"] = provenance("IMGW-PIB – stany charakterystyczne wodowskazu", page, kind, note=note) \
            if kind != "brak" else missing("IMGW-PIB – stany charakterystyczne wodowskazu", page, note)
        return out
    for k in ("warning_level_cm", "alarm_level_cm"):
        if out[k] is None:
            out[k] = hb[k]
    out.update(low_level_cm=hb["low_level_cm"], high_level_cm=hb["high_level_cm"], series_7d=hb["series_7d"],
               state=hb["state"], state_label=STATE_LABELS.get(str(hb["state"] or "")))
    last = hb["series_7d"][-1]["t"] if hb["series_7d"] else None
    out["provenance"] = provenance(
        f"IMGW-PIB – stany charakterystyczne i przebieg 7 dni, wodowskaz {gauge.get('name')}", page, "pomiar",
        observed_at=last, fetched_at=hb.get("fetched_at"),
        note=("Stany ostrzegawczy i alarmowy ustala IMGW-PIB dla każdego wodowskazu; granice stanów niskich i wysokich "
              "(strefy niska/średnia/wysoka) – również IMGW-PIB (serwis hydro.imgw.pl). Przebieg stanu wody z 7 dni, "
              f"co 3 godziny, czas w UTC. {IMGW_ATTRIBUTION}."),
    )
    return out


# --------------------------------------------------------------------------------------
# 3. Pojemność polowa / punkt więdnięcia – wartości tabelaryczne
# --------------------------------------------------------------------------------------
def soil_reference_provenance(soil_class_label: str, fc_pct: Optional[float], wp_pct: Optional[float]) -> Dict[str, Any]:
    return provenance(
        "Wartość tabelaryczna – FAO-56 (tab. 19), klasa uziarnienia", FAO56_URL, "tabelaryczna",
        note=(f"Pojemność polowa {fc_pct}% obj. i punkt trwałego więdnięcia {wp_pct}% obj. to typowe wartości dla klasy "
              f"„{soil_class_label}” (środki zakresów z tabeli 19 FAO Irrigation and Drainage Paper 56, 1998), przypisane do "
              "nazwy gleby podanej przez użytkownika. To nie jest pomiar na działce ani dana IUNG-PIB – rzeczywiste wartości "
              "zależą od składu granulometrycznego i próchnicy (oznaczenie w laboratorium, np. stacja chemiczno-rolnicza)."),
    )

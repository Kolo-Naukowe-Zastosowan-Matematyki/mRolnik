"""Klimat pola w liczbach przydatnych rolnikowi (zakładka „Plony”): jak zmienia się sezon od 1991 r. i jak wypada bieżący rok.

Dane: reanaliza ERA5 (Copernicus Climate Change Service / ECMWF) z archiwum Open-Meteo – dobowe Tmax, Tmin, Tśr,
opad i ewapotranspiracja wskaźnikowa ET0 (FAO-56) dla komórki siatki ~25 km z działką, od 1.01.1991 do ok. 5 dni wstecz.
To MODEL z asymilacją pomiarów, nie pomiar stacyjny – dobrze oddaje zmiany z roku na rok i trendy, gorzej lokalne
ekstrema (np. przymrozki w zagłębieniach terenu bywają silniejsze niż w ERA5).

Wskaźniki roczne (lata pełne):
- średnia roczna temperatura, roczna suma opadów,
- okres wegetacyjny: od pierwszego dnia pierwszej serii 5 dni z Tśr > 5°C (od 1 stycznia) do ostatniego dnia przed
  pierwszą serią 5 dni z Tśr ≤ 5°C po 1 lipca (ten sam próg 5°C co w programie azotanowym i w klasyfikacji termicznej),
- ostatni wiosenny przymrozek (ostatni dzień do 30 czerwca z Tmin ≤ 0°C) i pierwszy jesienny (pierwszy od 1 lipca),
- dni upalne: Tmax > 30°C (definicja IMGW-PIB),
- suma stopniodni (GDD) ponad 10°C w IV–IX: Σ max(0, (Tmax+Tmin)/2 − 10) – ciepło dla kukurydzy, słonecznika, soi,
- klimatyczny bilans wodny IV–IX i IV–VII: Σ (opad − ET0) – wskaźnik suszy rolniczej (jak w systemie IUNG-PIB),
Trend: regresja liniowa, podawany na 10 lat; średnia odniesienia: 1991–2020 (okres norm klimatycznych WMO/IMGW-PIB).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.core.cache import TTLCache
from src.data import era5_archive
from src.data.common import SourceError, friendly, missing, provenance, utcnow

log = logging.getLogger(__name__)

ARCHIVE_URL = era5_archive.OPEN_METEO_ARCHIVE_URL
START_YEAR = 1991
NORMAL_PERIOD = (1991, 2020)
CLIMATE_TTL_S = 7 * 24 * 3600
CLIMATE_FALLBACK_TTL_S = 3600
_climate_cache = TTLCache("climate_era5", max_entries=2000, default_ttl=CLIMATE_TTL_S)

SEASON_T = 5.0
GDD_BASE = 10.0
HOT_T = 30.0


def _grid_key(lat: float, lon: float) -> Tuple[float, float]:
    """Siatka 0,1° – ERA5 ma ~0,25°, więc sąsiednie działki dzielą pobranie."""
    return round(lat, 1), round(lon, 1)


def fetch_daily(lat: float, lon: float, analysis_date: Optional[date] = None) -> Dict[str, Any]:
    """Dobowe dane ERA5 od 1991 r. do `analysis_date` (lub końca archiwum). Rzuca SourceError.

    Dane ze wspólnego archiwum `era5_archive` (porcje roczne w cache – z sieci dociągane tylko brakujące dni;
    po wyczerpaniu limitu Open-Meteo zapasowo NASA POWER z ET0 wg Hargreavesa).
    """
    glat, glon = _grid_key(lat, lon)
    end = era5_archive.archive_end(utcnow())
    if analysis_date is not None:
        end = min(end, analysis_date)
    key = f"{glat:.1f},{glon:.1f}:{end.isoformat()}"
    cached = _climate_cache.get(key)
    if cached:
        return cached
    arch = era5_archive.daily_series(glat, glon, date(START_YEAR, 1, 1), end, era5_archive.CLIMATE)
    s = arch["series"]
    if sum(1 for v in s["precip"] if v is not None) < 3650:
        raise SourceError("archiwum: za mało danych dobowych", user_message="archiwum klimatyczne zwróciło za mało danych (< 10 lat)")
    res = {"time": arch["days"], "tmax": s["tmax"], "tmin": s["tmin"], "tmean": s["tmean"],
           "precip": s["precip"], "et0": s["et0"], "end": end.isoformat(),
           "fetched_at": arch.get("fetched_at"), "cell": arch.get("cell"),
           "provider": arch["provider"], "fallback_reason": arch.get("fallback_reason"), "start": arch["start"]}
    era5 = arch["provider"] == era5_archive.PROVIDER_ERA5
    _climate_cache.set(key, res, CLIMATE_TTL_S if era5 else CLIMATE_FALLBACK_TTL_S)
    return res


def _by_year(daily: Dict[str, Any]) -> Dict[int, List[Dict[str, Any]]]:
    out: Dict[int, List[Dict[str, Any]]] = {}
    for i, t in enumerate(daily["time"]):
        dt = date.fromisoformat(t)
        out.setdefault(dt.year, []).append({"date": dt, "tmax": daily["tmax"][i], "tmin": daily["tmin"][i], "tmean": daily["tmean"][i],
                                            "p": daily["precip"][i], "et0": daily["et0"][i]})
    return out


def _season(days: List[Dict[str, Any]]) -> Tuple[Optional[date], Optional[date]]:
    start = end = None
    run = 0
    for i, d in enumerate(days):
        run = run + 1 if (d["tmean"] is not None and d["tmean"] > SEASON_T) else 0
        if run == 5 and start is None:
            start = days[i - 4]["date"]
            break
    if start is None:
        return None, None
    run = 0
    for i, d in enumerate(days):
        if d["date"] < date(d["date"].year, 7, 1):
            continue
        run = run + 1 if (d["tmean"] is not None and d["tmean"] <= SEASON_T) else 0
        if run == 5:
            end = days[i - 4]["date"] - timedelta(days=1)
            break
    if end is None and days[-1]["date"].month == 12 and days[-1]["date"].day == 31:
        end = days[-1]["date"]
    return start, end


def _doy(d: Optional[date]) -> Optional[int]:
    return None if d is None else d.timetuple().tm_yday


def year_metrics(year: int, days: List[Dict[str, Any]]) -> Dict[str, Any]:
    def s(key: str, months: Optional[range] = None) -> Optional[float]:
        v = [d[key] for d in days if d[key] is not None and (months is None or d["date"].month in months)]
        return round(sum(v), 1) if v else None

    tm = [d["tmean"] for d in days if d["tmean"] is not None]
    start, end = _season(days)
    spring = [d for d in days if d["date"].month <= 6 and d["tmin"] is not None and d["tmin"] <= 0]
    autumn = [d for d in days if d["date"].month >= 7 and d["tmin"] is not None and d["tmin"] <= 0]
    gdd = sum(max(0.0, (d["tmax"] + d["tmin"]) / 2 - GDD_BASE) for d in days
              if d["date"].month in range(4, 10) and d["tmax"] is not None and d["tmin"] is not None)
    kbw = [d["p"] - d["et0"] for d in days if d["p"] is not None and d["et0"] is not None]
    kbw_49 = [d["p"] - d["et0"] for d in days if d["date"].month in range(4, 10) and d["p"] is not None and d["et0"] is not None]
    kbw_47 = [d["p"] - d["et0"] for d in days if d["date"].month in range(4, 8) and d["p"] is not None and d["et0"] is not None]
    return {
        "year": year,
        "t_mean_c": round(sum(tm) / len(tm), 2) if tm else None,
        "precip_mm": s("p"),
        "precip_veg_mm": s("p", range(4, 10)),
        "season_start": start.isoformat() if start else None,
        "season_end": end.isoformat() if end else None,
        "season_start_doy": _doy(start), "season_end_doy": _doy(end),
        "season_days": (end - start).days + 1 if start and end else None,
        "last_spring_frost": spring[-1]["date"].isoformat() if spring else None,
        "last_spring_frost_doy": _doy(spring[-1]["date"]) if spring else None,
        "first_autumn_frost": autumn[0]["date"].isoformat() if autumn else None,
        "first_autumn_frost_doy": _doy(autumn[0]["date"]) if autumn else None,
        "hot_days": sum(1 for d in days if d["tmax"] is not None and d["tmax"] > HOT_T),
        "frost_days": sum(1 for d in days if d["tmin"] is not None and d["tmin"] < 0),
        "gdd10_apr_sep": round(gdd),
        "kbw_apr_sep_mm": round(sum(kbw_49), 1) if kbw_49 else None,
        "kbw_apr_jul_mm": round(sum(kbw_47), 1) if kbw_47 else None,
        "kbw_year_mm": round(sum(kbw), 1) if kbw else None,
    }


def linear_trend(xs: List[float], ys: List[Optional[float]]) -> Optional[Dict[str, float]]:
    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if len(pts) < 10:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in pts) / sxx
    return {"slope_per_year": slope, "per_decade": round(slope * 10, 2), "intercept": my - slope * mx}


TREND_KEYS = ("t_mean_c", "precip_mm", "season_days", "season_start_doy", "season_end_doy", "last_spring_frost_doy",
              "first_autumn_frost_doy", "hot_days", "frost_days", "gdd10_apr_sep", "kbw_apr_sep_mm")


def _cumulative_from_april(days: List[Dict[str, Any]]) -> Dict[str, List[Optional[float]]]:
    """Narastająco od 1 kwietnia: GDD>10°C i klimatyczny bilans wodny, indeks = dzień od 1 IV (0..182)."""
    gdd, kbw = [], []
    g = k = 0.0
    for d in days:
        if not (4 <= d["date"].month <= 9):
            continue
        if d["tmax"] is not None and d["tmin"] is not None:
            g += max(0.0, (d["tmax"] + d["tmin"]) / 2 - GDD_BASE)
        if d["p"] is not None and d["et0"] is not None:
            k += d["p"] - d["et0"]
        gdd.append(round(g, 1))
        kbw.append(round(k, 1))
    return {"gdd": gdd, "kbw": kbw}


def climate_summary(lat: float, lon: float, analysis_date: Optional[date] = None) -> Dict[str, Any]:
    """Odpowiedź GET /api/reference/climate. Nigdy nie rzuca (braki → years=[] + "brak")."""
    try:
        daily = fetch_daily(lat, lon, analysis_date=analysis_date)
    except SourceError as exc:
        return {"years": [], "trends": {}, "normals": {}, "season": None,
                "provenance": missing(era5_archive.ERA5_SOURCE, era5_archive.ERA5_URL,
                                      f"Archiwum klimatyczne niedostępne ({friendly(exc)}).")}
    by_year = _by_year(daily)
    end = date.fromisoformat(daily["end"])
    full_years = [y for y in sorted(by_year) if y < end.year or (end.month == 12 and end.day == 31)]
    years = [year_metrics(y, by_year[y]) for y in full_years]
    xs = [r["year"] for r in years]
    trends = {}
    for k in TREND_KEYS:
        t = linear_trend(xs, [r[k] for r in years])
        if t:
            trends[k] = {"per_decade": t["per_decade"], "start": round(t["intercept"] + t["slope_per_year"] * xs[0], 2),
                         "end": round(t["intercept"] + t["slope_per_year"] * xs[-1], 2)}
    normals = {}
    base = [r for r in years if NORMAL_PERIOD[0] <= r["year"] <= NORMAL_PERIOD[1]]
    for k in TREND_KEYS + ("kbw_apr_jul_mm",):
        v = [r[k] for r in base if r[k] is not None]
        normals[k] = round(sum(v) / len(v), 1) if v else None

    # bieżący sezon (od 1 IV) vs średnia 1991–2020 i rok poprzedni
    cur_year = end.year if end.month >= 4 else end.year - 1
    cum = {y: _cumulative_from_april(by_year[y]) for y in by_year if NORMAL_PERIOD[0] <= y <= NORMAL_PERIOD[1] or y in (cur_year, cur_year - 1)}
    norm_curve = {"gdd": [], "kbw": []}
    for key in ("gdd", "kbw"):
        series = [cum[y][key] for y in range(NORMAL_PERIOD[0], NORMAL_PERIOD[1] + 1) if y in cum and len(cum[y][key]) >= 183]
        norm_curve[key] = [round(sum(s[i] for s in series) / len(series), 1) for i in range(183)] if series else []
    season = {
        "year": cur_year,
        "days_since_apr1": [(date(cur_year, 4, 1) + timedelta(days=i)).strftime("%m-%d") for i in range(183)],
        "current": cum.get(cur_year), "previous": cum.get(cur_year - 1), "normal": norm_curve,
        "previous_year": cur_year - 1,
    }
    if daily.get("provider", era5_archive.PROVIDER_ERA5) == era5_archive.PROVIDER_ERA5:
        prov = provenance(
            "ERA5 (Copernicus C3S, ECMWF) przez archiwum Open-Meteo", "https://open-meteo.com/en/docs/historical-weather-api", "reanaliza",
            fetched_at=daily.get("fetched_at"), resolution="ok. 25 km (siatka ERA5)",
            note=(f"Dobowe dane reanalizy ERA5 od 1991 r. do {daily['end']} dla komórki siatki z działką – model z asymilacją pomiarów, "
                  "nie pomiar stacyjny; dobrze oddaje różnice między latami i trendy, słabiej lokalne przymrozki. Wskaźniki: okres "
                  "wegetacyjny (Tśr > 5°C przez 5 dni), dni upalne (Tmax > 30°C, IMGW-PIB), GDD > 10°C (IV–IX), klimatyczny bilans "
                  "wodny = opad − ET0 (FAO-56). Trend – regresja liniowa; średnia odniesienia 1991–2020. Dane: Open-Meteo.com, CC BY 4.0."),
        )
    else:
        prov = provenance(
            era5_archive.POWER_SOURCE, era5_archive.POWER_URL, "reanaliza",
            fetched_at=daily.get("fetched_at"), resolution="ok. 50 km (siatka 0,5° × 0,625°)",
            note=(f"Archiwum ERA5 chwilowo niedostępne ({daily.get('fallback_reason') or 'brak odpowiedzi'}) – użyto zapasowej "
                  f"reanalizy NASA POWER (MERRA-2 / GEOS-IT) od {daily.get('start')} do {daily['end']}: Tmax, Tmin, Tśr, opad "
                  "PRECTOTCORR; ET0 policzone wzorem Hargreavesa (FAO-56, równ. 52) z temperatur, więc klimatyczny bilans wodny "
                  "jest mniej dokładny niż z ERA5 (Penman-Monteith). Rozdzielczość grubsza, niepewność większa. "
                  "Źródło: NASA Langley Research Center POWER Project (dane publiczne)."),
        )
    return {
        "years": years, "trends": trends, "normals": normals, "season": season,
        "normal_period": f"{NORMAL_PERIOD[0]}–{NORMAL_PERIOD[1]}",
        "data_end": daily["end"], "cell": daily.get("cell"), "source": daily.get("provider", era5_archive.PROVIDER_ERA5),
        "provenance": prov,
    }

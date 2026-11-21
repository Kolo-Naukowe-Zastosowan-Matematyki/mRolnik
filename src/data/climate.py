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
from src.data.common import SourceError, friendly, http_get_json, iso_utc, missing, provenance, to_float, utcnow

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
START_YEAR = 1991
NORMAL_PERIOD = (1991, 2020)
CLIMATE_TTL_S = 7 * 24 * 3600
_climate_cache = TTLCache("climate_era5", max_entries=2000, default_ttl=CLIMATE_TTL_S)

SEASON_T = 5.0
GDD_BASE = 10.0
HOT_T = 30.0


def _grid_key(lat: float, lon: float) -> Tuple[float, float]:
    """Siatka 0,1° – ERA5 ma ~0,25°, więc sąsiednie działki dzielą pobranie."""
    return round(lat, 1), round(lon, 1)


def fetch_daily(lat: float, lon: float) -> Dict[str, Any]:
    """Dobowe dane ERA5 od 1991 r. (cache 7 dni). Rzuca SourceError."""
    glat, glon = _grid_key(lat, lon)
    end = (utcnow() - timedelta(days=6)).date()
    key = f"{glat:.1f},{glon:.1f}:{end.isoformat()}"
    cached = _climate_cache.get(key)
    if cached:
        return cached
    data = http_get_json(ARCHIVE_URL, params={
        "latitude": f"{glat:.2f}", "longitude": f"{glon:.2f}", "start_date": f"{START_YEAR}-01-01", "end_date": end.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean,precipitation_sum,et0_fao_evapotranspiration",
        "timezone": "Europe/Warsaw",
    }, timeout=30.0, retries=1)
    d = (data or {}).get("daily") if isinstance(data, dict) else None
    if not isinstance(d, dict) or not isinstance(d.get("time"), list) or len(d["time"]) < 3650:
        raise SourceError("Open-Meteo archive: za mało danych dobowych")
    n = len(d["time"])

    def col(name: str) -> List[Optional[float]]:
        vals = d.get(name) or []
        return [to_float(v) for v in vals] + [None] * (n - len(vals))

    res = {"time": d["time"], "tmax": col("temperature_2m_max"), "tmin": col("temperature_2m_min"), "tmean": col("temperature_2m_mean"),
           "precip": col("precipitation_sum"), "et0": col("et0_fao_evapotranspiration"), "end": end.isoformat(),
           "fetched_at": iso_utc(), "cell": [to_float(data.get("latitude")), to_float(data.get("longitude"))]}
    _climate_cache.set(key, res, CLIMATE_TTL_S)
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


def climate_summary(lat: float, lon: float) -> Dict[str, Any]:
    """Odpowiedź GET /api/reference/climate. Nigdy nie rzuca (braki → years=[] + "brak")."""
    try:
        daily = fetch_daily(lat, lon)
    except SourceError as exc:
        return {"years": [], "trends": {}, "normals": {}, "season": None,
                "provenance": missing("ERA5 (Copernicus C3S) przez Open-Meteo", "https://open-meteo.com/",
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
    return {
        "years": years, "trends": trends, "normals": normals, "season": season,
        "normal_period": f"{NORMAL_PERIOD[0]}–{NORMAL_PERIOD[1]}",
        "data_end": daily["end"], "cell": daily.get("cell"),
        "provenance": provenance(
            "ERA5 (Copernicus C3S, ECMWF) przez archiwum Open-Meteo", "https://open-meteo.com/en/docs/historical-weather-api", "reanaliza",
            fetched_at=daily.get("fetched_at"), resolution="ok. 25 km (siatka ERA5)",
            note=(f"Dobowe dane reanalizy ERA5 od 1991 r. do {daily['end']} dla komórki siatki z działką – model z asymilacją pomiarów, "
                  "nie pomiar stacyjny; dobrze oddaje różnice między latami i trendy, słabiej lokalne przymrozki. Wskaźniki: okres "
                  "wegetacyjny (Tśr > 5°C przez 5 dni), dni upalne (Tmax > 30°C, IMGW-PIB), GDD > 10°C (IV–IX), klimatyczny bilans "
                  "wodny = opad − ET0 (FAO-56). Trend – regresja liniowa; średnia odniesienia 1991–2020. Dane: Open-Meteo.com, CC BY 4.0."),
        ),
    }


# --------------------------------------------------------------------------------------
# Analiza historyczna na zadaną datę (as_of) + 10-letnia podatność na suszę (bez wycieku przyszłości)
# --------------------------------------------------------------------------------------
MONTH_LABELS_PL = {
    1: "styczeń", 2: "luty", 3: "marzec", 4: "kwiecień", 5: "maj", 6: "czerwiec",
    7: "lipiec", 8: "sierpień", 9: "wrzesień", 10: "październik", 11: "listopad", 12: "grudzień",
}
ERA5_SOURCE = "ERA5 (Copernicus C3S, ECMWF) przez archiwum Open-Meteo"
ERA5_URL = "https://open-meteo.com/en/docs/historical-weather-api"
DRY_DAY_MM = 1.0
DROUGHT_KBW_APR_JUL_MM = -100.0
DROUGHT_KBW_APR_SEP_MM = -150.0


def _slice_until(daily: Dict[str, Any], as_of: date) -> List[Dict[str, Any]]:
    """Zwraca listę dni z archiwum ERA5 wyłącznie dla dat ≤ as_of (zero wycieku danych z przyszłości)."""
    rows: List[Dict[str, Any]] = []
    for i, t in enumerate(daily.get("time") or []):
        dt = date.fromisoformat(t)
        if dt > as_of:
            continue
        rows.append({
            "date": dt,
            "tmax": daily["tmax"][i],
            "tmin": daily["tmin"][i],
            "tmean": daily["tmean"][i],
            "p": daily["precip"][i],
            "et0": daily["et0"][i],
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def _window_sums(rows: List[Dict[str, Any]], as_of: date, days: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(opad_mm, et0_mm, kbw_mm) za okres [as_of - (days - 1), as_of]."""
    start = as_of - timedelta(days=days - 1)
    win = [r for r in rows if start <= r["date"] <= as_of]
    ps = [r["p"] for r in win if r["p"] is not None]
    es = [r["et0"] for r in win if r["et0"] is not None]
    ks = [r["p"] - r["et0"] for r in win if r["p"] is not None and r["et0"] is not None]
    return (
        round(sum(ps), 1) if ps else None,
        round(sum(es), 1) if es else None,
        round(sum(ks), 1) if ks else None,
    )


def _dry_spells(rows: List[Dict[str, Any]], as_of: date, window_days: int = 60) -> Tuple[int, int]:
    """(bieżący ciąg bezopadowy kończący się w as_of, najdłuższy ciąg bezopadowy w ostatnich window_days dniach)."""
    start = as_of - timedelta(days=window_days - 1)
    win = [r for r in rows if start <= r["date"] <= as_of]
    if not win:
        return 0, 0
    max_run = cur_run = 0
    for r in win:
        if r["p"] is not None and r["p"] < DRY_DAY_MM:
            cur_run += 1
            if cur_run > max_run:
                max_run = cur_run
        else:
            cur_run = 0
    return cur_run, max_run


def weather_as_of(lat: float, lon: float, as_of: date, window_days: int = 60) -> Dict[str, Any]:
    """Pogoda historyczna i bilans wodny na dzień `as_of` z ERA5 (wyłącznie dane ≤ as_of). Nigdy nie rzuca."""
    try:
        daily = fetch_daily(lat, lon)
    except SourceError as exc:
        msg = f"Archiwum pogodowe ERA5 niedostępne ({friendly(exc)})."
        prov = missing(ERA5_SOURCE, ERA5_URL, msg)
        return {
            "current": {"temp_c": None, "humidity_pct": None, "wind_ms": None, "precip_mm": None, "station": None},
            "daily": [], "recent_daily": [],
            "precip_7d_mm": None, "precip_14d_mm": None, "precip_30d_mm": None, "precip_60d_mm": None, "precip_90d_mm": None,
            "et0_30d_mm": None, "et0_60d_mm": None, "et0_90d_mm": None,
            "climatic_water_balance_14d_mm": None, "kbw_60d_mm": None, "kbw_90d_mm": None,
            "dry_spell_days": None, "max_dry_spell_60d_days": None, "hot_days_60d": None,
            "forecast_days": 0, "precip_past_7d_mm": None, "soil_temp_c": None, "warnings": [],
            "model": {"key": "era5", "label": "ERA5 (reanaliza historyczna)"},
            "provenance": {"current": prov, "forecast": prov, "warnings": prov},
            "notes": [msg],
        }

    rows = _slice_until(daily, as_of)
    if not rows:
        msg = f"Brak danych ERA5 dla daty {as_of.isoformat()} lub wcześniejszych."
        prov = missing(ERA5_SOURCE, ERA5_URL, msg)
        return {
            "current": {"temp_c": None, "humidity_pct": None, "wind_ms": None, "precip_mm": None, "station": None},
            "daily": [], "recent_daily": [],
            "precip_7d_mm": None, "precip_14d_mm": None, "precip_30d_mm": None, "precip_60d_mm": None, "precip_90d_mm": None,
            "et0_30d_mm": None, "et0_60d_mm": None, "et0_90d_mm": None,
            "climatic_water_balance_14d_mm": None, "kbw_60d_mm": None, "kbw_90d_mm": None,
            "dry_spell_days": None, "max_dry_spell_60d_days": None, "hot_days_60d": None,
            "forecast_days": 0, "precip_past_7d_mm": None, "soil_temp_c": None, "warnings": [],
            "model": {"key": "era5", "label": "ERA5 (reanaliza historyczna)"},
            "provenance": {"current": prov, "forecast": prov, "warnings": prov},
            "notes": [msg],
        }

    last = rows[-1]
    p7, _, _ = _window_sums(rows, as_of, 7)
    p14, _, kbw14 = _window_sums(rows, as_of, 14)
    p30, et30, _ = _window_sums(rows, as_of, 30)
    p60, et60, kbw60 = _window_sums(rows, as_of, max(60, window_days))
    p90, et90, kbw90 = _window_sums(rows, as_of, 90)
    dry_now, dry_max60 = _dry_spells(rows, as_of, 60)
    start60 = as_of - timedelta(days=59)
    hot60 = sum(1 for r in rows if start60 <= r["date"] <= as_of and r["tmax"] is not None and r["tmax"] > HOT_T)

    recent_start = as_of - timedelta(days=13)
    recent_daily = [
        {
            "date": r["date"].isoformat(),
            "t_max_c": round(r["tmax"], 1) if r["tmax"] is not None else None,
            "t_min_c": round(r["tmin"], 1) if r["tmin"] is not None else None,
            "t_mean_c": round(r["tmean"], 1) if r["tmean"] is not None else None,
            "rain_mm": round(r["p"], 1) if r["p"] is not None else None,
            "et0_mm": round(r["et0"], 2) if r["et0"] is not None else None,
        }
        for r in rows if recent_start <= r["date"] <= as_of
    ]

    prov_era5 = provenance(
        ERA5_SOURCE, ERA5_URL, "reanaliza",
        observed_at=last["date"].isoformat(),
        fetched_at=daily.get("fetched_at"),
        resolution="ok. 25 km (siatka ERA5)",
        note=(f"Dane historyczne reanalizy ERA5 na dzień {as_of.isoformat()} (ostatni dostępny dzień ≤ data analizy: "
              f"{last['date'].isoformat()}). Okno 60 dni: opad {p60} mm, ET0 {et60} mm, KBW {kbw60} mm. "
              "Brak prognozy przyszłości – analiza bez wycieku danych po dacie analizy. Open-Meteo.com, CC BY 4.0."),
    )
    prov_warn = missing("IMGW-PIB – ostrzeżenia meteorologiczne", "https://danepubliczne.imgw.pl/",
                        f"Ostrzeżenia IMGW-PIB nie są archiwizowane w API publicznym dla dat historycznych ({as_of.isoformat()}).")
    return {
        "current": {
            "temp_c": round(last["tmean"], 1) if last["tmean"] is not None else None,
            "t_max_c": round(last["tmax"], 1) if last["tmax"] is not None else None,
            "t_min_c": round(last["tmin"], 1) if last["tmin"] is not None else None,
            "humidity_pct": None,
            "wind_ms": None,
            "precip_mm": round(last["p"], 1) if last["p"] is not None else None,
            "station": f"ERA5 (siatka ~25 km, {last['date'].isoformat()})",
        },
        "daily": [],  # brak prognozy w przód w trybie historycznym (anti-leakage)
        "recent_daily": recent_daily,
        "precip_7d_mm": p7,
        "precip_past_7d_mm": p7,
        "precip_14d_mm": p14,
        "precip_30d_mm": p30,
        "precip_60d_mm": p60,
        "precip_90d_mm": p90,
        "et0_30d_mm": et30,
        "et0_60d_mm": et60,
        "et0_90d_mm": et90,
        "climatic_water_balance_14d_mm": kbw14,
        "kbw_60d_mm": kbw60,
        "kbw_90d_mm": kbw90,
        "dry_spell_days": dry_now,
        "max_dry_spell_60d_days": dry_max60,
        "hot_days_60d": hot60,
        "forecast_days": 0,
        "soil_temp_c": None,
        "warnings": [],
        "model": {"key": "era5", "label": "ERA5 (reanaliza historyczna)"},
        "provenance": {"current": prov_era5, "forecast": prov_era5, "warnings": prov_warn},
        "notes": [],
    }


def drought_climatology_10y(lat: float, lon: float, as_of: date) -> Dict[str, Any]:
    """10-letnia historyczna podatność na suszę (lata pełne kończące się przed/w `as_of`, zero wycieku przyszłości)."""
    try:
        daily = fetch_daily(lat, lon)
    except SourceError as exc:
        return {
            "available": False, "period": None, "years_analyzed": 0,
            "drought_years_count": None, "drought_frequency_pct": None, "drought_years": [],
            "mean_kbw_apr_jul_mm": None, "mean_kbw_apr_sep_mm": None, "mean_precip_apr_sep_mm": None,
            "monthly_kbw": [], "worst_months": [], "vulnerability_level": None,
            "provenance": missing(ERA5_SOURCE, ERA5_URL, f"Brak danych 10-letnich ERA5 ({friendly(exc)})."),
        }

    rows = _slice_until(daily, as_of)
    by_year: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        by_year.setdefault(r["date"].year, []).append(r)

    last_full_year = as_of.year if (as_of.month == 12 and as_of.day == 31) else as_of.year - 1
    target_years = [y for y in range(last_full_year - 9, last_full_year + 1) if y in by_year and len(by_year[y]) >= 300]
    if not target_years:
        return {
            "available": False, "period": None, "years_analyzed": 0,
            "drought_years_count": None, "drought_frequency_pct": None, "drought_years": [],
            "mean_kbw_apr_jul_mm": None, "mean_kbw_apr_sep_mm": None, "mean_precip_apr_sep_mm": None,
            "monthly_kbw": [], "worst_months": [], "vulnerability_level": None,
            "provenance": missing(ERA5_SOURCE, ERA5_URL, f"Za mało pełnych lat ERA5 przed {as_of.isoformat()}."),
        }

    kbw_47_list: List[float] = []
    kbw_49_list: List[float] = []
    p_49_list: List[float] = []
    drought_years: List[int] = []
    month_vals: Dict[int, List[float]] = {m: [] for m in range(4, 10)}

    for y in target_years:
        ym = year_metrics(y, by_year[y])
        k47 = ym.get("kbw_apr_jul_mm")
        k49 = ym.get("kbw_apr_sep_mm")
        p49 = ym.get("precip_veg_mm")
        if k47 is not None:
            kbw_47_list.append(k47)
        if k49 is not None:
            kbw_49_list.append(k49)
        if p49 is not None:
            p_49_list.append(p49)
        if (k47 is not None and k47 < DROUGHT_KBW_APR_JUL_MM) or (k49 is not None and k49 < DROUGHT_KBW_APR_SEP_MM):
            drought_years.append(y)
        for m in range(4, 10):
            mk = [r["p"] - r["et0"] for r in by_year[y] if r["date"].month == m and r["p"] is not None and r["et0"] is not None]
            if mk:
                month_vals[m].append(round(sum(mk), 1))

    n_years = len(target_years)
    freq_pct = round(len(drought_years) / n_years * 100)
    mean_47 = round(sum(kbw_47_list) / len(kbw_47_list), 1) if kbw_47_list else None
    mean_49 = round(sum(kbw_49_list) / len(kbw_49_list), 1) if kbw_49_list else None
    mean_p49 = round(sum(p_49_list) / len(p_49_list), 1) if p_49_list else None

    monthly_kbw = []
    for m in range(4, 10):
        vals = month_vals[m]
        monthly_kbw.append({
            "month": m,
            "label": MONTH_LABELS_PL[m],
            "mean_kbw_mm": round(sum(vals) / len(vals), 1) if vals else None,
            "deficit_years": sum(1 for v in vals if v < -30.0),
        })

    worst = sorted((m for m in monthly_kbw if m["mean_kbw_mm"] is not None), key=lambda x: x["mean_kbw_mm"])
    worst_months = [m["label"] for m in worst[:3] if m["mean_kbw_mm"] < 0]

    if freq_pct >= 50 or (mean_47 is not None and mean_47 < -120.0):
        vuln = "wysoka"
    elif freq_pct >= 20 or (mean_47 is not None and mean_47 < -60.0):
        vuln = "umiarkowana"
    else:
        vuln = "niska"

    period = f"{target_years[0]}–{target_years[-1]}"
    return {
        "available": True,
        "period": period,
        "years_analyzed": n_years,
        "drought_years_count": len(drought_years),
        "drought_frequency_pct": freq_pct,
        "drought_years": drought_years,
        "mean_kbw_apr_jul_mm": mean_47,
        "mean_kbw_apr_sep_mm": mean_49,
        "mean_precip_apr_sep_mm": mean_p49,
        "monthly_kbw": monthly_kbw,
        "worst_months": worst_months,
        "vulnerability_level": vuln,
        "provenance": provenance(
            ERA5_SOURCE, ERA5_URL, "reanaliza",
            observed_at=as_of.isoformat(),
            fetched_at=daily.get("fetched_at"),
            resolution="ok. 25 km (siatka ERA5)",
            note=(f"10-letnia podatność na suszę ({period}, wyłącznie dane ≤ {as_of.isoformat()}): lata z niedoborem wody "
                  f"(KBW IV–VII < {int(DROUGHT_KBW_APR_JUL_MM)} mm lub IV–IX < {int(DROUGHT_KBW_APR_SEP_MM)} mm): "
                  f"{len(drought_years)}/{n_years} ({freq_pct}%). Open-Meteo.com, CC BY 4.0."),
        ),
    }

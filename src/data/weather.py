"""Pogoda: obserwacje IMGW-PIB (synop, ostrzeżenia, hydro) i prognoza Open-Meteo.

Źródła:
- IMGW-PIB, dane publiczne: https://danepubliczne.imgw.pl/api/data/synop (obserwacje co godzinę, stacje
  synoptyczne), /warningsmeteo (ostrzeżenia meteorologiczne), /hydro (stany wody; /hydro2 zwraca pustą odpowiedź – nieużywane).
  Wymagana atrybucja: "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej –
  Państwowy Instytut Badawczy".
- Open-Meteo (https://open-meteo.com, CC BY 4.0): prognoza 14 dni z modeli ECMWF IFS / DWD ICON / NOAA GFS.
  Open-Meteo służy też jako zapas dla bieżących warunków, gdy IMGW nie odpowie (oznaczone jako "model").
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.core.cache import TTLCache
from src.data.common import (
    friendly,
    SOURCE_IMGW_HYDRO,
    SOURCE_IMGW_SYNOP,
    SOURCE_IMGW_WARN,
    SOURCE_OM_FORECAST,
    SourceError,
    haversine_km,
    http_get_json,
    iso_utc,
    missing,
    normalize_name,
    provenance,
    record_error,
    record_ok,
    safe_sum,
    to_float,
    utcnow,
)
from src.data.imgw_stations import HYDRO_STATIONS, SYNOP_STATIONS, SynopStation
from src.data.reference import climate_block

log = logging.getLogger(__name__)

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
IMGW_SYNOP_URL = "https://danepubliczne.imgw.pl/api/data/synop"
IMGW_WARNINGS_URL = "https://danepubliczne.imgw.pl/api/data/warningsmeteo"
# /api/data/hydro2 zwraca HTTP 200 z pustą treścią (text/html) – używamy wyłącznie /hydro (współrzędne + stany
# ostrzegawcze/alarmowe). Czas "stan_wody_data_pomiaru" jest w UTC (zgodny z hydro-back.imgw.pl, np. 12:50 ↔ 12:50Z).
IMGW_HYDRO_URLS = ("https://danepubliczne.imgw.pl/api/data/hydro",)
IMGW_ATTRIBUTION = "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy"

# Klucz API -> parametr `models` w Open-Meteo (None = automatyczny dobór "best_match")
WEATHER_MODELS: Dict[str, Dict[str, Any]] = {
    "auto": {"label": "Automatyczny dobór modelu (Open-Meteo best match: ICON / ECMWF / GFS)", "param": None, "days": 14},
    "ecmwf": {"label": "ECMWF IFS 0,25° (Europejskie Centrum Prognoz Średnioterminowych)", "param": "ecmwf_ifs025", "days": 14},
    "icon": {"label": "DWD ICON (Niemiecka Służba Meteorologiczna; prognoza ok. 7 dni)", "param": "icon_seamless", "days": 7},
    "gfs": {"label": "NOAA GFS (globalny model amerykański)", "param": "gfs_seamless", "days": 14},
}

FORECAST_TTL_S = 30 * 60
SYNOP_TTL_S = 20 * 60
WARNINGS_TTL_S = 10 * 60
HYDRO_TTL_S = 30 * 60
CLUSTER_DEG = 0.05            # rejon pogodowy ≈ 5 km (rozdzielczość modeli 2–25 km)
SYNOP_STALE_H = 6             # obserwacja starsza niż 6 h → używamy modelu jako bieżących warunków
HYDRO_MAX_KM = 25.0

_forecast_cache = TTLCache("om_forecast", max_entries=5000, default_ttl=FORECAST_TTL_S)
_synop_cache = TTLCache("imgw_synop", max_entries=10, default_ttl=SYNOP_TTL_S)
_warn_cache = TTLCache("imgw_warn", max_entries=10, default_ttl=WARNINGS_TTL_S)
_hydro_cache = TTLCache("imgw_hydro", max_entries=10, default_ttl=HYDRO_TTL_S)
_hydro_hist_cache = TTLCache("imgw_hydro_hist", max_entries=3000, default_ttl=48 * 3600)


# --------------------------------------------------------------------------------------
# Czas lokalny w Polsce bez zależności od bazy stref (Windows nie ma tzdata)
# --------------------------------------------------------------------------------------
def _last_sunday(year: int, month: int) -> datetime:
    d = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(days=1) if month < 12 else datetime(year, 12, 31, tzinfo=timezone.utc)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def poland_utc_offset(now_utc: Optional[datetime] = None) -> timedelta:
    """CET (+1) / CEST (+2): czas letni od ostatniej niedzieli marca 01:00 UTC do ostatniej niedzieli października 01:00 UTC."""
    now_utc = now_utc or utcnow()
    y = now_utc.year
    start = _last_sunday(y, 3).replace(hour=1)
    end = _last_sunday(y, 10).replace(hour=1)
    return timedelta(hours=2) if start <= now_utc < end else timedelta(hours=1)


def poland_now() -> datetime:
    now = utcnow()
    return (now + poland_utc_offset(now)).replace(tzinfo=None)


def cluster_key(lat: float, lon: float) -> Tuple[float, float]:
    """Zaokrąglenie współrzędnych do siatki rejonów pogodowych (wspólny cache dla sąsiednich działek)."""
    return (round(round(lat / CLUSTER_DEG) * CLUSTER_DEG, 3), round(round(lon / CLUSTER_DEG) * CLUSTER_DEG, 3))


# --------------------------------------------------------------------------------------
# Open-Meteo: prognoza 14 dni (+ 7 dni wstecz dla sumy opadów)
# --------------------------------------------------------------------------------------
def parse_open_meteo_forecast(data: Dict[str, Any]) -> Dict[str, Any]:
    daily = data.get("daily")
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        raise SourceError("Open-Meteo: brak sekcji daily")
    offset_s = int(to_float(data.get("utc_offset_seconds")) or 0)
    today = (utcnow() + timedelta(seconds=offset_s)).date().isoformat()

    def col(name: str) -> List[Optional[float]]:
        vals = daily.get(name) or []
        return [to_float(v) for v in vals] + [None] * (len(daily["time"]) - len(vals))

    rain, tmax, tmin, et0 = col("precipitation_sum"), col("temperature_2m_max"), col("temperature_2m_min"), col("et0_fao_evapotranspiration")
    past, future = [], []
    for i, d in enumerate(daily["time"]):
        row = {"date": str(d), "rain_mm": rain[i], "t_max_c": tmax[i], "t_min_c": tmin[i], "et0_mm": et0[i]}
        (past if str(d) < today else future).append(row)
    future = future[:14]
    cur = data.get("current") if isinstance(data.get("current"), dict) else {}
    return {"past": past, "daily": future, "current": cur, "utc_offset_seconds": offset_s}


def fetch_forecast(lat: float, lon: float, model_key: str = "auto") -> Dict[str, Any]:
    """Prognoza dzienna Open-Meteo dla rejonu (cache 30 min). Rzuca SourceError."""
    model = WEATHER_MODELS.get(model_key) or WEATHER_MODELS["auto"]
    clat, clon = cluster_key(lat, lon)
    key = f"{clat:.3f},{clon:.3f},{model_key}"
    cached = _forecast_cache.get(key)
    if cached:
        return cached
    params: Dict[str, Any] = {
        "latitude": f"{clat:.3f}",
        "longitude": f"{clon:.3f}",
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,et0_fao_evapotranspiration",
        "past_days": 7,
        "forecast_days": 14,
        "wind_speed_unit": "ms",
        "timezone": "Europe/Warsaw",
    }
    if model["param"]:
        params["models"] = model["param"]
    try:
        data = http_get_json(OPEN_METEO_FORECAST_URL, params=params, timeout=8.0, retries=1)
        if not isinstance(data, dict):
            raise SourceError("Open-Meteo: niepoprawna odpowiedź")
        parsed = parse_open_meteo_forecast(data)
    except SourceError as exc:
        record_error(SOURCE_OM_FORECAST, str(exc))
        raise
    parsed["fetched_at"] = iso_utc()
    parsed["model_key"] = model_key
    parsed["model_label"] = model["label"]
    parsed["point"] = [clat, clon]
    record_ok(SOURCE_OM_FORECAST)
    _forecast_cache.set(key, parsed, FORECAST_TTL_S)
    return parsed


# --------------------------------------------------------------------------------------
# IMGW-PIB synop
# --------------------------------------------------------------------------------------
def fetch_synop_all() -> Dict[str, Any]:
    cached = _synop_cache.get("all")
    if cached:
        return cached
    try:
        data = http_get_json(IMGW_SYNOP_URL, timeout=6.0, retries=1)
        if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
            raise SourceError("IMGW synop: nieoczekiwany format odpowiedzi")
    except SourceError as exc:
        record_error(SOURCE_IMGW_SYNOP, str(exc))
        raise
    record_ok(SOURCE_IMGW_SYNOP, f"{len(data)} stacji")
    payload = {"records": data, "fetched_at": iso_utc()}
    _synop_cache.set("all", payload, SYNOP_TTL_S)
    return payload


def stations_by_distance(lat: float, lon: float) -> List[Tuple[float, SynopStation]]:
    out = [(haversine_km(lat, lon, s.lat, s.lon), s) for s in SYNOP_STATIONS if not s.mountain]
    out.sort(key=lambda t: t[0])
    return out


def _synop_observed_at(rec: Dict[str, Any]) -> Optional[datetime]:
    try:
        d = str(rec.get("data_pomiaru") or "").strip()
        h = int(str(rec.get("godzina_pomiaru") or "").strip())
        return datetime.strptime(d, "%Y-%m-%d").replace(hour=h, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def nearest_synop(lat: float, lon: float, records: List[Dict[str, Any]], max_candidates: int = 8, max_km: float = 150.0
                  ) -> Optional[Tuple[SynopStation, Dict[str, Any], float]]:
    """Najbliższa stacja (z tabeli współrzędnych, ≤150 km), dla której API zwróciło temperaturę."""
    by_id = {str(r.get("id_stacji", "")).strip(): r for r in records}
    by_name = {normalize_name(str(r.get("stacja", ""))): r for r in records}
    for dist, st in stations_by_distance(lat, lon)[:max_candidates]:
        if dist > max_km:
            break
        rec = by_id.get(st.id) or by_name.get(normalize_name(st.name))
        if rec and to_float(rec.get("temperatura")) is not None:
            return st, rec, dist
    return None


def parse_synop_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    hum = to_float(rec.get("wilgotnosc_wzgledna"))
    return {
        "temp_c": to_float(rec.get("temperatura")),
        "humidity_pct": None if hum is None else round(hum),
        "wind_ms": to_float(rec.get("predkosc_wiatru")),
        "precip_mm": to_float(rec.get("suma_opadu")),
        "pressure_hpa": to_float(rec.get("cisnienie")),
    }


def current_conditions(lat: float, lon: float, forecast: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """Bieżące warunki: najpierw IMGW-PIB synop (pomiar), potem Open-Meteo (model). Zwraca (current, provenance, ostrzeżenia)."""
    warnings: List[str] = []
    empty = {"temp_c": None, "humidity_pct": None, "wind_ms": None, "precip_mm": None, "station": None}
    try:
        payload = fetch_synop_all()
        found = nearest_synop(lat, lon, payload["records"])  # type: ignore[index]
        if found is None:
            raise SourceError("brak danych z pobliskich stacji")
        st, rec, dist = found
        obs = _synop_observed_at(rec)
        age_h = (utcnow() - obs).total_seconds() / 3600 if obs else None
        if age_h is not None and age_h > SYNOP_STALE_H:
            raise SourceError(f"ostatnia obserwacja ze stacji {st.name} ma {age_h:.0f} h")
        cur = parse_synop_record(rec)
        cur["station"] = f"{rec.get('stacja') or st.name} (IMGW-PIB)"
        cur["station_distance_km"] = round(dist, 1)
        if dist > 60:
            warnings.append(f"Najbliższa stacja synoptyczna IMGW-PIB ({st.name}) jest {dist:.0f} km od działki – warunki lokalne mogą się różnić.")
        prov = provenance(
            "IMGW-PIB – stacja synoptyczna " + str(rec.get("stacja") or st.name), "https://danepubliczne.imgw.pl/", "pomiar",
            observed_at=iso_utc(obs) if obs else None, fetched_at=payload.get("fetched_at"),  # type: ignore[union-attr]
            note=(f"Obserwacja ze stacji oddalonej o {dist:.0f} km (termin obserwacji w UTC). Opad: suma wg IMGW dla terminu "
                  f"obserwacji. {IMGW_ATTRIBUTION}."),
        )
        return cur, prov, warnings
    except SourceError as exc:
        log.info("IMGW synop niedostępny (%s) – próba Open-Meteo", exc)
        warnings.append(f"Brak aktualnej obserwacji IMGW-PIB ({friendly(exc)}); bieżące warunki z modelu Open-Meteo.")
    cur_om = (forecast or {}).get("current") or {}
    if forecast and to_float(cur_om.get("temperature_2m")) is not None:
        hum = to_float(cur_om.get("relative_humidity_2m"))
        cur = {
            "temp_c": to_float(cur_om.get("temperature_2m")),
            "humidity_pct": None if hum is None else round(hum),
            "wind_ms": to_float(cur_om.get("wind_speed_10m")),
            "precip_mm": to_float(cur_om.get("precipitation")),
            "station": None,
        }
        obs = None
        try:
            local = datetime.strptime(str(cur_om.get("time")), "%Y-%m-%dT%H:%M")
            obs = iso_utc((local - timedelta(seconds=forecast.get("utc_offset_seconds", 0))).replace(tzinfo=timezone.utc))
        except (ValueError, TypeError):
            pass
        prov = provenance(
            "Open-Meteo – " + forecast.get("model_label", "model numeryczny"), "https://open-meteo.com/", "model",
            observed_at=obs, fetched_at=forecast.get("fetched_at"),
            note="Wartości z modelu numerycznego dla rejonu działki (nie pomiar stacyjny). Dane: Open-Meteo.com, CC BY 4.0.",
        )
        return cur, prov, warnings
    return empty, missing("IMGW-PIB / Open-Meteo", "https://danepubliczne.imgw.pl/",
                          "Brak bieżących danych pogodowych – IMGW-PIB i Open-Meteo nie odpowiedziały."), warnings


# --------------------------------------------------------------------------------------
# IMGW-PIB ostrzeżenia meteorologiczne
# --------------------------------------------------------------------------------------
def fetch_warnings_all() -> Dict[str, Any]:
    cached = _warn_cache.get("all")
    if cached is not None:
        return cached
    try:
        data = http_get_json(IMGW_WARNINGS_URL, timeout=6.0, retries=1)
        if not isinstance(data, list):
            raise SourceError("IMGW ostrzeżenia: nieoczekiwany format")
    except SourceError as exc:
        record_error(SOURCE_IMGW_WARN, str(exc))
        raise
    record_ok(SOURCE_IMGW_WARN, f"{len(data)} aktywnych ostrzeżeń w kraju")
    payload = {"records": [r for r in data if isinstance(r, dict)], "fetched_at": iso_utc()}
    _warn_cache.set("all", payload, WARNINGS_TTL_S)
    return payload


def _norm_time(s: Any) -> Optional[str]:
    if not s:
        return None
    return str(s).strip().replace(" ", "T")


def filter_warnings(records: List[Dict[str, Any]], powiat_teryt: Optional[str], voiv_teryt: Optional[str]) -> List[Dict[str, Any]]:
    now_local = poland_now().strftime("%Y-%m-%dT%H:%M:%S")
    out = []
    for r in records:
        teryt = r.get("teryt") or []
        if isinstance(teryt, str):
            teryt = [t.strip() for t in teryt.split(",")]
        teryt = [str(t) for t in teryt]
        if powiat_teryt:
            if powiat_teryt not in teryt:
                continue
        elif voiv_teryt:
            if not any(t.startswith(voiv_teryt) for t in teryt):
                continue
        else:
            continue
        valid_to = _norm_time(r.get("obowiazuje_do"))
        if valid_to and valid_to < now_local:
            continue
        lvl = to_float(r.get("stopien"))
        out.append({
            "level": int(lvl) if lvl is not None else None,
            "event": str(r.get("nazwa_zdarzenia") or "Ostrzeżenie meteorologiczne"),
            "valid_from": _norm_time(r.get("obowiazuje_od")),
            "valid_to": valid_to,
            "probability_pct": to_float(r.get("prawdopodobienstwo")),
            "text": str(r.get("tresc") or "")[:600],
            "source": "IMGW-PIB",
        })
    out.sort(key=lambda w: -(w["level"] or 0))
    return out


def warnings_for(lat: float, lon: float, powiat_teryt: Optional[str] = None, voiv_teryt: Optional[str] = None
                 ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not voiv_teryt and not powiat_teryt:
        voiv_teryt = stations_by_distance(lat, lon)[0][1].voiv_teryt
    try:
        payload = fetch_warnings_all()
    except SourceError as exc:
        return [], missing("IMGW-PIB – ostrzeżenia meteorologiczne", "https://meteo.imgw.pl/",
                           f"Nie udało się pobrać ostrzeżeń IMGW-PIB ({friendly(exc)}). Sprawdź https://meteo.imgw.pl/.")
    items = filter_warnings(payload["records"], powiat_teryt, voiv_teryt)
    scope = f"powiatu (TERYT {powiat_teryt})" if powiat_teryt else f"województwa (TERYT {voiv_teryt})"
    prov = provenance(
        "IMGW-PIB – ostrzeżenia meteorologiczne", "https://meteo.imgw.pl/", "prognoza",
        fetched_at=payload.get("fetched_at"),
        note=f"Aktualne ostrzeżenia dla {scope}; czasy w czasie lokalnym. {IMGW_ATTRIBUTION}.",
    )
    return items, prov


# --------------------------------------------------------------------------------------
# IMGW-PIB hydrologia: najbliższy wodowskaz
# --------------------------------------------------------------------------------------
def _first(rec: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if rec.get(k) not in (None, ""):
            return rec[k]
    return None


def _imgw_utc(s: Any) -> Optional[str]:
    """Czas z API hydro IMGW ("YYYY-MM-DD HH:MM:SS", UTC) → ISO 8601 z "Z"; niepoprawny → None."""
    if not s:
        return None
    try:
        dt = datetime.strptime(str(s).strip()[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(str(s).strip()[:16].replace("T", " "), "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    return iso_utc(dt.replace(tzinfo=timezone.utc))


def parse_hydro_records(data: Any) -> List[Dict[str, Any]]:
    """Rekordy API hydro → ujednolicone słowniki. Współrzędne z API, a gdy ich brak – z tabeli HYDRO_STATIONS."""
    if not isinstance(data, list):
        raise SourceError("IMGW hydro: nieoczekiwany format")
    table = {normalize_name(h.name): h for h in HYDRO_STATIONS}
    recs = []
    for r in data:
        if not isinstance(r, dict):
            continue
        name = str(_first(r, "nazwa_stacji", "stacja") or "").strip()
        level = to_float(_first(r, "stan", "stan_wody"))
        if not name or level is None:
            continue
        lat, lon = to_float(_first(r, "lat", "szerokosc")), to_float(_first(r, "lon", "dlugosc"))
        coords_src = "api"
        if lat is None or lon is None:
            h = table.get(normalize_name(name))
            if h is None:
                continue
            lat, lon, coords_src = h.lat, h.lon, "tabela"
        recs.append({
            "id": str(_first(r, "kod_stacji", "id_stacji") or ""),
            "name": name,
            "river": str(_first(r, "rzeka") or "").strip(),
            "lat": lat, "lon": lon, "coords_source": coords_src,
            "level_cm": level,
            "measured_at": _imgw_utc(_first(r, "stan_data", "stan_wody_data_pomiaru")),
            # IMGW nie publikuje stanów ostrzegawczych/alarmowych w tym API; bierzemy je tylko jeśli pole istnieje
            "warning_level_cm": to_float(_first(r, "stan_ostrzegawczy", "stan_ostrz")),
            "alarm_level_cm": to_float(_first(r, "stan_alarmowy", "stan_alarm")),
        })
    return recs


def fetch_hydro_all() -> Dict[str, Any]:
    cached = _hydro_cache.get("all")
    if cached is not None:
        return cached
    last: Optional[Exception] = None
    for url in IMGW_HYDRO_URLS:
        try:
            recs = parse_hydro_records(http_get_json(url, timeout=6.0, retries=0))
            if not recs:
                raise SourceError("brak posterunków z lokalizacją")
            payload = {"records": recs, "fetched_at": iso_utc(), "url": url}
            record_ok(SOURCE_IMGW_HYDRO, f"{len(recs)} posterunków")
            _hydro_cache.set("all", payload, HYDRO_TTL_S)
            _remember_levels(recs)
            return payload
        except SourceError as exc:
            last = exc
            log.info("IMGW hydro %s: %s", url, exc)
    record_error(SOURCE_IMGW_HYDRO, str(last))
    raise SourceError(str(last))


def _remember_levels(recs: List[Dict[str, Any]]) -> None:
    """Zapamiętuje kolejne odczyty (48 h), żeby policzyć trend stanu wody (API podaje tylko stan bieżący)."""
    for r in recs:
        if not r.get("measured_at"):
            continue
        key = normalize_name(r["name"] + r["river"])
        hist = _hydro_hist_cache.get(key) or []
        if not hist or hist[-1][0] != r["measured_at"]:
            hist.append([r["measured_at"], r["level_cm"]])
        _hydro_hist_cache.set(key, hist[-60:], 48 * 3600)


def hydro_trend(rec: Dict[str, Any]) -> Optional[str]:
    hist = _hydro_hist_cache.get(normalize_name(rec["name"] + rec["river"])) or []
    if len(hist) < 2:
        return None
    try:
        t_last = datetime.strptime(hist[-1][0][:16], "%Y-%m-%dT%H:%M")
        older = [h for h in hist if (t_last - datetime.strptime(h[0][:16], "%Y-%m-%dT%H:%M")) >= timedelta(hours=3)]
    except (ValueError, TypeError):
        return None
    if not older:
        return None
    d = hist[-1][1] - older[-1][1]
    return "rośnie" if d >= 3 else ("spada" if d <= -3 else "stabilny")


def nearest_hydro(lat: float, lon: float) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    try:
        payload = fetch_hydro_all()
    except SourceError as exc:
        return None, missing("IMGW-PIB – dane hydrologiczne", "https://hydro.imgw.pl/", f"Brak danych hydrologicznych IMGW-PIB ({friendly(exc)}).")
    best = min(payload["records"], key=lambda r: haversine_km(lat, lon, r["lat"], r["lon"]))
    dist = haversine_km(lat, lon, best["lat"], best["lon"])
    if dist > HYDRO_MAX_KM:
        return None, missing("IMGW-PIB – dane hydrologiczne", "https://hydro.imgw.pl/",
                             f"Najbliższy wodowskaz IMGW-PIB jest {dist:.0f} km od działki – pominięto jako niereprezentatywny.")
    out = dict(best, distance_km=round(dist, 1), trend=hydro_trend(best))
    loc = "współrzędne z API IMGW" if best.get("coords_source") == "api" else "lokalizacja przybliżona z tabeli mRolnik"
    prov = provenance(
        f"IMGW-PIB – wodowskaz {best['name']} ({best['river']})", "https://hydro.imgw.pl/", "pomiar",
        observed_at=best.get("measured_at"), fetched_at=payload.get("fetched_at"),
        note=(f"Stan wody na najbliższym wodowskazie ({dist:.0f} km, {loc}); czas pomiaru w UTC. Trend liczony z kolejnych "
              f"odczytów zapamiętanych przez serwer (min. 3 h). {IMGW_ATTRIBUTION}."),
    )
    return out, prov


# --------------------------------------------------------------------------------------
# Złożenie bloku "weather" (Kontrakt 2)
# --------------------------------------------------------------------------------------
def summarize_daily(daily: List[Dict[str, Any]]) -> Dict[str, Any]:
    rain = [d["rain_mm"] for d in daily]
    et0 = [d["et0_mm"] for d in daily]
    pairs = [(r, e) for r, e in zip(rain, et0) if r is not None and e is not None]
    return {
        "precip_7d_mm": safe_sum(rain[:7]),
        "precip_14d_mm": safe_sum(rain[:14]),
        "climatic_water_balance_14d_mm": round(sum(r - e for r, e in pairs), 1) if pairs else None,
        "forecast_days": len(pairs),
    }


def get_weather(lat: float, lon: float, model_key: str = "auto",
                powiat_teryt: Optional[str] = None, voiv_teryt: Optional[str] = None) -> Dict[str, Any]:
    """Blok pogodowy wg Kontraktu 2. Nigdy nie rzuca wyjątku – braki oznaczone proweniencją "brak"."""
    warnings: List[str] = []
    forecast: Optional[Dict[str, Any]] = None
    try:
        forecast = fetch_forecast(lat, lon, model_key)
    except SourceError as exc:
        warnings.append(f"Prognoza Open-Meteo niedostępna ({friendly(exc)}).")
    current, prov_cur, w_cur = current_conditions(lat, lon, forecast)
    warnings += w_cur
    alerts, prov_warn = warnings_for(lat, lon, powiat_teryt, voiv_teryt)

    if forecast:
        daily = forecast["daily"]
        summary = summarize_daily(daily)
        past_rain = safe_sum(d["rain_mm"] for d in forecast.get("past", [])[-7:])
        n = summary["forecast_days"]
        note = (f"Prognoza dobowa modelu {forecast['model_label']} dla rejonu działki (siatka ~5 km). "
                "Bilans wodny = suma opadów − suma ewapotranspiracji wskaźnikowej ET0 (FAO-56) w horyzoncie prognozy. "
                "Opad z ostatnich 7 dni: wartości modelowe (analiza). Dane: Open-Meteo.com, CC BY 4.0.")
        if n < 14:
            warnings.append(f"Model obejmuje {n} dni – sumy i bilans liczone dla {n} dni zamiast 14.")
        prov_fc = provenance("Open-Meteo – " + forecast["model_label"], "https://open-meteo.com/", "prognoza",
                             fetched_at=forecast.get("fetched_at"), resolution="2–25 km (zależnie od modelu)", note=note)
    else:
        daily, past_rain = [], None
        summary = {"precip_7d_mm": None, "precip_14d_mm": None, "climatic_water_balance_14d_mm": None, "forecast_days": 0}
        prov_fc = missing("Open-Meteo", "https://open-meteo.com/", "Prognoza pogody niedostępna.")

    return {
        "current": current,
        "daily": daily,
        **summary,
        "precip_past_7d_mm": past_rain,
        "soil_temp_c": None,
        "warnings": alerts,
        "model": model_key,
        "provenance": {"current": prov_cur, "forecast": prov_fc, "warnings": prov_warn},
        # linie referencyjne do wykresu prognozy: normy IMGW-PIB 1991–2020 dla najbliższej stacji synoptycznej
        "reference": {"climate_normals": climate_block(lat, lon, [d["date"] for d in daily])},
        "notes": warnings,
    }

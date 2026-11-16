"""Wilgotność i temperatura gleby z modeli numerycznych (Open-Meteo) oraz zapas wody dla roślin.

Źródła (przez Open-Meteo, CC BY 4.0):
- prognoza/analiza modeli DWD ICON (zmienne soil_moisture_0_to_1cm … 9_to_27cm, soil_temperature_6cm),
  ewentualnie ECMWF IFS (soil_moisture_0_to_7cm, 7_to_28cm, soil_temperature_0_to_7cm),
- zapasowo reanaliza ERA5-Land (Copernicus Climate Change Service) z archiwum Open-Meteo (opóźnienie ok. 5 dni).
To są wartości MODELOWE dla komórki siatki (2–11 km), a nie pomiar na działce.

Zapas wody (dni) – model bilansu wodnego warstwy 0–28 cm (FAO-56, rozdz. 8):
  TAW = (θFC − θWP) · 280 mm;  AW = (θ − θWP) · 280 mm (obcięte do 0…TAW);
  RAW = p · TAW, p = 0,5;  dzień po dniu: AW ← min(TAW, AW + 0,8·P − ET0)  (Kc = 1, 80% opadu efektywne);
  wynik = liczba dni prognozy, zanim AW spadnie poniżej TAW − RAW (początek stresu wodnego).
θFC/θWP: typowe wartości dla klas uziarnienia wg FAO-56 (tab. 19), przypisane do polskich nazw gleb.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.core.cache import TTLCache
from src.data.common import (
    SOURCE_OM_SOIL,
    SourceError,
    http_get_json,
    iso_utc,
    missing,
    provenance,
    record_error,
    friendly,
    record_ok,
    to_float,
    utcnow,
)
from src.data.reference import soil_reference_provenance
from src.data.weather import cluster_key

log = logging.getLogger(__name__)

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOIL_TTL_S = 3600
_soil_cache = TTLCache("om_soil", max_entries=5000, default_ttl=SOIL_TTL_S)

ICON_VARS = ["soil_moisture_0_to_1cm", "soil_moisture_1_to_3cm", "soil_moisture_3_to_9cm", "soil_moisture_9_to_27cm"]
ECMWF_VARS = ["soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm"]

ROOT_DEPTH_MM = 280.0
DEPLETION_P = 0.5
EFFECTIVE_RAIN = 0.8

# Typowe θ pojemności polowej (FC) i punktu trwałego więdnięcia (WP), m³/m³ – środki zakresów FAO-56 tab. 19.
SOIL_TEXTURES: Dict[str, Dict[str, Any]] = {
    "piasek": {"label": "piasek (gleba bardzo lekka)", "fc": 0.12, "wp": 0.045},
    "piasek_gliniasty": {"label": "piasek gliniasty (gleba lekka)", "fc": 0.15, "wp": 0.065},
    "glina_piaszczysta": {"label": "glina piaszczysta / piaszczysto-gliniasta", "fc": 0.23, "wp": 0.11},
    "glina": {"label": "glina (gleba średnia)", "fc": 0.25, "wp": 0.12},
    "pyl": {"label": "utwór pyłowy / less", "fc": 0.29, "wp": 0.15},
    "glina_pylasta": {"label": "glina pylasta / ilasta (gleba ciężka, mady, czarne ziemie)", "fc": 0.34, "wp": 0.21},
    "il": {"label": "ił (gleba bardzo ciężka)", "fc": 0.36, "wp": 0.22},
    "organiczna": {"label": "gleba organiczna / torfowa (wartości orientacyjne)", "fc": 0.55, "wp": 0.25},
}


def classify_soil(name: Optional[str]) -> Tuple[str, bool]:
    """Polska nazwa gleby → klasa uziarnienia. Zwraca (klucz, czy_rozpoznano)."""
    s = (name or "").lower()
    rules = [
        (("torf", "organiczn", "murszow"), "organiczna"),
        (("ił", "ilast", "bardzo ciężk"), "il"),
        (("piaszczysto-glin", "glina piaszcz", "gliniasto-piasz"), "glina_piaszczysta"),
        (("piasek glin", "lekka", "piaszczyst"), "piasek_gliniasty"),
        (("piasek", "luźn"), "piasek"),
        (("mada", "czarne ziemie", "czarna ziemia", "ciężk"), "glina_pylasta"),
        (("less", "pył", "pyl", "czarnoziem"), "pyl"),
        (("glin", "brunatn", "rędzin", "średni", "morenow", "płow"), "glina"),
    ]
    for keys, cls in rules:
        if any(k in s for k in keys):
            return cls, True
    return "glina", False


# --------------------------------------------------------------------------------------
# Parsowanie odpowiedzi Open-Meteo
# --------------------------------------------------------------------------------------
def _w_avg(pairs: List[Tuple[Optional[float], float]]) -> Optional[float]:
    vals = [(v, w) for v, w in pairs if v is not None]
    if not vals or len(vals) < len(pairs):
        return None
    return sum(v * w for v, w in vals) / sum(w for _, w in vals)


def layers_at(hourly: Dict[str, List[Any]], i: int) -> Tuple[Optional[float], Optional[float]]:
    """(θ 0–7 cm, θ 7–28 cm) w m³/m³ dla indeksu godziny; obsługuje oba zestawy zmiennych."""
    def v(name: str) -> Optional[float]:
        arr = hourly.get(name) or []
        return to_float(arr[i]) if i < len(arr) else None

    if v("soil_moisture_0_to_7cm") is not None or v("soil_moisture_7_to_28cm") is not None:
        return v("soil_moisture_0_to_7cm"), v("soil_moisture_7_to_28cm")
    # ICON: 0–1, 1–3, 3–9, 9–27 cm → średnie ważone grubością części wspólnej z 0–7 i 7–28 cm
    surface = _w_avg([(v("soil_moisture_0_to_1cm"), 1), (v("soil_moisture_1_to_3cm"), 2), (v("soil_moisture_3_to_9cm"), 4)])
    root = _w_avg([(v("soil_moisture_3_to_9cm"), 2), (v("soil_moisture_9_to_27cm"), 18)])
    return surface, root


def soil_temp_at(hourly: Dict[str, List[Any]], i: int) -> Optional[float]:
    for name in ("soil_temperature_6cm", "soil_temperature_0_to_7cm"):
        arr = hourly.get(name) or []
        if i < len(arr) and to_float(arr[i]) is not None:
            return to_float(arr[i])
    return None


def _combined(surface: Optional[float], root: Optional[float]) -> Optional[float]:
    if surface is None or root is None:
        return root if surface is None else surface
    return (surface * 7 + root * 21) / 28


def parse_soil_hourly(data: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Wybiera najnowszą godzinę ≤ teraz z kompletem danych i trend względem stanu sprzed 7 dni."""
    hourly = data.get("hourly")
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list) or not hourly["time"]:
        raise SourceError("Open-Meteo: brak danych godzinowych gleby")
    now = now or utcnow()
    times: List[datetime] = []
    for t in hourly["time"]:
        try:
            times.append(datetime.strptime(str(t), "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc))
        except ValueError as exc:
            raise SourceError("Open-Meteo: niepoprawny format czasu") from exc
    idx = None
    for i in range(len(times) - 1, -1, -1):
        if times[i] <= now and layers_at(hourly, i)[1] is not None:
            idx = i
            break
    if idx is None:
        raise SourceError("Open-Meteo: brak wartości wilgotności gleby")
    surface, root = layers_at(hourly, idx)
    target = times[idx] - timedelta(days=7)
    j = min(range(idx + 1), key=lambda k: abs((times[k] - target).total_seconds()))
    trend = None
    if (times[idx] - times[j]) >= timedelta(days=5):
        a, b = _combined(*layers_at(hourly, j)), _combined(surface, root)
        if a is not None and b is not None:
            d = b - a
            trend = "rośnie" if d > 0.01 else ("spada" if d < -0.01 else "stabilna")
    return {
        "surface": surface,
        "root": root,
        "soil_temp_c": soil_temp_at(hourly, idx),
        "valid_at": iso_utc(times[idx]),
        "trend_7d": trend,
    }


# --------------------------------------------------------------------------------------
# Pobieranie
# --------------------------------------------------------------------------------------
def _fetch_forecast_soil(lat: float, lon: float) -> Dict[str, Any]:
    params = {
        "latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}",
        "hourly": ",".join(ICON_VARS + ["soil_temperature_6cm"]),
        "past_days": 7, "forecast_days": 1, "timezone": "GMT",
    }
    data = http_get_json(OPEN_METEO_FORECAST_URL, params=params, timeout=8.0, retries=1)
    if not isinstance(data, dict):
        raise SourceError("Open-Meteo: niepoprawna odpowiedź")
    out = parse_soil_hourly(data)
    out.update(kind="model", source="Open-Meteo – model DWD ICON (analiza/prognoza)",
               note_src="Model numeryczny DWD ICON przez Open-Meteo (warstwy 0–1, 1–3, 3–9, 9–27 cm przeliczone na 0–7 i 7–28 cm).")
    return out


def _fetch_era5_land(lat: float, lon: float) -> Dict[str, Any]:
    end = utcnow().date()
    start = end - timedelta(days=16)
    params = {
        "latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": ",".join(ECMWF_VARS + ["soil_temperature_0_to_7cm"]),
        "models": "era5_land", "timezone": "GMT",
    }
    data = http_get_json(OPEN_METEO_ARCHIVE_URL, params=params, timeout=10.0, retries=1)
    if not isinstance(data, dict):
        raise SourceError("Open-Meteo archive: niepoprawna odpowiedź")
    out = parse_soil_hourly(data)
    out.update(kind="reanaliza", source="ERA5-Land (Copernicus C3S) przez Open-Meteo",
               note_src="Reanaliza ERA5-Land (Copernicus Climate Change Service, ECMWF), opóźnienie ok. 5 dni, siatka ~9 km.")
    return out


def fetch_soil(lat: float, lon: float) -> Dict[str, Any]:
    """Stan gleby dla rejonu (cache 1 h). Rzuca SourceError, gdy oba źródła zawiodą."""
    clat, clon = cluster_key(lat, lon)
    key = f"{clat:.3f},{clon:.3f}"
    cached = _soil_cache.get(key)
    if cached:
        return cached
    errors = []
    for fn in (_fetch_forecast_soil, _fetch_era5_land):
        try:
            res = fn(clat, clon)
            res["fetched_at"] = iso_utc()
            record_ok(SOURCE_OM_SOIL, res["source"])
            _soil_cache.set(key, res, SOIL_TTL_S)
            return res
        except SourceError as exc:
            msg = friendly(exc)
            if msg not in errors:
                errors.append(msg)
            log.info("Gleba (%s): %s", fn.__name__, exc)
    record_error(SOURCE_OM_SOIL, "; ".join(errors))
    raise SourceError("; ".join(errors))


# --------------------------------------------------------------------------------------
# Zapas wody
# --------------------------------------------------------------------------------------
def water_reserve(theta_surface: Optional[float], theta_root: Optional[float], soil_name: Optional[str],
                  daily: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Liczba dni do początku stresu wodnego wg prostego bilansu (patrz opis modułu)."""
    cls, recognized = classify_soil(soil_name)
    tex = SOIL_TEXTURES[cls]
    fc, wp = tex["fc"], tex["wp"]
    theta = _combined(theta_surface, theta_root)
    result: Dict[str, Any] = {
        "days": None, "at_least": False, "soil_class": tex["label"], "soil_recognized": recognized,
        "field_capacity_pct": round(fc * 100, 1), "wilting_point_pct": round(wp * 100, 1),
        "available_water_mm": None, "taw_mm": round((fc - wp) * ROOT_DEPTH_MM, 1),
    }
    if theta is None:
        return result
    taw = (fc - wp) * ROOT_DEPTH_MM
    aw = min(taw, max(0.0, (theta - wp) * ROOT_DEPTH_MM))
    threshold = taw * (1 - DEPLETION_P)
    result["available_water_mm"] = round(aw, 1)
    days_usable = [d for d in daily if d.get("et0_mm") is not None and d.get("rain_mm") is not None]
    if aw < threshold:
        result["days"] = 0
        return result
    if not days_usable:
        return result
    for n, d in enumerate(days_usable):
        aw = min(taw, aw + EFFECTIVE_RAIN * d["rain_mm"] - d["et0_mm"])
        if aw < threshold:
            result["days"] = n
            return result
    result["days"] = len(days_usable)
    result["at_least"] = True
    return result


def soil_block(lat: float, lon: float, soil_name: Optional[str], daily: List[Dict[str, Any]]
               ) -> Tuple[Optional[Dict[str, Any]], Optional[int], Dict[str, Any], Dict[str, Any], Optional[float], List[str]]:
    """(soil_moisture, water_reserve_days, prov_soil, prov_water, soil_temp_c, ostrzeżenia). Nigdy nie rzuca."""
    try:
        s = fetch_soil(lat, lon)
    except SourceError as exc:
        return soil_block_from(None, str(exc), soil_name, daily)
    return soil_block_from(s, None, soil_name, daily)


def soil_block_from(s: Optional[Dict[str, Any]], error: Optional[str], soil_name: Optional[str], daily: List[Dict[str, Any]]
                    ) -> Tuple[Optional[Dict[str, Any]], Optional[int], Dict[str, Any], Dict[str, Any], Optional[float], List[str]]:
    """Jak soil_block, ale z wynikiem fetch_soil pobranym wcześniej (s) albo komunikatem błędu."""
    warnings: List[str] = []
    if s is None:
        note = f"Brak danych o wilgotności gleby z Open-Meteo/ERA5-Land ({error})."
        return None, None, missing("Open-Meteo", "https://open-meteo.com/", note), \
            missing("Open-Meteo", "https://open-meteo.com/", "Zapas wody nieznany – brak danych o wilgotności gleby."), None, [note]
    sm = {
        "surface_pct": None if s["surface"] is None else round(s["surface"] * 100, 1),
        "root_zone_pct": None if s["root"] is None else round(s["root"] * 100, 1),
        "trend_7d": s.get("trend_7d"),
    }
    wr = water_reserve(s["surface"], s["root"], soil_name, daily)
    sm.update(soil_class=wr["soil_class"], field_capacity_pct=wr["field_capacity_pct"], wilting_point_pct=wr["wilting_point_pct"])
    # linie referencyjne pojemności polowej i punktu więdnięcia – wartości tabelaryczne (FAO-56), nie pomiar
    sm["reference_provenance"] = soil_reference_provenance(wr["soil_class"], wr["field_capacity_pct"], wr["wilting_point_pct"])
    # Ten sam wskaźnik co w HydroMelior: woda dostępna jako % zakresu użytecznego (θ − θWP)/(θFC − θWP)
    sm["available_water_pct"] = (None if wr["available_water_mm"] is None or not wr["taw_mm"]
                                 else round(wr["available_water_mm"] / wr["taw_mm"] * 100))
    if not wr["soil_recognized"]:
        warnings.append("Nie rozpoznano rodzaju gleby – do zapasu wody przyjęto glebę średnią (glina).")
    prov_soil = provenance(
        s["source"], "https://open-meteo.com/", s["kind"], observed_at=s.get("valid_at"), fetched_at=s.get("fetched_at"),
        resolution="2–11 km (komórka modelu)",
        note=s["note_src"] + " Wilgotność objętościowa [% obj.] dla komórki siatki, nie pomiar na działce. Dane: Open-Meteo.com, CC BY 4.0.",
    )
    days_txt = "brak prognozy ET0" if wr["days"] is None else (f"co najmniej {wr['days']} dni (horyzont prognozy)" if wr["at_least"] else f"{wr['days']} dni")
    if wr["days"] == 0 and sm.get("available_water_pct") is not None:
        days_txt = f"0 dni – łatwo dostępna woda już wyczerpana (zostało ok. {sm['available_water_pct']}% wody użytecznej)"
    prov_water = provenance(
        "Model bilansu wodnego mRolnik (FAO-56)", "https://www.fao.org/3/x0490e/x0490e00.htm", "model",
        observed_at=s.get("valid_at"), fetched_at=s.get("fetched_at"),
        note=(f"Dni do stresu wodnego: {days_txt}. Warstwa 0–28 cm, gleba: {wr['soil_class']} (θFC {wr['field_capacity_pct']}%, θWP "
              f"{wr['wilting_point_pct']}% – typowe wartości FAO-56). Dzień po dniu: woda dostępna + 0,8·opad − ET0 (Kc=1); "
              "wynik = dni do spadku poniżej 50% wody dostępnej (początek stresu)."),
    )
    return sm, wr["days"], prov_soil, prov_water, s.get("soil_temp_c"), warnings

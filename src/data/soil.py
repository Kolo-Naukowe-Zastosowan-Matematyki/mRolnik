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
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.core.cache import TTLCache
from src.data.common import (
    SOURCE_OM_SOIL,
    SOURCE_SOILGRIDS,
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
SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
SOILGRIDS_SOURCE_NAME = "ISRIC SoilGrids v2.0 (0–30 cm)"
SOILGRIDS_SOURCE_URL = "https://soilgrids.org/"
SOIL_TTL_S = 3600
SOIL_HIST_TTL_S = 30 * 24 * 3600
SOILGRIDS_TTL_S = 30 * 24 * 3600
_soil_cache = TTLCache("om_soil", max_entries=5000, default_ttl=SOIL_TTL_S)
_soilgrids_cache = TTLCache("soilgrids", max_entries=5000, default_ttl=SOILGRIDS_TTL_S)

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
# ISRIC SoilGrids v2.0 (0–30 cm): automatyczne uziarnienie, próchnica i kategoria suszowa IUNG (I–IV)
# --------------------------------------------------------------------------------------
IUNG_DROUGHT_CATEGORIES: Dict[str, Dict[str, str]] = {
    "piasek": {"code": "I", "label": "Kategoria I – bardzo podatna na suszę (gleba bardzo lekka)"},
    "piasek_gliniasty": {"code": "II", "label": "Kategoria II – podatna na suszę (gleba lekka)"},
    "glina_piaszczysta": {"code": "III", "label": "Kategoria III – średnio podatna na suszę (gleba średnia)"},
    "glina": {"code": "III", "label": "Kategoria III – średnio podatna na suszę (gleba średnia)"},
    "pyl": {"code": "III", "label": "Kategoria III – średnio podatna na suszę (gleba średnia)"},
    "glina_pylasta": {"code": "IV", "label": "Kategoria IV – mało podatna na suszę (gleba ciężka)"},
    "il": {"code": "IV", "label": "Kategoria IV – mało podatna na suszę (gleba bardzo ciężka)"},
    "organiczna": {"code": "IV", "label": "Kategoria IV – mało podatna na suszę (gleba organiczna)"},
}

_SG_DEPTH_WEIGHTS = (("0-5cm", 5.0), ("5-15cm", 10.0), ("15-30cm", 15.0))


def classify_texture_from_fractions(
    sand_pct: Optional[float], silt_pct: Optional[float], clay_pct: Optional[float], organic_matter_pct: Optional[float] = None
) -> str:
    """Frakcje uziarnienia [% masy] (0–30 cm) → klucz w SOIL_TEXTURES (wg klas USDA/PTG i FAO-56)."""
    if organic_matter_pct is not None and organic_matter_pct >= 12.0:
        return "organiczna"
    if sand_pct is None or clay_pct is None:
        return "glina"
    silt = silt_pct if silt_pct is not None else max(0.0, 100.0 - sand_pct - clay_pct)
    if clay_pct >= 40.0:
        return "il"
    if clay_pct >= 27.0:
        return "glina_pylasta"
    if sand_pct >= 80.0:
        return "piasek"
    if sand_pct >= 65.0:
        return "piasek_gliniasty"
    if silt >= 50.0:
        return "pyl"
    if sand_pct >= 45.0:
        return "glina_piaszczysta"
    return "glina"


def _parse_soilgrids_layer(layers: List[Dict[str, Any]], prop_name: str, scale: float) -> Optional[float]:
    """Średnia ważona miąższością dla warstwy 0–30 cm (0–5, 5–15, 15–30 cm)."""
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("name") != prop_name:
            continue
        by_label: Dict[str, Optional[float]] = {}
        for d in layer.get("depths") or []:
            if not isinstance(d, dict):
                continue
            lbl = str(d.get("label") or "")
            vals = d.get("values") or {}
            by_label[lbl] = to_float(vals.get("mean"))
        weighted = [(by_label.get(lbl), w) for lbl, w in _SG_DEPTH_WEIGHTS if by_label.get(lbl) is not None]
        if not weighted:
            return None
        raw = sum(v * w for v, w in weighted) / sum(w for _, w in weighted)
        return round(raw / scale, 2)
    return None


def fetch_soilgrids(lat: float, lon: float) -> Dict[str, Any]:
    """Pobiera uziarnienie (piasek/pył/ił) i SOC (0–30 cm) z ISRIC SoilGrids v2.0 (cache 30 dni). Rzuca SourceError."""
    glat, glon = round(lat, 3), round(lon, 3)
    key = f"{glat:.3f},{glon:.3f}"
    cached = _soilgrids_cache.get(key)
    if cached:
        return cached
    params = [
        ("lat", f"{glat:.4f}"),
        ("lon", f"{glon:.4f}"),
        ("property", "sand"),
        ("property", "silt"),
        ("property", "clay"),
        ("property", "soc"),
        ("depth", "0-5cm"),
        ("depth", "5-15cm"),
        ("depth", "15-30cm"),
        ("value", "mean"),
    ]
    try:
        data = http_get_json(SOILGRIDS_URL, params=params, timeout=8.0, retries=1)
    except SourceError as exc:
        record_error(SOURCE_SOILGRIDS, friendly(exc))
        raise
    layers = ((data or {}).get("properties") or {}).get("layers") if isinstance(data, dict) else None
    if not isinstance(layers, list) or not layers:
        record_error(SOURCE_SOILGRIDS, "brak warstw w odpowiedzi")
        raise SourceError("ISRIC SoilGrids: brak danych dla tego punktu (np. teren zabudowany lub woda)")
    # SoilGrids: sand/silt/clay w g/kg (÷10 = %), soc w dg/kg (÷10 = g/kg)
    sand_pct = _parse_soilgrids_layer(layers, "sand", 10.0)
    silt_pct = _parse_soilgrids_layer(layers, "silt", 10.0)
    clay_pct = _parse_soilgrids_layer(layers, "clay", 10.0)
    soc_g_kg = _parse_soilgrids_layer(layers, "soc", 10.0)
    if sand_pct is None and clay_pct is None:
        record_error(SOURCE_SOILGRIDS, "punkt bez danych glebowych")
        raise SourceError("ISRIC SoilGrids: brak wartości uziarnienia dla tej lokalizacji")
    om_pct = round(soc_g_kg * 0.1724, 2) if soc_g_kg is not None else None
    om_status = None if om_pct is None else ("niska" if om_pct < 1.5 else ("średnia" if om_pct <= 2.5 else "dobra"))
    tex_key = classify_texture_from_fractions(sand_pct, silt_pct, clay_pct, om_pct)
    tex = SOIL_TEXTURES[tex_key]
    iung = IUNG_DROUGHT_CATEGORIES[tex_key]
    fetched_at = iso_utc()
    res = {
        "available": True,
        "texture_key": tex_key,
        "texture_label": tex["label"],
        "sand_pct": sand_pct,
        "silt_pct": silt_pct,
        "clay_pct": clay_pct,
        "soc_g_kg": soc_g_kg,
        "organic_matter_pct": om_pct,
        "organic_matter_status": om_status,
        "drought_category": iung["code"],
        "drought_category_label": iung["label"],
        "field_capacity_pct": round(tex["fc"] * 100, 1),
        "wilting_point_pct": round(tex["wp"] * 100, 1),
        "provenance": provenance(
            SOILGRIDS_SOURCE_NAME, SOILGRIDS_SOURCE_URL, "model",
            fetched_at=fetched_at, resolution="250 m (siatka SoilGrids)",
            note=(f"Model globalny ISRIC SoilGrids v2.0 (0–30 cm, CC BY 4.0): piasek {sand_pct}%, pył {silt_pct}%, "
                  f"ił {clay_pct}%, próchnica ok. {om_pct}% ({om_status or 'brak'}). Nie zastępuje badania laboratoryjnego gleby."),
        ),
    }
    record_ok(SOURCE_SOILGRIDS, f"{tex_key} (kat. {iung['code']})")
    _soilgrids_cache.set(key, res, SOILGRIDS_TTL_S)
    return res


def soilgrids_properties(lat: float, lon: float) -> Dict[str, Any]:
    """Bezpieczny wrapper na fetch_soilgrids (nigdy nie rzuca; przy błędzie zwraca available=False + proweniencję brak)."""
    try:
        return fetch_soilgrids(lat, lon)
    except SourceError as exc:
        return {
            "available": False,
            "texture_key": None,
            "texture_label": None,
            "sand_pct": None, "silt_pct": None, "clay_pct": None,
            "soc_g_kg": None, "organic_matter_pct": None, "organic_matter_status": None,
            "drought_category": None, "drought_category_label": None,
            "provenance": missing(SOILGRIDS_SOURCE_NAME, SOILGRIDS_SOURCE_URL,
                                  f"ISRIC SoilGrids niedostępny ({friendly(exc)}) – użyto gleby podanej przez użytkownika lub domyślnej."),
        }


# --------------------------------------------------------------------------------------
# Parametry wodne upraw (FAO-56 tab. 12 i 22): współczynnik Kc (miesięczny dla Polski) i próg p
# --------------------------------------------------------------------------------------
_CROP_RULES: List[Tuple[Tuple[str, ...], str, float, Dict[int, float]]] = [
    (("ziemniak",), "ziemniak", 0.35, {4: 0.45, 5: 0.55, 6: 0.95, 7: 1.15, 8: 1.10, 9: 0.75}),
    (("kukurydz",), "kukurydza", 0.55, {4: 0.30, 5: 0.40, 6: 0.75, 7: 1.15, 8: 1.20, 9: 0.85, 10: 0.55}),
    (("burak",), "burak cukrowy", 0.55, {4: 0.35, 5: 0.60, 6: 1.00, 7: 1.20, 8: 1.20, 9: 1.05, 10: 0.75}),
    (("rzepak",), "rzepak ozimy", 0.60, {3: 0.60, 4: 1.00, 5: 1.10, 6: 0.90, 7: 0.45, 9: 0.50, 10: 0.65, 11: 0.55}),
    (("pszenic",), "pszenica", 0.55, {3: 0.50, 4: 0.85, 5: 1.15, 6: 1.15, 7: 0.65, 8: 0.35, 10: 0.45, 11: 0.45}),
    (("jęczmień", "jeczmien"), "jęczmień", 0.55, {4: 0.50, 5: 1.00, 6: 1.15, 7: 0.60, 8: 0.35}),
    (("pszenżyt", "pszenzyt", "żyto", "zyto"), "żyto / pszenżyto", 0.55, {3: 0.50, 4: 0.80, 5: 1.10, 6: 1.05, 7: 0.55, 10: 0.45}),
    (("owies", "ows"), "owies", 0.55, {4: 0.45, 5: 0.95, 6: 1.15, 7: 0.70, 8: 0.35}),
    (("soja", "groch", "łubin", "lubin", "bobik", "strączk", "straczk"), "rośliny strączkowe", 0.50, {4: 0.40, 5: 0.55, 6: 0.95, 7: 1.15, 8: 0.85}),
    (("łąk", "lak", "pastwisk", "użytek zielony", "uzytek zielony", "traw", "koniczyn", "lucern"), "użytek zielony / motylkowe", 0.50,
     {4: 0.90, 5: 0.95, 6: 0.95, 7: 0.95, 8: 0.95, 9: 0.90, 10: 0.75}),
]


def crop_water_params(crop: Optional[str], month: Optional[int] = None) -> Dict[str, Any]:
    """Zwraca {'kc', 'p', 'crop_label', 'crop_recognized'} wg FAO-56. Gdy brak/nieznana uprawa: Kc=1.0, p=0.5."""
    s = (crop or "").strip().lower()
    m = month if (month is not None and 1 <= month <= 12) else utcnow().month
    if s:
        for keys, label, p_val, kc_map in _CROP_RULES:
            if any(k in s for k in keys):
                kc = kc_map.get(m, 0.40)
                return {"kc": round(kc, 2), "p": p_val, "crop_label": label, "crop_recognized": True}
    return {"kc": 1.0, "p": DEPLETION_P, "crop_label": None, "crop_recognized": False}


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


def _fetch_era5_land(lat: float, lon: float, as_of: Optional[date] = None) -> Dict[str, Any]:
    end = as_of or utcnow().date()
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
    cutoff = datetime(end.year, end.month, end.day, 23, 59, tzinfo=timezone.utc) if as_of is not None else None
    out = parse_soil_hourly(data, now=cutoff)
    out.update(kind="reanaliza", source="ERA5-Land (Copernicus C3S) przez Open-Meteo",
               note_src="Reanaliza ERA5-Land (Copernicus Climate Change Service, ECMWF), opóźnienie ok. 5 dni, siatka ~9 km.")
    return out


def fetch_soil(lat: float, lon: float, as_of: Optional[date] = None) -> Dict[str, Any]:
    """Stan gleby dla rejonu (cache 1 h dla bieżących, 30 dni dla historycznych). Rzuca SourceError, gdy źródła zawiodą."""
    clat, clon = cluster_key(lat, lon)
    is_historical = as_of is not None and (utcnow().date() - as_of).days > 5
    key = f"{clat:.3f},{clon:.3f}:{as_of.isoformat()}" if as_of is not None else f"{clat:.3f},{clon:.3f}"
    cached = _soil_cache.get(key)
    if cached:
        return cached
    errors = []
    if is_historical:
        fetchers = [(lambda a, b: _fetch_era5_land(a, b, as_of=as_of), "_fetch_era5_land")]
        ttl = SOIL_HIST_TTL_S
    else:
        fetchers = [
            (_fetch_forecast_soil, "_fetch_forecast_soil"),
            (lambda a, b: _fetch_era5_land(a, b, as_of=as_of), "_fetch_era5_land"),
        ]
        ttl = SOIL_TTL_S
    for fn, fname in fetchers:
        try:
            res = fn(clat, clon)
            res["fetched_at"] = iso_utc()
            record_ok(SOURCE_OM_SOIL, res["source"])
            _soil_cache.set(key, res, ttl)
            return res
        except SourceError as exc:
            msg = friendly(exc)
            if msg not in errors:
                errors.append(msg)
            log.info("Gleba (%s): %s", fname, exc)
    record_error(SOURCE_OM_SOIL, "; ".join(errors))
    raise SourceError("; ".join(errors))


# --------------------------------------------------------------------------------------
# Zapas wody
# --------------------------------------------------------------------------------------
def water_reserve(
    theta_surface: Optional[float],
    theta_root: Optional[float],
    soil_name: Optional[str],
    daily: List[Dict[str, Any]],
    *,
    crop: Optional[str] = None,
    as_of: Optional[date] = None,
    soilgrids: Optional[Dict[str, Any]] = None,
    recent_daily: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Liczba dni do początku stresu wodnego wg bilansu FAO-56 (z Kc i p dla uprawy oraz opcjonalnie SoilGrids)."""
    cls, recognized = classify_soil(soil_name)
    soil_source = "user" if recognized else "default"
    if not recognized and isinstance(soilgrids, dict) and soilgrids.get("available") and soilgrids.get("texture_key") in SOIL_TEXTURES:
        cls = soilgrids["texture_key"]
        recognized = True
        soil_source = "soilgrids"
    tex = SOIL_TEXTURES[cls]
    fc, wp = tex["fc"], tex["wp"]
    cpar = crop_water_params(crop, as_of.month if as_of is not None else None)
    kc = cpar["kc"]
    p_val = cpar["p"]
    theta = _combined(theta_surface, theta_root)
    taw = (fc - wp) * ROOT_DEPTH_MM
    result: Dict[str, Any] = {
        "days": None, "at_least": False, "estimated_from_recent_et": False,
        "soil_class": tex["label"], "soil_key": cls, "soil_recognized": recognized, "soil_source": soil_source,
        "drought_category": IUNG_DROUGHT_CATEGORIES[cls]["code"],
        "field_capacity_pct": round(fc * 100, 1), "wilting_point_pct": round(wp * 100, 1),
        "available_water_mm": None, "taw_mm": round(taw, 1), "raw_mm": round(taw * p_val, 1),
        "kc": kc, "depletion_p": p_val, "crop_recognized": cpar["crop_recognized"],
    }
    if theta is None:
        return result
    aw = min(taw, max(0.0, (theta - wp) * ROOT_DEPTH_MM))
    threshold = taw * (1.0 - p_val)
    result["available_water_mm"] = round(aw, 1)
    days_usable = [d for d in daily if d.get("et0_mm") is not None and d.get("rain_mm") is not None]
    if aw < threshold:
        result["days"] = 0
        return result
    if days_usable:
        for n, d in enumerate(days_usable):
            etc = d["et0_mm"] * kc
            aw = min(taw, aw + EFFECTIVE_RAIN * d["rain_mm"] - etc)
            if aw < threshold:
                result["days"] = n
                return result
        result["days"] = len(days_usable)
        result["at_least"] = True
        return result
    # Tryb historyczny (brak prognozy w przód – anti-leakage): szacunek dni zapasu wg średniego ETc z ostatnich 7 dni ≤ as_of
    if recent_daily:
        et_vals = [d["et0_mm"] * kc for d in recent_daily[-7:] if d.get("et0_mm") is not None]
        if et_vals:
            avg_etc = max(0.5, sum(et_vals) / len(et_vals))
            est_days = int((aw - threshold) / avg_etc)
            result["days"] = min(14, max(0, est_days))
            result["at_least"] = est_days >= 14
            result["estimated_from_recent_et"] = True
    return result


def soil_block(
    lat: float,
    lon: float,
    soil_name: Optional[str],
    daily: List[Dict[str, Any]],
    *,
    crop: Optional[str] = None,
    as_of: Optional[date] = None,
    soilgrids: Optional[Dict[str, Any]] = None,
    recent_daily: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[int], Dict[str, Any], Dict[str, Any], Optional[float], List[str]]:
    """(soil_moisture, water_reserve_days, prov_soil, prov_water, soil_temp_c, ostrzeżenia). Nigdy nie rzuca."""
    try:
        s = fetch_soil(lat, lon, as_of=as_of)
    except SourceError as exc:
        return soil_block_from(None, str(exc), soil_name, daily, crop=crop, as_of=as_of, soilgrids=soilgrids, recent_daily=recent_daily)
    return soil_block_from(s, None, soil_name, daily, crop=crop, as_of=as_of, soilgrids=soilgrids, recent_daily=recent_daily)


def soil_block_from(
    s: Optional[Dict[str, Any]],
    error: Optional[str],
    soil_name: Optional[str],
    daily: List[Dict[str, Any]],
    *,
    crop: Optional[str] = None,
    as_of: Optional[date] = None,
    soilgrids: Optional[Dict[str, Any]] = None,
    recent_daily: Optional[List[Dict[str, Any]]] = None,
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
    wr = water_reserve(
        s["surface"], s["root"], soil_name, daily,
        crop=crop, as_of=as_of, soilgrids=soilgrids, recent_daily=recent_daily,
    )
    sm.update(
        soil_class=wr["soil_class"],
        soil_source=wr["soil_source"],
        drought_category=wr["drought_category"],
        field_capacity_pct=wr["field_capacity_pct"],
        wilting_point_pct=wr["wilting_point_pct"],
        kc=wr["kc"],
        depletion_p=wr["depletion_p"],
    )
    # linie referencyjne pojemności polowej i punktu więdnięcia – wartości tabelaryczne (FAO-56), nie pomiar
    sm["reference_provenance"] = soil_reference_provenance(wr["soil_class"], wr["field_capacity_pct"], wr["wilting_point_pct"])
    # Ten sam wskaźnik co w HydroMelior: woda dostępna jako % zakresu użytecznego (θ − θWP)/(θFC − θWP)
    sm["available_water_pct"] = (None if wr["available_water_mm"] is None or not wr["taw_mm"]
                                 else round(wr["available_water_mm"] / wr["taw_mm"] * 100))
    if not wr["soil_recognized"]:
        warnings.append("Nie rozpoznano rodzaju gleby (ISRIC SoilGrids niedostępny) – do zapasu wody przyjęto glebę średnią (glina).")
    prov_soil = provenance(
        s["source"], "https://open-meteo.com/", s["kind"], observed_at=s.get("valid_at"), fetched_at=s.get("fetched_at"),
        resolution="2–11 km (komórka modelu)",
        note=s["note_src"] + " Wilgotność objętościowa [% obj.] dla komórki siatki, nie pomiar na działce. Dane: Open-Meteo.com, CC BY 4.0.",
    )
    if wr["days"] is None:
        days_txt = "brak danych ET0"
    elif wr["estimated_from_recent_et"]:
        days_txt = (f"ok. {wr['days']}+ dni" if wr["at_least"] else f"ok. {wr['days']} dni") + " (szacunek wg średniego ETc z ostatnich 7 dni, bez danych z przyszłości)"
    else:
        days_txt = f"co najmniej {wr['days']} dni (horyzont prognozy)" if wr["at_least"] else f"{wr['days']} dni"
    if wr["days"] == 0 and sm.get("available_water_pct") is not None:
        days_txt = f"0 dni – łatwo dostępna woda już wyczerpana (zostało ok. {sm['available_water_pct']}% wody użytecznej)"
    soil_src_txt = "wykryta automatycznie z ISRIC SoilGrids" if wr["soil_source"] == "soilgrids" else (
        "podana dla działki" if wr["soil_source"] == "user" else "domyślna średnia"
    )
    prov_water = provenance(
        "Model bilansu wodnego mRolnik (FAO-56)", "https://www.fao.org/3/x0490e/x0490e00.htm", "model",
        observed_at=s.get("valid_at"), fetched_at=s.get("fetched_at"),
        note=(f"Dni do stresu wodnego: {days_txt}. Warstwa 0–28 cm, gleba ({soil_src_txt}): {wr['soil_class']} "
              f"(kat. suszowa IUNG: {wr['drought_category']}, θFC {wr['field_capacity_pct']}%, θWP {wr['wilting_point_pct']}% – FAO-56). "
              f"Parametry uprawy: Kc={wr['kc']:.2f}, próg wyczerpania p={wr['depletion_p']:.2f} "
              f"(początek stresu poniżej {round((1 - wr['depletion_p']) * 100)}% wody dostępnej)."),
    )
    return sm, wr["days"], prov_soil, prov_water, s.get("soil_temp_c"), warnings

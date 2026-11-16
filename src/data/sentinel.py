"""Prawdziwe NDVI z Sentinel-2 L2A przez Copernicus Data Space Ecosystem (CDSE, Sentinel Hub API).

Przepływ dla działki:
1. OAuth2 client credentials → token (cache do wygaśnięcia).
2. Catalog API (STAC): zobrazowania sentinel-2-l2a nad działką z ostatnich 45 dni (data, zachmurzenie sceny).
3. Statistical API, interwał P1D: dla każdego dnia z danymi średnie/min/max/odchylenie NDVI po działce
   i udział pikseli bezchmurnych (maska SCL: cień chmur 3, chmury 8/9, cirrus 10, śnieg 11, brak danych 0/1).
   Wybieramy NAJNOWSZY dzień z ≥ 70% pikseli działki bez chmur.
4. Process API: raster NDVI (UINT8, 10 m) dla tego dnia, przycięty do granic działki → kolorowa nakładka PNG
   (numpy + PIL) i udziały klas (gleba odsłonięta / stres / dobry łan).
5. Historia: Statistical API, interwał P1M, 24 miesiące; dla piksela mediana bezchmurnych obserwacji w miesiącu.

Bez danych logowania CDSE albo przy błędzie: NDVI = null i proweniencja "brak". Nigdy nie liczymy
"pseudo-NDVI" z RGB i nie generujemy syntetycznych rastrów.
"""
from __future__ import annotations

import base64
import io
import logging
import math
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import requests
from PIL import Image
from pyproj import Transformer
from shapely.geometry import mapping

from src.core.cache import TTLCache
from src.core.config import settings
from src.data.cadastre import ParcelData, bbox_wgs84, reproject
from src.data.common import (
    friendly,
    SOURCE_CDSE,
    SourceError,
    iso_utc,
    missing,
    provenance,
    record_error,
    record_ok,
    to_float,
    user_agent,
    utcnow,
)

log = logging.getLogger(__name__)

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CATALOG_URL = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
STATS_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
COLLECTION = "sentinel-2-l2a"

SOURCE_NAME = "Copernicus Sentinel-2 L2A (Copernicus Data Space Ecosystem)"
SOURCE_URL = "https://dataspace.copernicus.eu/"
NOT_CONFIGURED_NOTE = ("NDVI niedostępne: skonfiguruj CDSE_CLIENT_ID/CDSE_CLIENT_SECRET (bezpłatne konto Copernicus "
                       "Data Space Ecosystem, instrukcja w docs/ZRODLA_DANYCH.md).")

LOOKBACK_DAYS = 45
MIN_VALID_FRACTION = 0.70
HISTORY_MONTHS = 24
CURRENT_TTL_S = 6 * 3600
HISTORY_TTL_S = 24 * 3600
TIMEOUT_S = 25.0
MAX_PX = 512

_current_cache = TTLCache("s2_current", max_entries=5000, default_ttl=CURRENT_TTL_S)
_history_cache = TTLCache("s2_history", max_entries=5000, default_ttl=HISTORY_TTL_S)
CLIMATOLOGY_YEARS = (2019, 2025)
CLIMATOLOGY_TTL_S = 30 * 24 * 3600
CLIMATOLOGY_MIN_YEARS = 2
_climatology_cache = TTLCache("s2_climatology", max_entries=5000, default_ttl=CLIMATOLOGY_TTL_S)
_CDSE_SEM = threading.BoundedSemaphore(4)  # ograniczenie równoległych żądań do CDSE (limity API)

# Klasy SCL wykluczane z analizy: 0 brak danych, 1 piksel wadliwy, 3 cień chmur, 8/9 chmury, 10 cirrus, 11 śnieg
SCL_MASKED = [0, 1, 3, 8, 9, 10, 11]

# Paleta nakładki NDVI (przedziały [od, do)) – opisana w docs/ZRODLA_DANYCH.md
NDVI_PALETTE: List[Dict[str, Any]] = [
    {"from": -1.00, "to": 0.00, "color": "#7f3b08", "label": "woda / brak roślin"},
    {"from": 0.00, "to": 0.25, "color": "#c48a3a", "label": "gleba odsłonięta"},
    {"from": 0.25, "to": 0.40, "color": "#f2d24b", "label": "roślinność rzadka / stres"},
    {"from": 0.40, "to": 0.50, "color": "#c7e36b", "label": "roślinność umiarkowana"},
    {"from": 0.50, "to": 0.65, "color": "#7cc24a", "label": "łan dobry"},
    {"from": 0.65, "to": 0.80, "color": "#2f9a3a", "label": "łan gęsty"},
    {"from": 0.80, "to": 1.01, "color": "#0b5d1e", "label": "łan bardzo gęsty"},
]
# Paleta NDMI = (B8A − B11)/(B8A + B11) – woda w roślinach i wierzchniej warstwie (nie wilgotność gleby w %)
NDMI_PALETTE: List[Dict[str, Any]] = [
    {"from": -1.00, "to": -0.20, "color": "#a50026", "label": "silny niedobór wody / gleba odkryta"},
    {"from": -0.20, "to": 0.00, "color": "#f46d43", "label": "niedobór wody w roślinach"},
    {"from": 0.00, "to": 0.20, "color": "#fee08b", "label": "umiarkowane uwodnienie"},
    {"from": 0.20, "to": 0.40, "color": "#74add1", "label": "dobre uwodnienie"},
    {"from": 0.40, "to": 1.01, "color": "#313695", "label": "wysokie uwodnienie"},
]
NDMI_NOTE = ("NDMI = (B8A − B11)/(B8A + B11) z tej samej sceny Sentinel-2 co NDVI (maska SCL). Pokazuje wodę w roślinach i w "
             "wierzchniej warstwie powierzchni – to nie jest wilgotność gleby w % ani zapas wody w strefie korzeni.")
BARE_SOIL_MAX = 0.25   # NDVI < 0,25 → gleba odsłonięta
HEALTHY_MIN = 0.50     # NDVI ≥ 0,50 → dobry łan; pomiędzy → stres / rzadka roślinność

_SCL_JS = "[" + ",".join(str(c) for c in SCL_MASKED) + "]"

# dataMask = zasięg działki (piksele poza geometrią i dni bez przelotu wypadają), a wyjście "cnt" mówi,
# ile pikseli działki jest bezchmurnych (B0) – średnia B0 to udział pikseli ważnych W DZIAŁCE, nie w jej bbox.
# NDVI = NaN dla pikseli zachmurzonych (Statistical API pomija NaN w mean/min/max).
EVALSCRIPT_STATS = f"""//VERSION=3
function setup() {{
  return {{
    input: [{{bands: ["B04", "B08", "SCL", "dataMask"]}}],
    output: [{{id: "ndvi", bands: 1, sampleType: "FLOAT32"}}, {{id: "cnt", bands: 1, sampleType: "UINT8"}},
             {{id: "dataMask", bands: 1}}]
  }};
}}
function evaluatePixel(s) {{
  var d = s.B08 + s.B04;
  var ok = s.dataMask === 1 && {_SCL_JS}.indexOf(s.SCL) === -1 && d > 0;
  var ndvi = ok ? (s.B08 - s.B04) / d : NaN;
  return {{ndvi: [ndvi], cnt: [ok ? 1 : 0], dataMask: [s.dataMask]}};
}}
"""

EVALSCRIPT_MONTHLY = f"""//VERSION=3
function setup() {{
  return {{
    input: [{{bands: ["B04", "B08", "SCL", "dataMask"]}}],
    output: [{{id: "ndvi", bands: 1, sampleType: "FLOAT32"}}, {{id: "cnt", bands: 1, sampleType: "UINT8"}},
             {{id: "dataMask", bands: 1}}],
    mosaicking: "ORBIT"
  }};
}}
function evaluatePixel(samples) {{
  var v = [];
  var inside = 0;
  for (var i = 0; i < samples.length; i++) {{
    var s = samples[i];
    if (s.dataMask === 1) inside = 1;
    var d = s.B08 + s.B04;
    if (s.dataMask !== 1 || {_SCL_JS}.indexOf(s.SCL) !== -1 || d <= 0) continue;
    v.push((s.B08 - s.B04) / d);
  }}
  if (v.length === 0) return {{ndvi: [NaN], cnt: [0], dataMask: [inside]}};
  v.sort(function (a, b) {{ return a - b; }});
  return {{ndvi: [v[Math.floor(v.length / 2)]], cnt: [1], dataMask: [inside]}};
}}
"""

# Kanały UINT8: R = NDVI, G = NDMI (0 = brak danych / chmura / poza działką; 1..255 = wartość z zakresu [-1, 1]), B = 0
EVALSCRIPT_PNG = f"""//VERSION=3
function setup() {{
  return {{input: [{{bands: ["B04", "B08", "B8A", "B11", "SCL", "dataMask"]}}], output: {{bands: 3, sampleType: "UINT8"}}}};
}}
function enc(v) {{ return 1 + Math.round((Math.max(-1, Math.min(1, v)) + 1) / 2 * 254); }}
function evaluatePixel(s) {{
  var d = s.B08 + s.B04;
  if (s.dataMask !== 1 || {_SCL_JS}.indexOf(s.SCL) !== -1 || d <= 0) return [0, 0, 0];
  var d2 = s.B8A + s.B11;
  return [enc((s.B08 - s.B04) / d), d2 > 0 ? enc((s.B8A - s.B11) / d2) : 0, 0];
}}
"""

# Zdjęcie w barwach naturalnych (B04/B03/B02 × 2,5), przezroczyste poza działką
EVALSCRIPT_TRUE_COLOR = """//VERSION=3
function setup() {
  return {input: [{bands: ["B02", "B03", "B04", "dataMask"]}], output: {bands: 4, sampleType: "UINT8"}};
}
function c(v) { return Math.max(0, Math.min(255, Math.round(v * 2.5 * 255))); }
function evaluatePixel(s) { return [c(s.B04), c(s.B03), c(s.B02), s.dataMask === 1 ? 255 : 0]; }
"""


# --------------------------------------------------------------------------------------
# OAuth2 + wywołania Sentinel Hub
# --------------------------------------------------------------------------------------
_TOKEN_LOCK = threading.Lock()

# Jedno pobieranie naraz dla danego klucza (np. historia tej samej działki): rozgrzewanie cache, pobieranie w tle
# i doczytywanie przez przeglądarkę nie wysyłają tego samego, wolnego zapytania do CDSE kilka razy – drugi czeka na wynik pierwszego.
_KEY_LOCKS: Dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        if len(_KEY_LOCKS) > 10000:
            _KEY_LOCKS.clear()
        return _KEY_LOCKS.setdefault(key, threading.Lock())
_TOKEN: Dict[str, Any] = {"value": None, "exp": 0.0}


def reset_token() -> None:
    with _TOKEN_LOCK:
        _TOKEN.update(value=None, exp=0.0)


def get_token() -> str:
    if not settings.cdse_configured:
        raise SourceError("brak CDSE_CLIENT_ID/CDSE_CLIENT_SECRET")
    if settings.offline_demo:
        raise SourceError("tryb offline")
    with _TOKEN_LOCK:
        if _TOKEN["value"] and time.time() < _TOKEN["exp"] - 60:
            return _TOKEN["value"]
        try:
            r = requests.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials", "client_id": settings.cdse_client_id,
                      "client_secret": settings.cdse_client_secret},
                headers={"User-Agent": user_agent()}, timeout=10,
            )
        except requests.RequestException as exc:
            raise SourceError(f"CDSE token: {exc}") from exc
        if r.status_code != 200:
            raise SourceError(f"CDSE token: HTTP {r.status_code} (sprawdź CDSE_CLIENT_ID/SECRET)")
        try:
            data = r.json()
            tok = str(data["access_token"])
            exp = float(data.get("expires_in", 600))
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceError("CDSE token: niepoprawna odpowiedź") from exc
        _TOKEN.update(value=tok, exp=time.time() + exp)
        return tok


def _sh_post(url: str, body: Dict[str, Any], accept: str = "application/json", timeout: float = TIMEOUT_S) -> requests.Response:
    """POST do Sentinel Hub z tokenem; przy 401 odświeża token i ponawia raz; przy 429/5xx ponawia raz."""
    for attempt in range(2):
        tok = get_token()
        headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept": accept,
                   "User-Agent": user_agent()}
        try:
            with _CDSE_SEM:
                r = requests.post(url, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == 0:
                continue
            raise SourceError(f"{url}: {exc}") from exc
        if r.status_code == 200:
            return r
        detail = (getattr(r, "text", "") or "")[:300]
        log.warning("CDSE %s HTTP %s: %s", url.rsplit("/", 1)[-1], r.status_code, detail)
        if r.status_code == 401 and attempt == 0:
            reset_token()
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            time.sleep(1.0)
            continue
        raise SourceError(f"CDSE HTTP {r.status_code}")
    raise SourceError("CDSE: nieudane żądanie")


# --------------------------------------------------------------------------------------
# Geometria
# --------------------------------------------------------------------------------------
def utm_epsg(lon: float, lat: float) -> int:
    zone = int(math.floor((lon + 180) / 6)) + 1
    return (32600 if lat >= 0 else 32700) + zone


def geometry_utm(parcel: ParcelData) -> Tuple[Dict[str, Any], str]:
    epsg = utm_epsg(parcel.centroid_lon, parcel.centroid_lat)
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    g = reproject(parcel.polygon_wgs84, tr)
    return mapping(g), f"http://www.opengis.net/def/crs/EPSG/0/{epsg}"


def raster_size(bbox: Tuple[float, float, float, float], res_m: float = 10.0) -> Tuple[int, int]:
    w, s, e, n = bbox
    lat = (s + n) / 2
    w_m = (e - w) * 111320.0 * math.cos(math.radians(lat))
    h_m = (n - s) * 110574.0
    return (max(4, min(MAX_PX, math.ceil(w_m / res_m))), max(4, min(MAX_PX, math.ceil(h_m / res_m))))


# --------------------------------------------------------------------------------------
# Catalog / Statistics / Process
# --------------------------------------------------------------------------------------
def catalog_search(bbox: Tuple[float, float, float, float], start: datetime, end: datetime) -> Dict[str, Dict[str, Any]]:
    """Zobrazowania pogrupowane po dniu: {YYYY-MM-DD: {"datetime", "cloud_cover"}}."""
    body = {
        "bbox": list(bbox),
        "datetime": f"{iso_utc(start)}/{iso_utc(end)}",
        "collections": [COLLECTION],
        "limit": 100,
        "filter": "eo:cloud_cover <= 100",
        "filter-lang": "cql2-text",
        "fields": {"include": ["id", "properties.datetime", "properties.eo:cloud_cover"]},
    }
    # Catalog API (STAC) odpowiada wyłącznie jako application/geo+json – Accept: application/json daje HTTP 406
    data = _sh_post(CATALOG_URL, body, accept="application/geo+json").json()
    feats = data.get("features") if isinstance(data, dict) else None
    if not isinstance(feats, list):
        raise SourceError("Catalog API: brak listy features")
    by_day: Dict[str, Dict[str, Any]] = {}
    for f in feats:
        props = (f or {}).get("properties") or {}
        dt = str(props.get("datetime") or "")
        if len(dt) < 10:
            continue
        day = dt[:10]
        cc = to_float(props.get("eo:cloud_cover"))
        entry = by_day.setdefault(day, {"datetime": dt, "covers": []})
        if cc is not None:
            entry["covers"].append(cc)
    out = {}
    for day, e in by_day.items():
        out[day] = {"datetime": e["datetime"], "cloud_cover": (sum(e["covers"]) / len(e["covers"])) if e["covers"] else None}
    return out


def parse_stats(data: Any) -> List[Dict[str, Any]]:
    """Wyniki Statistical API → lista interwałów z mean/min/max/std i udziałem pikseli ważnych."""
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise SourceError("Statistical API: niepoprawna odpowiedź")
    out = []
    for item in data["data"]:
        if not isinstance(item, dict) or item.get("error"):
            continue
        frm = str(((item.get("interval") or {}).get("from")) or "")
        try:
            st = item["outputs"]["ndvi"]["bands"]["B0"]["stats"]
        except (KeyError, TypeError):
            continue
        n = to_float(st.get("sampleCount")) or 0.0
        nod = to_float(st.get("noDataCount")) or 0.0
        try:
            cnt = item["outputs"]["cnt"]["bands"]["B0"]["stats"]
        except (KeyError, TypeError):
            cnt = None
        if cnt is not None:
            # dataMask = zasięg działki → (n − nod) pikseli w działce; średnia "cnt" = udział bezchmurnych
            n = n - nod
            valid = to_float(cnt.get("mean")) or 0.0 if n > 0 else 0.0
        else:
            valid = (n - nod) / n if n > 0 else 0.0
        out.append({
            "from": frm, "date": frm[:10], "mean": to_float(st.get("mean")), "min": to_float(st.get("min")),
            "max": to_float(st.get("max")), "std": to_float(st.get("stDev")), "valid_frac": valid, "samples": int(n),
        })
    return out


def stats_request(parcel: ParcelData, start: datetime, end: datetime, interval: str, evalscript: str,
                  mosaicking_order: Optional[str] = "mostRecent") -> List[Dict[str, Any]]:
    geom, crs = geometry_utm(parcel)
    data_spec: Dict[str, Any] = {"type": COLLECTION}
    if mosaicking_order:
        data_spec["dataFilter"] = {"mosaickingOrder": mosaicking_order}
    body = {
        "input": {"bounds": {"geometry": geom, "properties": {"crs": crs}}, "data": [data_spec]},
        "aggregation": {
            "timeRange": {"from": iso_utc(start), "to": iso_utc(end)},
            "aggregationInterval": {"of": interval, "lastIntervalBehavior": "SHORTEN"},
            "evalscript": evalscript,
            "resx": 10, "resy": 10,
        },
    }
    return parse_stats(_sh_post(STATS_URL, body, timeout=30.0).json())


def process_ndvi_png(parcel: ParcelData, day: str, bbox: Tuple[float, float, float, float], evalscript: Optional[str] = None) -> bytes:
    width, height = raster_size(bbox)
    body = {
        "input": {
            "bounds": {"bbox": list(bbox), "geometry": mapping(parcel.polygon_wgs84), "properties": {"crs": CRS84}},
            "data": [{"type": COLLECTION, "dataFilter": {
                "timeRange": {"from": f"{day}T00:00:00Z", "to": f"{day}T23:59:59Z"}, "mosaickingOrder": "mostRecent"}}],
        },
        "output": {"width": width, "height": height, "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
        "evalscript": evalscript or EVALSCRIPT_PNG,
    }
    r = _sh_post(PROCESS_URL, body, accept="image/png")
    content = r.content or b""
    if not content.startswith(b"\x89PNG"):
        raise SourceError("Process API: odpowiedź nie jest obrazem PNG")
    return content


# --------------------------------------------------------------------------------------
# Raster → statystyki klas i kolorowa nakładka
# --------------------------------------------------------------------------------------
def decode_ndvi_png(png: bytes, channel: int = 0) -> np.ndarray:
    """Kanał PNG (UINT8, 0 = brak, 1..255 = [-1, 1]) → tablica wartości z NaN. channel 0 = NDVI, 1 = NDMI."""
    img = Image.open(io.BytesIO(png))
    if img.mode in ("LA", "RGBA", "RGB"):
        colour = len(img.getbands()) - (1 if img.mode in ("LA", "RGBA") else 0)
        if channel >= colour:
            raise ValueError("brak kanału w rastrze")
        img = img.getchannel(channel)
    elif channel != 0:
        raise ValueError("raster jednokanałowy – brak kanału NDMI")
    elif img.mode not in ("L", "P"):
        img = img.convert("L")
    arr = np.asarray(img, dtype=np.float32)
    ndvi = (arr - 1.0) / 254.0 * 2.0 - 1.0
    ndvi[arr == 0] = np.nan
    return ndvi


def class_fractions(ndvi: np.ndarray) -> Optional[Dict[str, float]]:
    v = ndvi[np.isfinite(ndvi)]
    if v.size == 0:
        return None
    n = float(v.size)
    bare = float(np.count_nonzero(v < BARE_SOIL_MAX)) / n * 100
    healthy = float(np.count_nonzero(v >= HEALTHY_MIN)) / n * 100
    return {"healthy_pct": round(healthy, 1), "stress_pct": round(100 - healthy - bare, 1), "bare_soil_pct": round(bare, 1)}


def _hex_rgb(h: str) -> Tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def render_overlay(ndvi: np.ndarray, palette: Optional[List[Dict[str, Any]]] = None) -> str:
    """Kolorowa nakładka RGBA (piksele bez danych przezroczyste) jako data URI PNG."""
    palette = palette or NDVI_PALETTE
    h, w = ndvi.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    valid = np.isfinite(ndvi)
    edges = np.array([c["to"] for c in palette[:-1]], dtype=np.float32)
    idx = np.digitize(np.where(valid, ndvi, 0.0), edges)
    colors = np.array([_hex_rgb(c["color"]) for c in palette], dtype=np.uint8)
    rgba[..., :3] = colors[idx]
    rgba[..., 3] = np.where(valid, 230, 0).astype(np.uint8)
    img = Image.fromarray(rgba, mode="RGBA")
    scale = max(1, min(8, 256 // max(h, w)))
    if scale > 1:
        img = img.resize((w * scale, h * scale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# --------------------------------------------------------------------------------------
# API modułu
# --------------------------------------------------------------------------------------
def _brak(note: str) -> Dict[str, Any]:
    return {"ndvi": None, "layer": None, "provenance": missing(SOURCE_NAME, SOURCE_URL, note), "warnings": [note]}


def analyze_current(parcel: ParcelData) -> Dict[str, Any]:
    """NDVI z najnowszego bezchmurnego zobrazowania. Zwraca {"ndvi", "layer", "provenance", "warnings"}; nie rzuca."""
    if not settings.cdse_configured:
        return {"ndvi": None, "layer": None, "provenance": missing(SOURCE_NAME, SOURCE_URL, NOT_CONFIGURED_NOTE), "warnings": []}
    key = parcel.geometry_key() + ":v2"   # v2: warstwy NDMI i zdjęcie w barwach naturalnych
    cached = _current_cache.get(key)
    if cached:
        return cached
    end = utcnow()
    # Interwały P1D liczone od "from": start o północy UTC, żeby etykieta dnia = dzień zobrazowania
    sd = (end - timedelta(days=LOOKBACK_DAYS)).date()
    start = datetime(sd.year, sd.month, sd.day, tzinfo=timezone.utc)
    bbox = bbox_wgs84(parcel)
    warnings: List[str] = []
    try:
        try:
            scenes = catalog_search(bbox, start, end)
        except SourceError as exc:
            scenes = {}
            warnings.append(f"Katalog zobrazowań CDSE niedostępny ({friendly(exc)}) – brak metadanych zachmurzenia sceny.")
        daily = stats_request(parcel, start, end, "P1D", EVALSCRIPT_STATS)
    except SourceError as exc:
        record_error(SOURCE_CDSE, str(exc))
        return _brak(f"Nie udało się pobrać danych Sentinel-2 z CDSE ({friendly(exc)}).")
    record_ok(SOURCE_CDSE)
    usable = sorted((d for d in daily if d["samples"] > 0 and d["mean"] is not None), key=lambda d: d["date"], reverse=True)
    chosen = next((d for d in usable if d["valid_frac"] >= MIN_VALID_FRACTION), None)
    if chosen is None:
        best = max((d["valid_frac"] for d in usable), default=0.0)
        res = _brak(f"Brak zobrazowania Sentinel-2 z co najmniej {int(MIN_VALID_FRACTION * 100)}% działki bez chmur w ostatnich "
                    f"{LOOKBACK_DAYS} dniach (najlepsze: {best * 100:.0f}%). Jesienią i zimą to częste – spróbuj później.")
        _current_cache.set(key, res, 3 * 3600)
        return res
    day = chosen["date"]
    scene = scenes.get(day, {})
    ndvi_stats: Dict[str, Any] = {
        "mean": round(chosen["mean"], 3),
        "min": None if chosen["min"] is None else round(chosen["min"], 3),
        "max": None if chosen["max"] is None else round(chosen["max"], 3),
        "std": None if chosen["std"] is None else round(chosen["std"], 3),
        "healthy_pct": None, "stress_pct": None, "bare_soil_pct": None,
        "valid_pct": round(chosen["valid_frac"] * 100, 1),
        "acquired_on": day,
    }
    layer = layer_ndmi = None
    w, s, e, n = bbox
    try:
        png = process_ndvi_png(parcel, day, bbox)
        raster = decode_ndvi_png(png)
        fr = class_fractions(raster)
        if fr:
            ndvi_stats.update(fr)
        layer = {"image": render_overlay(raster), "bounds": [[s, w], [n, e]], "legend": NDVI_PALETTE[:-1] + [dict(NDVI_PALETTE[-1], to=1.0)],
                 "acquired_on": day}
        try:
            ndmi = decode_ndvi_png(png, channel=1)
            vals = ndmi[np.isfinite(ndmi)]
            if vals.size:
                layer_ndmi = {"image": render_overlay(ndmi, NDMI_PALETTE), "bounds": [[s, w], [n, e]],
                              "legend": NDMI_PALETTE[:-1] + [dict(NDMI_PALETTE[-1], to=1.0)], "mean": round(float(vals.mean()), 3),
                              "acquired_on": day, "note": NDMI_NOTE}
        except (OSError, ValueError) as exc:
            log.info("NDMI niedostępne: %s", exc)
    except (SourceError, OSError, ValueError) as exc:
        warnings.append(f"Nie udało się pobrać rastra NDVI z CDSE ({friendly(exc)}) – pokazano tylko statystyki działki.")
    age = (end.date() - date.fromisoformat(day)).days
    if age > 20:
        warnings.append(f"Najnowsze bezchmurne zobrazowanie ma {age} dni – stan łanu mógł się zmienić.")
    prov = provenance(
        SOURCE_NAME, SOURCE_URL, "pomiar",
        observed_at=scene.get("datetime") or day, fetched_at=iso_utc(end),
        cloud_cover_pct=scene.get("cloud_cover"), resolution="10 m",
        note=(f"Najnowsze zobrazowanie z ostatnich {LOOKBACK_DAYS} dni z ≥{int(MIN_VALID_FRACTION * 100)}% działki bez chmur "
              f"(tu: {chosen['valid_frac'] * 100:.0f}% pikseli ważnych). NDVI = (B08 − B04)/(B08 + B04), maska chmur/cieni/śniegu SCL. "
              f"Zachmurzenie podane dla całej sceny. Zawiera zmodyfikowane dane Copernicus Sentinel {day[:4]}."),
    )
    res = {"ndvi": ndvi_stats, "layer": layer, "layer_ndmi": layer_ndmi, "layer_true_color": None,
           "provenance": prov, "warnings": warnings}
    _current_cache.set(key, res, CURRENT_TTL_S)
    return res


def _month_start(d: date, months_back: int) -> date:
    y, m = d.year, d.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def monthly_history(parcel: ParcelData) -> Optional[Dict[str, Any]]:
    """Miesięczna historia NDVI (24 mies.) → {"ndvi_monthly": [...], "provenance"} albo None (brak danych)."""
    if not settings.cdse_configured:
        return None
    key = parcel.geometry_key()
    cached = _history_cache.get(key)
    if cached:
        return cached
    with _key_lock("hist:" + key):
        cached = _history_cache.get(key)  # mogło dojść, gdy czekaliśmy na inne pobieranie
        return cached if cached else _monthly_history_fetch(parcel, key)


def _monthly_history_fetch(parcel: ParcelData, key: str) -> Optional[Dict[str, Any]]:
    end = utcnow()
    start_d = _month_start(end.date(), HISTORY_MONTHS - 1)
    start = datetime(start_d.year, start_d.month, 1, tzinfo=timezone.utc)
    try:
        rows = stats_request(parcel, start, end, "P1M", EVALSCRIPT_MONTHLY, mosaicking_order=None)
    except SourceError as exc:
        record_error(SOURCE_CDSE, str(exc))
        log.info("Historia NDVI niedostępna: %s", exc)
        return None
    record_ok(SOURCE_CDSE)
    months = []
    for r in sorted(rows, key=lambda r: r["date"]):
        ok = r["mean"] is not None and r["valid_frac"] >= 0.5
        months.append({"month": r["date"][:7], "mean": round(r["mean"], 3) if ok else None,
                       "valid_pct": round(r["valid_frac"] * 100, 1)})
    if not any(m["mean"] is not None for m in months):
        return None
    res = {
        "ndvi_monthly": months,
        "provenance": provenance(
            SOURCE_NAME, SOURCE_URL, "pomiar", fetched_at=iso_utc(end), resolution="10 m",
            note=("Miesięczny kompozyt NDVI: dla każdego piksela mediana bezchmurnych obserwacji Sentinel-2 w miesiącu, "
                  "uśredniona po działce. Miesiące z <50% pikseli bez chmur mają wartość null. "
                  "Zawiera zmodyfikowane dane Copernicus Sentinel."),
        ),
    }
    _history_cache.set(key, res, HISTORY_TTL_S)
    return res


def ndvi_climatology(parcel: ParcelData) -> Dict[str, Any]:
    """Średnie wieloletnie NDVI (2019–2025) dla każdego miesiąca kalendarzowego dla tej samej działki.

    Jedno zapytanie Statistical API (P1M, 84 miesiące, ten sam kompozyt co historia), cache 30 dni.
    Średnia miesiąca liczona z lat, w których ≥50% pikseli było bez chmur; wymagane ≥2 lata. Nigdy nie rzuca.
    """
    y0, y1 = CLIMATOLOGY_YEARS
    period = f"{y0}–{y1}"
    if not settings.cdse_configured:
        return {"period": period, "months": [], "provenance": missing(SOURCE_NAME, SOURCE_URL, NOT_CONFIGURED_NOTE)}
    key = parcel.geometry_key()
    cached = _climatology_cache.get(key)
    if cached:
        return cached
    start = datetime(y0, 1, 1, tzinfo=timezone.utc)
    end = datetime(y1, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    try:
        rows = stats_request(parcel, start, end, "P1M", EVALSCRIPT_MONTHLY, mosaicking_order=None)
    except SourceError as exc:
        record_error(SOURCE_CDSE, str(exc))
        return {"period": period, "months": [], "provenance": missing(SOURCE_NAME, SOURCE_URL, f"Średnia wieloletnia NDVI niedostępna ({friendly(exc)}).")}
    record_ok(SOURCE_CDSE)
    by_month: Dict[int, List[float]] = {m: [] for m in range(1, 13)}
    for r in rows:
        try:
            m = int(r["date"][5:7])
        except (ValueError, TypeError):
            continue
        if r["mean"] is not None and r["valid_frac"] >= 0.5 and 1 <= m <= 12:
            by_month[m].append(r["mean"])
    months = []
    for m in range(1, 13):
        v = by_month[m]
        ok = len(v) >= CLIMATOLOGY_MIN_YEARS
        months.append({"month": m, "mean": round(sum(v) / len(v), 3) if ok else None, "n_years": len(v),
                       "min": round(min(v), 3) if ok else None, "max": round(max(v), 3) if ok else None})
    res = {
        "period": period,
        "months": months,
        "provenance": provenance(
            SOURCE_NAME + " – średnia wieloletnia", SOURCE_URL, "statystyka", fetched_at=iso_utc(), resolution="10 m",
            note=(f"Średnia NDVI dla tego samego miesiąca kalendarzowego z lat {period} dla tej działki (miesięczne kompozyty "
                  "Sentinel-2 – mediana bezchmurnych obserwacji piksela – uśrednione po działce). Miesiąc liczony, gdy "
                  f"≥50% pikseli było bez chmur; średnia tylko z ≥{CLIMATOLOGY_MIN_YEARS} lat. Program Copernicus (UE). "
                  "Zawiera zmodyfikowane dane Copernicus Sentinel."),
        ),
    }
    if not any(m["mean"] is not None for m in months):
        res["provenance"] = missing(SOURCE_NAME, SOURCE_URL, f"Brak bezchmurnych zdjęć Sentinel-2 z lat {period} wystarczających do średniej wieloletniej.")
    else:
        _climatology_cache.set(key, res, CLIMATOLOGY_TTL_S)
    return res


_true_color_cache = TTLCache("s2_true_color", max_entries=5000, default_ttl=CURRENT_TTL_S)


def cached_history(parcel: ParcelData) -> Optional[Dict[str, Any]]:
    """Historia NDVI tylko z cache (bez zapytań) – do szybkiej odpowiedzi /api/analyze."""
    if not settings.cdse_configured:
        return None
    return _history_cache.get(parcel.geometry_key())


def true_color_layer(parcel: ParcelData) -> Dict[str, Any]:
    """Zdjęcie Sentinel-2 w barwach naturalnych z tej samej sceny co bieżące NDVI (osobne zapytanie, wczytywane w tle).

    Zwraca {"layer": {...} | None, "warnings": [...]}; nigdy nie rzuca. Cache 6 h (klucz: geometria + dzień zobrazowania)."""
    cur = analyze_current(parcel)
    day = (cur.get("ndvi") or {}).get("acquired_on")
    if not day:
        return {"layer": None, "warnings": ["Brak bezchmurnego zobrazowania – nie ma zdjęcia do pokazania."]}
    key = f"{parcel.geometry_key()}:{day}"
    cached = _true_color_cache.get(key)
    if cached:
        return cached
    with _key_lock("tc:" + key):
        cached = _true_color_cache.get(key)
        return cached if cached else _true_color_fetch(parcel, day, key)


def _true_color_fetch(parcel: ParcelData, day: str, key: str) -> Dict[str, Any]:
    bbox = bbox_wgs84(parcel)
    w, s, e, n = bbox
    try:
        tc = process_ndvi_png(parcel, day, bbox, EVALSCRIPT_TRUE_COLOR)
    except (SourceError, OSError, ValueError) as exc:
        return {"layer": None, "warnings": [f"Nie udało się pobrać zdjęcia Sentinel-2 w barwach naturalnych ({friendly(exc)})."]}
    res = {"layer": {"image": "data:image/png;base64," + base64.b64encode(tc).decode("ascii"), "bounds": [[s, w], [n, e]],
                     "acquired_on": day}, "warnings": []}
    _true_color_cache.set(key, res, CURRENT_TTL_S)
    return res


def probe() -> Tuple[bool, str]:
    """Lekki test konfiguracji CDSE (pobranie tokenu) dla /api/sources/status?check=true."""
    if not settings.cdse_configured:
        return False, "Nieskonfigurowane: ustaw CDSE_CLIENT_ID i CDSE_CLIENT_SECRET"
    try:
        get_token()
    except SourceError as exc:
        record_error(SOURCE_CDSE, str(exc))
        return False, str(exc)
    record_ok(SOURCE_CDSE, "Token OAuth2 uzyskany")
    return True, "Token OAuth2 uzyskany"

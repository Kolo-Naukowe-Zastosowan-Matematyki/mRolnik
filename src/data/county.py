"""Działka na tle powiatu: NDVI działki w porównaniu z polami uprawnymi całego powiatu z tego samego dnia.

Przepływ:
1. GUGiK ULDK GetCountyByXY → TERYT, nazwa i granica powiatu (upraszczana do ok. 150 m).
2. Maska pól uprawnych (raz na sezon i powiat): Sentinel-2 L2A z sezonu 15.04–31.10 ostatniego zakończonego roku,
   kwadraty 100 m (1 ha). Dla każdego kwadratu 10. i 90. percentyl bezchmurnych NDVI: pole uprawne to miejsce, które
   w sezonie było i gołe (NDVI < 0,3 – przed wschodami albo po zbiorze), i zielone (NDVI > 0,55). Łąki, lasy, woda
   i zabudowa takiego cyklu nie mają, więc są pomijane. To przybliżenie, nie urzędowa mapa upraw.
3. NDVI powiatu z dnia zdjęcia działki (maska chmur SCL) → rozkład NDVI na polach uprawnych, mediana i miejsce działki.

Bez CDSE albo przy za małej liczbie bezchmurnych pól: status "brak" i proweniencja z wyjaśnieniem. Nic nie zmyślamy.
"""
from __future__ import annotations

import base64
import io
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import shapely
from PIL import Image
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping

from src.core.cache import TTLCache
from src.core.config import settings
from src.data import sentinel
from src.data.common import (
    SOURCE_CDSE,
    SOURCE_ULDK,
    SourceError,
    friendly,
    http_request,
    iso_utc,
    missing,
    provenance,
    record_error,
    record_ok,
)

log = logging.getLogger(__name__)

ULDK_URL = "https://uldk.gugik.gov.pl/"
COUNTY_TTL_S = 90 * 24 * 3600
MASK_TTL_S = 120 * 24 * 3600
DAY_TTL_S = 14 * 24 * 3600
RES_M = 100.0
MAX_PX = 1200
MIN_PX = 500             # min. kwadratów 1 ha pól uprawnych z bezchmurnym NDVI w dniu zdjęcia
SEGMENTS = 20
CROP_LOW_MAX = 0.30      # 10. percentyl NDVI w sezonie poniżej → bywało gołe
CROP_HIGH_MIN = 0.55     # 90. percentyl NDVI w sezonie powyżej → bywało zielone
MIN_OBS = 4

_county_cache = TTLCache("county_geom", max_entries=20000, default_ttl=COUNTY_TTL_S)
_mask_cache = TTLCache("county_mask", max_entries=500, default_ttl=MASK_TTL_S)
_result_cache = TTLCache("county_ndvi", max_entries=5000, default_ttl=DAY_TTL_S)
_monthly_cache = TTLCache("county_monthly", max_entries=20000, default_ttl=365 * 24 * 3600)

MASK_EVALSCRIPT = f"""//VERSION=3
function setup() {{
  return {{input: [{{bands: ["B04", "B08", "SCL", "dataMask"]}}], output: {{bands: 1, sampleType: "UINT8"}}, mosaicking: "ORBIT"}};
}}
function q(a, p) {{ return a[Math.min(a.length - 1, Math.max(0, Math.round(p * (a.length - 1))))]; }}
function evaluatePixel(samples) {{
  var v = [], inside = 0;
  for (var i = 0; i < samples.length; i++) {{
    var s = samples[i];
    if (s.dataMask === 1) inside = 1;
    if (s.dataMask !== 1 || {sentinel._SCL_JS}.indexOf(s.SCL) !== -1 || s.B08 + s.B04 <= 0) continue;
    v.push((s.B08 - s.B04) / (s.B08 + s.B04));
  }}
  if (!inside) return [0];
  if (v.length < {MIN_OBS}) return [1];
  v.sort(function (a, b) {{ return a - b; }});
  return [q(v, 0.1) < {CROP_LOW_MAX} && q(v, 0.9) > {CROP_HIGH_MIN} ? 3 : 2];
}}
"""

DAY_EVALSCRIPT = f"""//VERSION=3
function setup() {{
  return {{input: [{{bands: ["B04", "B08", "SCL", "dataMask"]}}], output: {{bands: 1, sampleType: "UINT8"}}}};
}}
function evaluatePixel(s) {{
  if (s.dataMask !== 1 || {sentinel._SCL_JS}.indexOf(s.SCL) !== -1 || s.B08 + s.B04 <= 0) return [0];
  var n = Math.max(-1, Math.min(1, (s.B08 - s.B04) / (s.B08 + s.B04)));
  return [1 + Math.round((n + 1) / 2 * 254)];
}}
"""


# --------------------------------------------------------------------------------------
# Powiat z ULDK
# --------------------------------------------------------------------------------------
def parse_county_response(text: str) -> Optional[Dict[str, Any]]:
    """'0\\nTERYT|nazwa|SRID=4326;MULTIPOLYGON(...)' → {teryt, name, wkt (uproszczony, WGS84)}; None gdy brak wyniku."""
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if not lines:
        raise ValueError("pusta odpowiedź ULDK")
    if lines[0] != "0" or len(lines) < 2:
        return None
    parts = lines[1].split("|")
    if len(parts) < 3:
        raise ValueError("niepełny rekord ULDK (powiat)")
    teryt, name, raw = parts[0].strip(), parts[1].strip(), parts[2].strip()
    if not re.fullmatch(r"\d{4}", teryt):
        raise ValueError("niepoprawny TERYT powiatu")
    if raw.upper().startswith("SRID="):
        head, _, raw = raw.partition(";")
        if head.split("=", 1)[1].strip() != "4326":
            raise ValueError("oczekiwano geometrii w EPSG:4326")
    geom = shapely_wkt.loads(raw)
    simple = geom.simplify(0.0015, preserve_topology=True)
    if simple.is_empty or not simple.is_valid:
        simple = geom.buffer(0)
    return {"teryt": teryt, "name": name, "wkt": shapely_wkt.dumps(simple, rounding_precision=5)}


def county_at(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    key = f"{lat:.3f},{lon:.3f}"
    cached = _county_cache.get(key)
    if cached:
        return cached
    params = {"request": "GetCountyByXY", "xy": f"{lon:.6f},{lat:.6f},4326", "result": "teryt,county,geom_wkt", "srid": "4326"}
    try:
        resp = http_request("GET", ULDK_URL, params=params, timeout=15.0, retries=1)
        resp.encoding = resp.encoding or "utf-8"
        res = parse_county_response(resp.text)
    except (SourceError, ValueError, shapely.errors.ShapelyError) as exc:
        record_error(SOURCE_ULDK, f"Błąd ULDK (powiat): {friendly(exc)}")
        raise SourceError(str(exc)) from exc
    record_ok(SOURCE_ULDK)
    if res:
        _county_cache.set(key, res, COUNTY_TTL_S)
    return res


# --------------------------------------------------------------------------------------
# Rastry CDSE na siatce powiatu
# --------------------------------------------------------------------------------------
def season_for(day: date) -> int:
    """Ostatni zakończony sezon wegetacyjny (15.04–31.10)."""
    return day.year if day.month >= 11 else day.year - 1


def grid(bbox: Tuple[float, float, float, float]) -> Tuple[int, int]:
    w, s, e, n = bbox
    lat = (s + n) / 2
    w_m = (e - w) * 111320.0 * math.cos(math.radians(lat))
    h_m = (n - s) * 110574.0
    res = max(RES_M, max(w_m, h_m) / MAX_PX)
    return max(8, math.ceil(w_m / res)), max(8, math.ceil(h_m / res))


def _process(geom, frm: str, to: str, evalscript: str, extra: Optional[Dict[str, Any]] = None, timeout: float = 90.0,
             bands: int = 1) -> np.ndarray:
    bbox = geom.bounds
    width, height = grid(bbox)
    data_filter: Dict[str, Any] = {"timeRange": {"from": frm, "to": to}}
    data_filter.update(extra or {})
    body = {
        "input": {
            "bounds": {"bbox": list(bbox), "geometry": mapping(geom), "properties": {"crs": sentinel.CRS84}},
            "data": [{"type": sentinel.COLLECTION, "dataFilter": data_filter}],
        },
        "output": {"width": width, "height": height, "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
        "evalscript": evalscript,
    }
    r = sentinel._sh_post(sentinel.PROCESS_URL, body, accept="image/png", timeout=timeout)
    content = r.content or b""
    if not content.startswith(b"\x89PNG"):
        raise SourceError("Process API: odpowiedź nie jest obrazem PNG")
    arr = _png_decode(content)
    return arr if bands > 1 else arr[..., 0]


def _png_decode(content: bytes) -> np.ndarray:
    """PNG → tablica (wys., szer., kanały) UINT8 z surowymi wartościami kanałów (także kanału alfa)."""
    img = Image.open(io.BytesIO(content))
    if img.mode == "P":
        img = img.convert("L")
    arr = np.asarray(img, dtype=np.uint8)
    return arr[..., None] if arr.ndim == 2 else arr


def _crop_mask(county: Dict[str, Any], season: int) -> np.ndarray:
    key = f"{county['teryt']}:{season}:v1"
    with sentinel._key_lock(f"county_mask:{key}"):
        cached = _mask_cache.get(key)
        if cached:
            return _png_decode(base64.b64decode(cached["png"]))[..., 0]
        geom = shapely_wkt.loads(county["wkt"])
        mask = _process(geom, f"{season}-04-15T00:00:00Z", f"{season}-10-31T23:59:59Z", MASK_EVALSCRIPT,
                        {"maxCloudCoverage": 40, "mosaickingOrder": "leastCC"}, timeout=150.0)
        if np.count_nonzero(mask == 3):
            buf = io.BytesIO()
            Image.fromarray(mask, mode="L").save(buf, format="PNG", optimize=True)
            _mask_cache.set(key, {"png": base64.b64encode(buf.getvalue()).decode("ascii")}, MASK_TTL_S)
        return mask


def _day_ndvi(county: Dict[str, Any], day: str) -> np.ndarray:
    geom = shapely_wkt.loads(county["wkt"])
    raw = _process(geom, f"{day}T00:00:00Z", f"{day}T23:59:59Z", DAY_EVALSCRIPT, {"mosaickingOrder": "mostRecent"}, timeout=90.0)
    vals = (raw.astype(np.float32) - 1.0) / 254.0 * 2.0 - 1.0
    vals[raw == 0] = np.nan
    return vals


# --------------------------------------------------------------------------------------
# Statystyka
# --------------------------------------------------------------------------------------
def _color(v: float) -> str:
    for c in sentinel.NDVI_PALETTE:
        if c["from"] <= v < c["to"]:
            return c["color"]
    return sentinel.NDVI_PALETTE[-1]["color"] if v >= 0 else sentinel.NDVI_PALETTE[0]["color"]


def compare(mask: np.ndarray, ndvi: np.ndarray, value: Optional[float], px_ha: float = 1.0) -> Optional[Dict[str, Any]]:
    """Rozkład NDVI na polach uprawnych (maska == 3) i miejsce wartości działki. None gdy za mało danych."""
    if mask.shape != ndvi.shape:
        raise ValueError("różne siatki maski i NDVI")
    crop = mask == 3
    v = ndvi[crop & np.isfinite(ndvi)]
    if v.size < MIN_PX:
        return None
    qs = np.quantile(v, [(i + 0.5) / SEGMENTS for i in range(SEGMENTS)])
    segs = [{"from_pct": round(i * 100 / SEGMENTS), "to_pct": round((i + 1) * 100 / SEGMENTS),
             "ndvi": round(float(q), 3), "color": _color(float(q))} for i, q in enumerate(qs)]
    rank = None
    if value is not None and math.isfinite(value):
        rank = int(round(float(np.count_nonzero(v < value)) / v.size * 100))
    return {
        "segments": segs,
        "median": round(float(np.median(v)), 3),
        "p10": round(float(np.quantile(v, 0.1)), 3),
        "p90": round(float(np.quantile(v, 0.9)), 3),
        "rank_pct": rank,
        "n_px": int(v.size),
        "cropland_km2": round(float(np.count_nonzero(crop)) * px_ha / 100.0, 1),
        "compared_km2": round(float(v.size) * px_ha / 100.0, 1),
        "coverage_pct": round(float(v.size) / max(1, int(np.count_nonzero(crop))) * 100),
    }


def _brak(note: str, **extra: Any) -> Dict[str, Any]:
    return {"status": "brak", **extra, "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, note)}


def county_ndvi(lat: float, lon: float, day: str, value: Optional[float]) -> Dict[str, Any]:
    """Porównanie NDVI działki z polami uprawnymi powiatu z dnia `day` (RRRR-MM-DD). Nigdy nie rzuca."""
    if not settings.cdse_configured:
        return _brak(sentinel.NOT_CONFIGURED_NOTE)
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return _brak("Niepoprawna data zdjęcia.")
    try:
        county = county_at(lat, lon)
    except SourceError as exc:
        return _brak(f"Nie udało się pobrać granicy powiatu z ULDK ({friendly(exc)}).")
    if not county:
        return _brak("Punkt nie leży w żadnym powiecie (ULDK).")
    season = season_for(d)
    base = {"county": {"teryt": county["teryt"], "name": county["name"]}, "day": day, "season": season, "ndvi": value}
    key = f"{county['teryt']}:{day}:v1"
    stats = _result_cache.get(key)
    if not stats:
        try:
            mask = _crop_mask(county, season)
            ndvi = _day_ndvi(county, day)
        except (SourceError, ValueError, OSError) as exc:
            record_error(SOURCE_CDSE, str(exc))
            return _brak(f"Zdjęcia powiatu niedostępne ({friendly(exc)}).", **base)
        record_ok(SOURCE_CDSE)
        geom = shapely_wkt.loads(county["wkt"])
        w, s, e, n = geom.bounds
        width, height = grid(geom.bounds)
        px_ha = ((e - w) * 111320.0 * math.cos(math.radians((s + n) / 2)) / width) * ((n - s) * 110574.0 / height) / 10000.0
        stats = compare(mask, ndvi, None, px_ha)
        if stats is None:
            stats = {"insufficient": True}
        _result_cache.set(key, stats, DAY_TTL_S)
    if stats.get("insufficient"):
        return _brak(f"W dniu {day} nad powiatem było za dużo chmur – za mało bezchmurnych pól do porównania.", **base)
    res = dict(stats)
    if value is not None and math.isfinite(value) and res.get("segments"):
        # miejsce działki liczone z kwantyli (rozkład jest cache'owany bez wartości działki)
        qs = [s["ndvi"] for s in res["segments"]]
        below = sum(1 for q in qs if q < value)
        res["rank_pct"] = int(round(below * 100 / len(qs)))
    return {
        "status": "ok", **base, **res,
        "provenance": provenance(
            sentinel.SOURCE_NAME, sentinel.SOURCE_URL, "pomiar", observed_at=day, fetched_at=iso_utc(), resolution="ok. 100 m (1 ha)",
            note=(f"NDVI pól uprawnych powiatu ({county['name']}, TERYT {county['teryt']}) z tego samego dnia co zdjęcie działki, "
                  f"z pominięciem chmur (maska SCL). Pola uprawne wykryte automatycznie ze zdjęć sezonu {season} (15.04–31.10): "
                  "kwadraty 1 ha, które w sezonie były i gołe (NDVI < 0,3), i zielone (NDVI > 0,55); łąki, lasy, woda i zabudowa są "
                  "pomijane. To przybliżenie, nie urzędowa mapa upraw; różne uprawy rosną w różnym tempie. Granica powiatu: GUGiK ULDK. "
                  "Zawiera zmodyfikowane dane Copernicus Sentinel."),
        ),
    }


# --------------------------------------------------------------------------------------
# Średnia miesięczna NDVI pól uprawnych w powiecie (linia odniesienia w Historii)
# --------------------------------------------------------------------------------------
CHUNK_MONTHS = 4          # jedno zapytanie Process API = 4 miesiące (4 kanały PNG)
MONTHLY_MIN_FRAC = 0.15   # min. udział pól powiatu z bezchmurnym kompozytem, żeby podać średnią miesiąca


def monthly_evalscript(months: List[str]) -> str:
    """Kompozyt miesięczny jak w historii działki (mediana bezchmurnych NDVI piksela), 4 miesiące w 4 kanałach UINT8."""
    ms = "[" + ",".join(f'"{m}"' for m in months) + "]"
    return f"""//VERSION=3
var MONTHS = {ms};
function setup() {{
  return {{input: [{{bands: ["B04", "B08", "SCL", "dataMask"]}}], output: {{bands: {CHUNK_MONTHS}, sampleType: "UINT8"}}, mosaicking: "ORBIT"}};
}}
function med(a) {{ a.sort(function (x, y) {{ return x - y; }}); return a[Math.floor(a.length / 2)]; }}
function evaluatePixel(samples, scenes) {{
  var v = [];
  for (var k = 0; k < {CHUNK_MONTHS}; k++) v.push([]);
  for (var i = 0; i < samples.length; i++) {{
    var s = samples[i];
    if (s.dataMask !== 1 || {sentinel._SCL_JS}.indexOf(s.SCL) !== -1 || s.B08 + s.B04 <= 0) continue;
    var k = MONTHS.indexOf(String(scenes.orbits[i].dateFrom).slice(0, 7));
    if (k < 0) continue;
    v[k].push((s.B08 - s.B04) / (s.B08 + s.B04));
  }}
  var out = [];
  for (var k = 0; k < {CHUNK_MONTHS}; k++) {{
    if (!v[k].length) {{ out.push(0); continue; }}
    var m = Math.max(-1, Math.min(1, med(v[k])));
    out.push(1 + Math.round((m + 1) / 2 * 254));
  }}
  return out;
}}
"""


def _months_between(start: date, end: date) -> List[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _chunk_of(ym: str) -> Tuple[int, int]:
    y, m = int(ym[:4]), int(ym[5:7])
    return y, (m - 1) // CHUNK_MONTHS * CHUNK_MONTHS + 1


def chunk_means(mask: np.ndarray, raw: np.ndarray, months: List[str]) -> Dict[str, Dict[str, Any]]:
    """Kanały rastra (0 = brak) + maska pól → {miesiąc: {ndvi, n_px}}; null gdy za mało bezchmurnych pól."""
    crop = mask == 3
    n_crop = int(np.count_nonzero(crop))
    need = max(MIN_PX // 2, int(n_crop * MONTHLY_MIN_FRAC))
    out: Dict[str, Dict[str, Any]] = {}
    for k, ym in enumerate(months):
        band = raw[..., k] if raw.ndim == 3 and k < raw.shape[2] else np.zeros(mask.shape, dtype=np.uint8)
        sel = crop & (band > 0)
        n = int(np.count_nonzero(sel))
        if n_crop and n >= need:
            vals = (band[sel].astype(np.float32) - 1.0) / 254.0 * 2.0 - 1.0
            out[ym] = {"ndvi": round(float(vals.mean()), 3), "n_px": n}
        else:
            out[ym] = {"ndvi": None, "n_px": n}
    return out


def _chunk(county: Dict[str, Any], mask: np.ndarray, season: int, y: int, m0: int, now: datetime) -> Dict[str, Dict[str, Any]]:
    months = [f"{y}-{m:02d}" for m in range(m0, m0 + CHUNK_MONTHS)]
    key = f"{county['teryt']}:{season}:{months[0]}:v1"
    cached = _monthly_cache.get(key)
    if cached:
        return cached
    with sentinel._key_lock("county_chunk:" + key):   # dwie karty / rozgrzewanie: jedno liczenie zamiast kilku
        cached = _monthly_cache.get(key)
        if cached:
            return cached
        return _chunk_fetch(county, mask, season, y, m0, now, months, key)


def _chunk_fetch(county: Dict[str, Any], mask: np.ndarray, season: int, y: int, m0: int, now: datetime,
                 months: List[str], key: str) -> Dict[str, Dict[str, Any]]:
    start = datetime(y, m0, 1, tzinfo=timezone.utc)
    end_y, end_m = (y, m0 + CHUNK_MONTHS) if m0 + CHUNK_MONTHS <= 12 else (y + 1, m0 + CHUNK_MONTHS - 12)
    end = min(datetime(end_y, end_m, 1, tzinfo=timezone.utc), now)
    geom = shapely_wkt.loads(county["wkt"])
    raw = _process(geom, iso_utc(start), iso_utc(end), monthly_evalscript(months), {"maxCloudCoverage": 80},
                   timeout=150.0, bands=CHUNK_MONTHS)
    res = chunk_means(mask, raw, months)
    done = datetime(end_y, end_m, 1, tzinfo=timezone.utc) <= now
    _monthly_cache.set(key, res, 365 * 24 * 3600 if done else 24 * 3600)
    return res


def county_monthly(points: List[Tuple[float, float, float]], start: Optional[str], end: Optional[str]) -> Dict[str, Any]:
    """Miesięczna średnia NDVI pól uprawnych w powiecie (lub kilku powiatach, ważona powierzchnią działek).

    points: [(lat, lon, waga)]. Zwraca {months:[{month, ndvi}], counties:[{teryt, name, weight_pct}], provenance}. Nigdy nie rzuca."""
    from src.data.history import resolve_range   # import lokalny: history importuje sentinel, unikamy cyklu
    if not settings.cdse_configured:
        return {"months": [], "counties": [], "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, sentinel.NOT_CONFIGURED_NOTE)}
    start_d, end_dt = resolve_range(start, end)
    months = _months_between(start_d, end_dt.date())
    groups: Dict[str, Dict[str, Any]] = {}
    try:
        for lat, lon, w in points:
            c = county_at(lat, lon)
            if c:
                g = groups.setdefault(c["teryt"], {"county": c, "weight": 0.0})
                g["weight"] += max(0.0, w) or 1.0
    except SourceError as exc:
        return {"months": [], "counties": [], "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL,
                                                                        f"Nie udało się pobrać granicy powiatu z ULDK ({friendly(exc)}).")}
    if not groups:
        return {"months": [], "counties": [], "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, "Działki poza granicami powiatów (ULDK).")}
    now = datetime.now(timezone.utc)
    season = season_for(now.date())
    chunks = sorted({_chunk_of(ym) for ym in months})
    per: Dict[str, Dict[str, Dict[str, Any]]] = {t: {} for t in groups}
    try:
        masks = {t: _crop_mask(g["county"], season) for t, g in groups.items()}
        jobs = [(t, y, m0) for t in groups for (y, m0) in chunks]
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(_chunk, groups[t]["county"], masks[t], season, y, m0, now): t for t, y, m0 in jobs}
            for f, t in futs.items():
                per[t].update(f.result())
    except (SourceError, ValueError, OSError) as exc:
        record_error(SOURCE_CDSE, str(exc))
        return {"months": [], "counties": [], "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL,
                                                                        f"Średnia w powiecie niedostępna ({friendly(exc)}).")}
    record_ok(SOURCE_CDSE)
    total = sum(g["weight"] for g in groups.values()) or 1.0
    rows = []
    for ym in months:
        num_, den = 0.0, 0.0
        for t, g in groups.items():
            v = (per[t].get(ym) or {}).get("ndvi")
            if v is not None:
                num_ += v * g["weight"]
                den += g["weight"]
        # przy kilku powiatach średnia tylko wtedy, gdy są dane dla powiatów z co najmniej połową powierzchni działek
        rows.append({"month": ym, "ndvi": round(num_ / den, 3) if den and den >= total * 0.5 else None})
    counties = [{"teryt": t, "name": g["county"]["name"], "weight_pct": round(g["weight"] / total * 100)} for t, g in groups.items()]
    names = ", ".join(c["name"] for c in counties)
    return {
        "months": rows,
        "counties": counties,
        "provenance": provenance(
            sentinel.SOURCE_NAME + " – średnia w powiecie", sentinel.SOURCE_URL, "pomiar" if any(r["ndvi"] is not None for r in rows) else "brak",
            fetched_at=iso_utc(), resolution="ok. 100 m (1 ha)",
            note=(f"Średnie NDVI pól uprawnych ({names}) w każdym miesiącu: dla każdego kwadratu 1 ha mediana bezchmurnych zdjęć "
                  "Sentinel-2 z miesiąca (maska SCL), uśredniona po polach powiatu"
                  + (" i ważona powierzchnią zaznaczonych działek w każdym powiecie" if len(counties) > 1 else "")
                  + f". Pola uprawne wykryte automatycznie ze zdjęć sezonu {season} (kwadraty, które w sezonie były i gołe, i zielone); "
                  "łąki, lasy, woda i zabudowa pominięte. Miesiąc bez wartości: za mało bezchmurnych pól. Granica powiatu: GUGiK ULDK. "
                  "Zawiera zmodyfikowane dane Copernicus Sentinel."),
        ),
    }

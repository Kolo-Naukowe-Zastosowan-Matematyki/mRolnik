"""Historia działki (zakładka „Historia”): wskaźniki Sentinel-2 miesiąc po miesiącu, klatki mapy do animacji
oraz miesięczne opady na tle normy IMGW-PIB.

Wskaźniki (miesięczny kompozyt – dla każdego piksela mediana bezchmurnych obserwacji w miesiącu, maska SCL):
- NDVI  = (B08 − B04) / (B08 + B04)                          – zieleń / biomasa roślin,
- NDMI  = (B08 − B11) / (B08 + B11)                          – zawartość wody w roślinach (tzw. NDWI wg Gao, 1996),
- NDWI  = (B03 − B08) / (B03 + B08)                          – woda stojąca na powierzchni (McFeeters, 1996),
- EVI   = 2,5 · (B08 − B04) / (B08 + 6·B04 − 7,5·B02 + 1)    – wskaźnik wegetacji mniej wrażliwy na nasycenie przy gęstym łanie,
- gleba odsłonięta = udział pikseli działki z medianą NDVI < 0,25.
Dane: program Copernicus (UE), Sentinel-2 L2A przez Copernicus Data Space Ecosystem (Statistical API i Process API).

Opady miesięczne: reanaliza ERA5 (Copernicus C3S/ECMWF) przez archiwum Open-Meteo – model, nie pomiar stacyjny;
norma: IMGW-PIB 1991–2020 dla najbliższej stacji synoptycznej (src/data/imgw_normals.json).
Wilgotność gleby 7–28 cm: ERA5-Land (średnia miesięczna). Wszystko z proweniencją; braki → null + "brak".
"""
from __future__ import annotations

import base64
import io
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image
from shapely.geometry import mapping

from src.core.cache import TTLCache
from src.core.config import settings
from src.data import era5_archive, sentinel
from src.data.cadastre import ParcelData, bbox_wgs84
from src.data.common import (
    SOURCE_CDSE,
    SourceError,
    friendly,
    http_get_json,
    iso_utc,
    missing,
    provenance,
    record_error,
    record_ok,
    to_float,
    utcnow,
)
from src.data.reference import nearest_normals, normals_provenance

log = logging.getLogger(__name__)

HISTORY_MONTHS = 36
INDICES_TTL_S = 24 * 3600
FRAME_PAST_TTL_S = 30 * 24 * 3600
FRAME_CURRENT_TTL_S = 6 * 3600
CLIMATE_TTL_S = 24 * 3600
OPEN_METEO_ARCHIVE_URL = era5_archive.OPEN_METEO_ARCHIVE_URL

_indices_cache = TTLCache("hist_indices", max_entries=5000, default_ttl=INDICES_TTL_S)
_frame_cache = TTLCache("hist_frames", max_entries=20000, default_ttl=FRAME_PAST_TTL_S)
_climate_cache = TTLCache("hist_climate", max_entries=5000, default_ttl=CLIMATE_TTL_S)
# Wiersze pojedynczych miesięcy (ta sama statystyka P1M co w całym zakresie): miesiąc zakończony nie zmienia się,
# więc przy kolejnym otwarciu Historii z CDSE pobieramy tylko miesiące jeszcze nie policzone (zwykle bieżący).
INDEX_MONTH_DONE_TTL_S = 30 * 24 * 3600
INDEX_MONTH_OPEN_TTL_S = 6 * 3600
INDEX_MONTH_SETTLE_DAYS = 6     # miesiąc uznajemy za zamknięty po 6 dniach (dogrywanie scen L2A do archiwum)
_index_month_cache = TTLCache("hist_index_month", max_entries=200000, default_ttl=INDEX_MONTH_DONE_TTL_S)

_SCL_JS = sentinel._SCL_JS

INDEX_INFO: Dict[str, Dict[str, Any]] = {
    "ndvi": {"label": "NDVI – zieleń roślin", "short": "NDVI", "range": (-0.2, 1.0)},
    "ndmi": {"label": "NDMI – woda w roślinach", "short": "NDMI", "range": (-0.5, 0.7)},
    "ndwi": {"label": "NDWI – woda na powierzchni pola", "short": "NDWI", "range": (-0.8, 0.5)},
    "evi": {"label": "EVI – wskaźnik wegetacji (gęsty łan)", "short": "EVI", "range": (-0.2, 1.0)},
    "bare": {"label": "Gleba odsłonięta (NDVI < 0,25)", "short": "Gleba odsłonięta", "range": (0.0, 1.0)},
}

# Palety klatek mapy (przedziały [od, do)); NDVI = paleta głównej mapy
PALETTES: Dict[str, List[Dict[str, Any]]] = {
    "ndvi": sentinel.NDVI_PALETTE,
    "evi": [
        {"from": -1.0, "to": 0.10, "color": "#7f3b08", "label": "brak roślin"},
        {"from": 0.10, "to": 0.20, "color": "#c48a3a", "label": "gleba odsłonięta"},
        {"from": 0.20, "to": 0.35, "color": "#f2d24b", "label": "roślinność rzadka"},
        {"from": 0.35, "to": 0.50, "color": "#a6d96a", "label": "roślinność umiarkowana"},
        {"from": 0.50, "to": 0.70, "color": "#4daf4a", "label": "łan dobry"},
        {"from": 0.70, "to": 1.01, "color": "#0b5d1e", "label": "łan bardzo gęsty"},
    ],
    "ndmi": [
        {"from": -1.0, "to": -0.20, "color": "#8c510a", "label": "bardzo sucho / gleba odkryta"},
        {"from": -0.20, "to": 0.00, "color": "#d8b365", "label": "sucho – stres wodny"},
        {"from": 0.00, "to": 0.20, "color": "#c7eae5", "label": "umiarkowanie"},
        {"from": 0.20, "to": 0.40, "color": "#5ab4ac", "label": "dobre uwodnienie"},
        {"from": 0.40, "to": 1.01, "color": "#01665e", "label": "wysokie uwodnienie / woda"},
    ],
    "ndwi": sentinel.NDWI_PALETTE,
    "bare": [
        {"from": -1.0, "to": 0.25, "color": "#a0522d", "label": "gleba odsłonięta (NDVI < 0,25)"},
        {"from": 0.25, "to": 1.01, "color": "#2e8b57", "label": "pokryta roślinami"},
    ],
}

# Statystyki miesięczne: mediana bezchmurnych obserwacji piksela w miesiącu dla NDVI, NDMI, NDWI i EVI + piksel „gleba odsłonięta”
EVALSCRIPT_MULTI = f"""//VERSION=3
function setup() {{
  return {{
    input: [{{bands: ["B02", "B03", "B04", "B08", "B11", "SCL", "dataMask"]}}],
    output: [{{id: "ndvi", bands: 1, sampleType: "FLOAT32"}}, {{id: "ndmi", bands: 1, sampleType: "FLOAT32"}},
             {{id: "ndwi", bands: 1, sampleType: "FLOAT32"}}, {{id: "evi", bands: 1, sampleType: "FLOAT32"}}, {{id: "bare", bands: 1, sampleType: "FLOAT32"}},
             {{id: "cnt", bands: 1, sampleType: "UINT8"}}, {{id: "dataMask", bands: 1}}],
    mosaicking: "ORBIT"
  }};
}}
function med(a) {{ a.sort(function (x, y) {{ return x - y; }}); return a[Math.floor(a.length / 2)]; }}
function evaluatePixel(samples) {{
  var nd = [], nm = [], nw = [], ev = [];
  var inside = 0;
  for (var i = 0; i < samples.length; i++) {{
    var s = samples[i];
    if (s.dataMask === 1) inside = 1;
    if (s.dataMask !== 1 || {_SCL_JS}.indexOf(s.SCL) !== -1) continue;
    var d1 = s.B08 + s.B04, d2 = s.B08 + s.B11, d3 = s.B08 + 6 * s.B04 - 7.5 * s.B02 + 1;
    if (d1 <= 0 || d2 <= 0 || d3 === 0) continue;
    nd.push((s.B08 - s.B04) / d1);
    nm.push((s.B08 - s.B11) / d2);
    if (s.B03 + s.B08 > 0) nw.push((s.B03 - s.B08) / (s.B03 + s.B08));
    ev.push(Math.max(-1, Math.min(1.5, 2.5 * (s.B08 - s.B04) / d3)));
  }}
  if (nd.length === 0) return {{ndvi: [NaN], ndmi: [NaN], ndwi: [NaN], evi: [NaN], bare: [NaN], cnt: [0], dataMask: [inside]}};
  var n = med(nd);
  return {{ndvi: [n], ndmi: [med(nm)], ndwi: [nw.length ? med(nw) : NaN], evi: [med(ev)], bare: [n < {sentinel.BARE_SOIL_MAX} ? 1 : 0], cnt: [1], dataMask: [inside]}};
}}
"""


def _frame_evalscript(index: str) -> str:
    """Raster miesięczny jednego wskaźnika (mediana bezchmurnych obserwacji), UINT8: 0 = brak, 1..255 = wartość [-1, 1]."""
    expr = {
        "ndvi": "(s.B08 - s.B04) / (s.B08 + s.B04)",
        "bare": "(s.B08 - s.B04) / (s.B08 + s.B04)",
        "ndmi": "(s.B08 - s.B11) / (s.B08 + s.B11)",
        "ndwi": "(s.B03 - s.B08) / (s.B03 + s.B08)",
        "evi": "2.5 * (s.B08 - s.B04) / (s.B08 + 6 * s.B04 - 7.5 * s.B02 + 1)",
    }[index]
    return f"""//VERSION=3
function setup() {{
  return {{input: [{{bands: ["B02", "B03", "B04", "B08", "B11", "SCL", "dataMask"]}}], output: {{bands: 1, sampleType: "UINT8"}}, mosaicking: "ORBIT"}};
}}
function evaluatePixel(samples) {{
  var v = [];
  for (var i = 0; i < samples.length; i++) {{
    var s = samples[i];
    if (s.dataMask !== 1 || {_SCL_JS}.indexOf(s.SCL) !== -1 || s.B08 + s.B04 <= 0 || s.B08 + s.B11 <= 0) continue;
    var x = {expr};
    if (isFinite(x)) v.push(x);
  }}
  if (v.length === 0) return [0];
  v.sort(function (a, b) {{ return a - b; }});
  var m = Math.max(-1, Math.min(1, v[Math.floor(v.length / 2)]));
  return [1 + Math.round((m + 1) / 2 * 254)];
}}
"""


# Klatka „zdjęcie”: barwy naturalne (B04, B03, B02), dla każdego piksela mediana bezchmurnych obserwacji w miesiącu
EVALSCRIPT_PHOTO = f"""//VERSION=3
function setup() {{
  return {{input: [{{bands: ["B02", "B03", "B04", "SCL", "dataMask"]}}], output: {{bands: 4, sampleType: "UINT8"}}, mosaicking: "ORBIT"}};
}}
function med(a) {{ a.sort(function (x, y) {{ return x - y; }}); return a[Math.floor(a.length / 2)]; }}
function c(v) {{ return Math.max(0, Math.min(255, Math.round(v * 2.5 * 255))); }}
function evaluatePixel(samples) {{
  var r = [], g = [], b = [];
  for (var i = 0; i < samples.length; i++) {{
    var s = samples[i];
    if (s.dataMask !== 1 || {_SCL_JS}.indexOf(s.SCL) !== -1) continue;
    r.push(s.B04); g.push(s.B03); b.push(s.B02);
  }}
  if (r.length === 0) return [0, 0, 0, 0];
  return [c(med(r)), c(med(g)), c(med(b)), 255];
}}
"""

FRAME_KINDS = set(INDEX_INFO) | {"photo"}


# --------------------------------------------------------------------------------------
# Pomocnicze
# --------------------------------------------------------------------------------------
def month_start(d: date, months_back: int) -> date:
    y, m = d.year, d.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


ARCHIVE_START = "2017-01"   # zakres wyboru w aplikacji: pełne lata archiwum Sentinel-2 L2A w CDSE
CLIMATE_ARCHIVE_START = "1991-01"   # opady / wilgotność gleby z reanalizy: 10 lat wstecz od dowolnej daty analizy


def resolve_range(start: Optional[str], end: Optional[str], months: int = HISTORY_MONTHS,
                  earliest: str = ARCHIVE_START) -> Tuple[date, datetime]:
    """Zakres miesięcy (RRRR-MM) → (pierwszy dzień, koniec). Bez parametrów: ostatnie `months` miesięcy.
    Początek nie wcześniej niż `earliest`, koniec nie później niż teraz; odwrócony zakres jest zamieniany."""
    now = utcnow()
    end_dt = now
    if end:
        e = month_range(end)[1]
        end_dt = min(e, now)
    if start:
        sy, sm = int(start[:4]), int(start[5:7])
        start_d = date(sy, sm, 1)
    else:
        start_d = month_start(end_dt.date(), months - 1)
    lo = date(int(earliest[:4]), int(earliest[5:7]), 1)
    start_d = max(start_d, lo)
    if start_d > end_dt.date():
        start_d, end_dt = date(end_dt.year, end_dt.month, 1), min(month_range(start_d.strftime("%Y-%m"))[1], now)
    return start_d, end_dt


def month_range(ym: str) -> Tuple[datetime, datetime]:
    y, m = int(ym[:4]), int(ym[5:7])
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    nxt = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc)
    return start, nxt - timedelta(seconds=1)


def parse_multi_stats(data: Any) -> List[Dict[str, Any]]:
    """Statistical API (wiele wyjść) → [{month, ndvi, ndmi, ndwi, evi, bare_pct, valid_pct}]."""
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise SourceError("Statistical API: niepoprawna odpowiedź")
    out = []
    for item in data["data"]:
        if not isinstance(item, dict) or item.get("error"):
            continue
        frm = str(((item.get("interval") or {}).get("from")) or "")
        outs = item.get("outputs") or {}

        def stat(name: str, key: str = "mean") -> Optional[float]:
            try:
                return to_float(outs[name]["bands"]["B0"]["stats"].get(key))
            except (KeyError, TypeError, AttributeError):
                return None

        try:
            st = outs["ndvi"]["bands"]["B0"]["stats"]
            n = (to_float(st.get("sampleCount")) or 0.0) - (to_float(st.get("noDataCount")) or 0.0)
        except (KeyError, TypeError, AttributeError):
            continue
        valid = (stat("cnt") or 0.0) if n > 0 else 0.0
        ok = valid >= 0.5
        bare = stat("bare")
        out.append({
            "month": frm[:7],
            "ndvi": round(stat("ndvi"), 3) if ok and stat("ndvi") is not None else None,
            "ndmi": round(stat("ndmi"), 3) if ok and stat("ndmi") is not None else None,
            "ndwi": round(stat("ndwi"), 3) if ok and stat("ndwi") is not None else None,
            "evi": round(stat("evi"), 3) if ok and stat("evi") is not None else None,
            "bare_pct": round(bare * 100, 1) if ok and bare is not None else None,
            "valid_pct": round(valid * 100, 1),
        })
    return out


def _hex_rgb(h: str) -> Tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def colorize(values: np.ndarray, index: str) -> str:
    """Raster wartości (NaN = brak) → kolorowa nakładka PNG (data URI) wg palety wskaźnika."""
    pal = PALETTES[index]
    h, w = values.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    valid = np.isfinite(values)
    edges = np.array([c["to"] for c in pal[:-1]], dtype=np.float32)
    idx = np.digitize(np.where(valid, values, 0.0), edges)
    colors = np.array([_hex_rgb(c["color"]) for c in pal], dtype=np.uint8)
    rgba[..., :3] = colors[idx]
    alpha = 230 if index != "bare" else 0
    rgba[..., 3] = np.where(valid, alpha, 0).astype(np.uint8)
    if index == "bare":   # gleba odsłonięta wyraźnie, roślinność półprzezroczysta
        rgba[..., 3] = np.where(valid, np.where(idx == 0, 235, 110), 0).astype(np.uint8)
    img = Image.fromarray(rgba, mode="RGBA")
    scale = max(1, min(8, 256 // max(h, w)))
    if scale > 1:
        img = img.resize((w * scale, h * scale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _legend(index: str) -> List[Dict[str, Any]]:
    pal = PALETTES[index]
    return [dict(c, to=min(c["to"], 1.0)) for c in pal]


# --------------------------------------------------------------------------------------
# 1) Wskaźniki miesięczne (36 mies.)
# --------------------------------------------------------------------------------------
def _months_list(start_d: date, end_d: date) -> List[str]:
    out, y, m = [], start_d.year, start_d.month
    while (y, m) <= (end_d.year, end_d.month):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _error_months(data: Any) -> set:
    """Miesiące, dla których Statistical API zwróciło błąd – nie zapisujemy ich jako „brak obserwacji”."""
    out = set()
    for item in (data.get("data") if isinstance(data, dict) else None) or []:
        if isinstance(item, dict) and item.get("error"):
            out.add(str(((item.get("interval") or {}).get("from")) or "")[:7])
    return out


def _month_settled(ym: str, now: datetime) -> bool:
    return month_range(ym)[1] + timedelta(days=INDEX_MONTH_SETTLE_DAYS) < now


def monthly_indices(parcel: ParcelData, months: int = HISTORY_MONTHS, start: Optional[str] = None,
                    end: Optional[str] = None) -> Dict[str, Any]:
    """Miesięczne NDVI, NDMI, NDWI, EVI i udział gleby odsłoniętej dla zakresu RRRR-MM (domyślnie ostatnie 36 mies.).
    Brakujące miesiące to jedno zapytanie Statistical API (P1M); miesiące zakończone są w cache, więc zwykle
    pobieramy tylko bieżący. Równoczesne zapytania o to samo czekają na jedno pobranie. Nigdy nie rzuca."""
    if not settings.cdse_configured:
        return {"months": [], "indices": INDEX_INFO_PUBLIC, "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, sentinel.NOT_CONFIGURED_NOTE)}
    start_d, end = resolve_range(start, end, months)
    key = f"{parcel.geometry_key()}:{start_d.isoformat()}:{end.date().isoformat()}:v2"   # v2: + NDWI
    cached = _indices_cache.get(key)
    if cached:
        return cached
    with sentinel._key_lock("hist_idx:" + key):
        cached = _indices_cache.get(key)
        return cached if cached else _monthly_indices_fetch(parcel, start_d, end, key)


def _monthly_indices_fetch(parcel: ParcelData, start_d: date, end: datetime, key: str) -> Dict[str, Any]:
    gk = parcel.geometry_key()
    wanted = _months_list(start_d, end.date())
    known: Dict[str, Optional[Dict[str, Any]]] = {}
    for ym in wanted:
        hit = _index_month_cache.get(f"{gk}:{ym}:v2")
        if isinstance(hit, dict) and "row" in hit:
            known[ym] = hit["row"]
    todo = [ym for ym in wanted if ym not in known]
    if todo:
        f_start = datetime(int(todo[0][:4]), int(todo[0][5:7]), 1, tzinfo=timezone.utc)
        f_end = min(month_range(todo[-1])[1], end)
        geom, crs = sentinel.geometry_utm(parcel)
        body = {
            "input": {"bounds": {"geometry": geom, "properties": {"crs": crs}}, "data": [{"type": sentinel.COLLECTION}]},
            "aggregation": {
                "timeRange": {"from": iso_utc(f_start), "to": iso_utc(f_end)},
                "aggregationInterval": {"of": "P1M", "lastIntervalBehavior": "SHORTEN"},
                "evalscript": EVALSCRIPT_MULTI, "resx": 10, "resy": 10,
            },
        }
        try:
            data = sentinel._sh_post(sentinel.STATS_URL, body, timeout=90.0).json()
            rows = parse_multi_stats(data)
        except (SourceError, ValueError) as exc:
            record_error(SOURCE_CDSE, str(exc))
            return {"months": [], "indices": INDEX_INFO_PUBLIC,
                    "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, f"Historia wskaźników niedostępna ({friendly(exc)}).")}
        record_ok(SOURCE_CDSE)
        got = {r["month"]: r for r in rows}
        failed = _error_months(data)
        now = utcnow()
        fetched = set(_months_list(f_start.date(), f_end.date()))
        for ym in todo:
            if ym in failed:
                continue                      # błąd po stronie CDSE dla tego miesiąca: spróbujemy następnym razem
            row = got.get(ym)                 # brak wiersza = w miesiącu nie było żadnej sceny nad działką
            known[ym] = row
            if ym in fetched:
                _index_month_cache.set(f"{gk}:{ym}:v2", {"row": row},
                                       INDEX_MONTH_DONE_TTL_S if _month_settled(ym, now) else INDEX_MONTH_OPEN_TTL_S)
        for ym, r in got.items():             # miesiące spoza listy do pobrania, ale w zakresie (zwykle ich nie ma)
            if ym in fetched and ym in set(wanted) and ym not in known:
                known[ym] = r
    rows = sorted((r for ym, r in known.items() if r is not None and ym in set(wanted)), key=lambda r: r["month"])
    res = {
        "months": rows,
        "indices": INDEX_INFO_PUBLIC,
        "provenance": provenance(
            sentinel.SOURCE_NAME, sentinel.SOURCE_URL, "pomiar", fetched_at=iso_utc(end), resolution="10 m (B11: 20 m)",
            note=("Miesięczny kompozyt: dla każdego piksela mediana bezchmurnych obserwacji Sentinel-2 w miesiącu (maska SCL), "
                  "uśredniona po działce. NDVI = (B8−B4)/(B8+B4); NDMI = (B8−B11)/(B8+B11) (woda w roślinach, NDWI wg Gao); "
                  "NDWI = (B3−B8)/(B3+B8) (woda stojąca na powierzchni, McFeeters); "
                  "EVI = 2,5·(B8−B4)/(B8+6·B4−7,5·B2+1); gleba odsłonięta = udział pikseli z NDVI < 0,25. Miesiące z <50% "
                  "działki bez chmur mają wartość null. Zawiera zmodyfikowane dane Copernicus Sentinel."),
        ),
    }
    if any(r["ndvi"] is not None for r in rows):
        _indices_cache.set(key, res, INDICES_TTL_S)
    return res


INDEX_INFO_PUBLIC = {k: {"label": v["label"], "short": v["short"]} for k, v in INDEX_INFO.items()}


# --------------------------------------------------------------------------------------
# 2) Klatka mapy (raster miesięczny jednego wskaźnika)
# --------------------------------------------------------------------------------------
def monthly_frame(parcel: ParcelData, month: str, index: str) -> Dict[str, Any]:
    """Kolorowa nakładka wskaźnika dla miesiąca (mediana bezchmurnych obserwacji). Nigdy nie rzuca.
    Równoczesne zapytania o tę samą klatkę (np. wstępne pobieranie i kliknięcie) czekają na jedno pobranie z CDSE."""
    with sentinel._key_lock(f"hist_frame:{parcel.geometry_key()}:{month}:{index}"):
        return _monthly_frame(parcel, month, index)


def _monthly_frame(parcel: ParcelData, month: str, index: str) -> Dict[str, Any]:
    if index not in FRAME_KINDS:
        index = "ndvi"
    photo = index == "photo"
    base = {"month": month, "index": index, "legend": [] if photo else _legend(index)}
    if not settings.cdse_configured:
        return {**base, "image": None, "bounds": None, "mean": None, "valid_pct": None,
                "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, sentinel.NOT_CONFIGURED_NOTE)}
    now = utcnow()
    current = month == now.strftime("%Y-%m")
    key = f"{parcel.geometry_key()}:{month}:{index}" + (":okolica2" if photo else "")
    cached = _frame_cache.get(key)
    if cached:
        return cached
    start, end = month_range(month)
    if start > now:
        return {**base, "image": None, "bounds": None, "mean": None, "valid_pct": None,
                "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, "Miesiąc z przyszłości.")}
    end = min(end, now)
    if _span_km(bbox_wgs84(parcel)) > MAX_FRAME_SPAN_KM:
        # działki w odległych miejscach: jedna klatka na pół Polski nie pokaże działek, a kosztowałaby duże zapytanie
        return {**base, "image": None, "bounds": None, "mean": None, "valid_pct": None,
                "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL,
                                      f"Zaznaczone działki leżą dalej niż {MAX_FRAME_SPAN_KM:.0f} km od siebie – mapę pokazujemy osobno dla każdego miejsca.")}
    bbox = photo_bbox(parcel) if photo else bbox_wgs84(parcel)
    width, height = sentinel.raster_size(bbox)
    bounds: Dict[str, Any] = {"bbox": list(bbox), "properties": {"crs": sentinel.CRS84}}
    if not photo:   # wskaźniki przycinamy do działki; zdjęcie pokazuje też okolicę (cały kadr mapy)
        bounds["geometry"] = mapping(parcel.polygon_wgs84)
    body = {
        "input": {
            "bounds": bounds,
            "data": [{"type": sentinel.COLLECTION, "dataFilter": {"timeRange": {"from": iso_utc(start), "to": iso_utc(end)}}}],
        },
        "output": {"width": width, "height": height, "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
        "evalscript": EVALSCRIPT_PHOTO if photo else _frame_evalscript(index),
    }
    try:
        r = sentinel._sh_post(sentinel.PROCESS_URL, body, accept="image/png", timeout=40.0)
        if not (r.content or b"").startswith(b"\x89PNG"):
            raise SourceError("Process API: odpowiedź nie jest obrazem PNG")
        if photo:
            return _photo_frame(base, r.content, bbox, month, current, key, now)
        values = sentinel.decode_ndvi_png(r.content)
    except SourceError as exc:
        record_error(SOURCE_CDSE, str(exc))
        return {**base, "image": None, "bounds": None, "mean": None, "valid_pct": None,
                "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, f"Klatka niedostępna ({friendly(exc)}).")}
    record_ok(SOURCE_CDSE)
    finite = values[np.isfinite(values)]
    w, s, e, n = bbox
    if index == "bare":
        mean = round(float(np.count_nonzero(finite < sentinel.BARE_SOIL_MAX)) / finite.size * 100, 1) if finite.size else None
    else:
        mean = round(float(finite.mean()), 3) if finite.size else None
    res = {
        **base,
        "image": colorize(values, index) if finite.size else None,
        "bounds": [[s, w], [n, e]],
        "mean": mean,
        "provenance": provenance(
            sentinel.SOURCE_NAME, sentinel.SOURCE_URL, "pomiar" if finite.size else "brak", fetched_at=iso_utc(now),
            resolution="10 m",
            note=(f"Kompozyt miesięczny {month}: mediana bezchmurnych obserwacji Sentinel-2 dla każdego piksela (maska SCL). "
                  "Piksele bez żadnej bezchmurnej obserwacji w miesiącu są przezroczyste. Zawiera zmodyfikowane dane Copernicus Sentinel."
                  if finite.size else f"W miesiącu {month} nie było bezchmurnej obserwacji działki."),
        ),
    }
    _frame_cache.set(key, res, FRAME_CURRENT_TTL_S if current else FRAME_PAST_TTL_S)
    return res


MAX_FRAME_SPAN_KM = 40.0    # klatka mapy tylko dla działek w jednym miejscu (większy zasięg = osobne miejsca)


def _span_km(bbox: Tuple[float, float, float, float]) -> float:
    import math
    w, s, e, n = bbox
    return max((e - w) * 111.32 * math.cos(math.radians((s + n) / 2)), (n - s) * 110.574)


PHOTO_MIN_SPAN_M = 1200.0   # zdjęcie miesięczne: co najmniej ok. 1,2 × 1,2 km wokół działek
PHOTO_PAD_FRAC = 0.5
PHOTO_WIDE_X = 2.8    # połowa szerokości kadru = 2,8 × połowa boku bazowego
PHOTO_WIDE_Y = 1.2


def photo_bbox(parcel: ParcelData) -> Tuple[float, float, float, float]:
    """Kadr zdjęcia w Historii (też tło pod wskaźnikami): działki z okolicą, szeroki jak mapa na komputerze.

    Bok bazowy S = większy z wymiarów działek z marginesem 50% (nie mniej niż ok. 1,2 km); kadr ma 2,4 S wysokości
    i 5,6 S szerokości, żeby przy dopasowaniu mapy do działek zdjęcie wypełniało cały widok (mapa ma ok. 3,6 : 1).
    """
    import math
    w, s, e, n = bbox_wgs84(parcel, pad_frac=PHOTO_PAD_FRAC)
    cx, cy = (w + e) / 2, (s + n) / 2
    mx = 111320.0 * math.cos(math.radians(cy))
    my = 110574.0
    half = max((e - w) / 2 * mx, (n - s) / 2 * my, PHOTO_MIN_SPAN_M / 2)
    half_x = half * PHOTO_WIDE_X / mx
    half_y = half * PHOTO_WIDE_Y / my
    return (cx - half_x, cy - half_y, cx + half_x, cy + half_y)


def _photo_frame(base: Dict[str, Any], png: bytes, bbox: Tuple[float, float, float, float], month: str, current: bool,
                 key: str, now: datetime) -> Dict[str, Any]:
    """Klatka w barwach naturalnych: obraz RGBA z Process API (przezroczyste = brak bezchmurnej obserwacji)."""
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    alpha = np.asarray(img.getchannel("A"))
    valid = int(np.count_nonzero(alpha))
    w, s, e, n = bbox
    if valid:
        h_px, w_px = alpha.shape
        scale = max(1, min(8, 256 // max(h_px, w_px)))
        if scale > 1:
            img = img.resize((w_px * scale, h_px * scale), Image.NEAREST)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        image = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    else:
        image = None
    res = {
        **base,
        "image": image,
        "bounds": [[s, w], [n, e]],
        "mean": None,
        "provenance": provenance(
            sentinel.SOURCE_NAME, sentinel.SOURCE_URL, "pomiar" if valid else "brak", fetched_at=iso_utc(now), resolution="10 m",
            note=(f"Zdjęcie {month} w barwach naturalnych (kanały B04, B03, B02), działki z okolicą: dla każdego piksela mediana "
                  "bezchmurnych obserwacji Sentinel-2 z miesiąca (maska SCL); miejsca bez bezchmurnego zdjęcia są przezroczyste. "
                  "Zawiera zmodyfikowane dane Copernicus Sentinel."
                  if valid else f"W miesiącu {month} nie było bezchmurnej obserwacji działki."),
        ),
    }
    _frame_cache.set(key, res, FRAME_CURRENT_TTL_S if current else FRAME_PAST_TTL_S)
    return res


# --------------------------------------------------------------------------------------
# 3) Opady miesięczne (ERA5) na tle normy IMGW-PIB + wilgotność gleby (ERA5-Land)
#    Dane dobowe ze wspólnego archiwum src/data/era5_archive.py (porcje roczne w cache, bezpiecznik limitu
#    Open-Meteo, zapasowo NASA POWER). Krok 2 (historyczna retencja wody), zakładki Historia i Pole.
# --------------------------------------------------------------------------------------
CLIMATE_FALLBACK_TTL_S = 3600   # wynik ze źródła zapasowego trzymamy krócej – po odnowieniu limitu wracamy do ERA5


def monthly_climate(lat: float, lon: float, months: int = HISTORY_MONTHS, start: Optional[str] = None,
                    end: Optional[str] = None, analysis_date: Optional[date] = None) -> Dict[str, Any]:
    """{months:[{month, precip_mm, precip_normal_mm, soil_moisture_pct, soil_wetness_pct, complete}], normals,
    provenance, source}. Nigdy nie rzuca.

    `analysis_date` (opcjonalnie) – data analizy: żaden dzień po niej nie trafia do wyniku (brak przecieku danych
    z przyszłości); bez `start` zakres to `months` miesięcy kończących się w miesiącu daty analizy.
    """
    if analysis_date is not None:
        ad_ym = analysis_date.strftime("%Y-%m")
        end = min(end, ad_ym) if end else ad_ym
    start_d, end_dt = resolve_range(start, end, months, earliest=CLIMATE_ARCHIVE_START)
    end_d = min(end_dt.date(), era5_archive.archive_end(utcnow()))   # archiwum ERA5 ma opóźnienie ok. 5 dni
    if analysis_date is not None:
        end_d = min(end_d, analysis_date)
    if end_d < start_d:
        end_d = start_d
    key = f"{round(lat, 2):.2f},{round(lon, 2):.2f}:{start_d}:{end_d}"
    cached = _climate_cache.get(key)
    if cached:
        return cached
    with sentinel._key_lock("hist_clim:" + key):
        cached = _climate_cache.get(key)
        if cached:
            return cached
        return _monthly_climate_fetch(lat, lon, start_d, end_d, key)


def _pct_mean(vals: List[float]) -> Optional[float]:
    return round(sum(vals) / len(vals) * 100, 1) if vals else None


def _monthly_climate_fetch(lat: float, lon: float, start_d: date, end_d: date, key: str) -> Dict[str, Any]:
    normals = nearest_normals(lat, lon)
    nprov = normals_provenance(normals)
    try:
        arch = era5_archive.daily_series(lat, lon, start_d, end_d, era5_archive.WATER)
    except SourceError as exc:
        return {"months": [], "source": None, "normals_station": normals["station"] if normals else None,
                "provenance": {"precip": missing(era5_archive.ERA5_SOURCE, era5_archive.ERA5_URL,
                                                 f"Archiwum opadów i wilgotności gleby niedostępne ({friendly(exc)}). "
                                                 "Ocena historycznej podatności na suszę nie jest możliwa bez tych danych."),
                               "normals": nprov}}
    ser = arch["series"]
    p_sum: Dict[str, float] = {}
    p_days: Dict[str, int] = {}
    sm: Dict[str, List[float]] = {}
    sw: Dict[str, List[float]] = {}
    for i, t in enumerate(arch["days"]):
        ym = t[:7]
        p = ser["precip"][i]
        if p is not None:
            p_sum[ym] = p_sum.get(ym, 0.0) + p
            p_days[ym] = p_days.get(ym, 0) + 1
        if ser["soil_moisture"][i] is not None:
            sm.setdefault(ym, []).append(ser["soil_moisture"][i])
        if ser["soil_wetness"][i] is not None:
            sw.setdefault(ym, []).append(ser["soil_wetness"][i])
    rows = []
    for ym in sorted(set(p_days) | set(sm) | set(sw)):
        y, m = int(ym[:4]), int(ym[5:7])
        ndays = (date(y + (m == 12), m % 12 + 1, 1) - date(y, m, 1)).days
        n = p_days.get(ym, 0)
        normal = normals["precip_mm"][m - 1] if normals else None
        rows.append({
            "month": ym,
            "precip_mm": round(p_sum[ym], 1) if n else None,
            # dla niepełnego miesiąca norma proporcjonalna do liczby dni z danymi
            "precip_normal_mm": None if normal is None or not n else round(normal * (n / ndays), 1),
            "soil_moisture_pct": _pct_mean(sm.get(ym, [])),     # ERA5-Land: % objętości (m³/m³ × 100)
            "soil_wetness_pct": _pct_mean(sw.get(ym, [])),      # NASA POWER: względne nasycenie strefy korzeniowej
            "complete": n >= ndays,
        })
    provider = arch["provider"]
    data_end = arch.get("data_end") or end_d.isoformat()
    if provider == era5_archive.PROVIDER_ERA5:
        prec_prov = provenance(
            era5_archive.ERA5_SOURCE, era5_archive.ERA5_URL, "reanaliza",
            fetched_at=arch.get("fetched_at"), resolution="ok. 9–25 km", observed_at=data_end,
            note=("Miesięczne sumy opadu (ERA5) i średnia wilgotność gleby 7–28 cm (ERA5-Land) z reanalizy Copernicus C3S "
                  f"(model z asymilacją pomiarów), nie pomiar na działce; dane od {start_d.isoformat()} do {data_end} "
                  "(opóźnienie archiwum ok. 5 dni; ostatnie ok. 3 miesiące to wstępna wersja ERA5T). "
                  "Dla niepełnego miesiąca norma przeliczona proporcjonalnie do liczby dni. Dane: Open-Meteo.com, CC BY 4.0; "
                  "zawiera zmodyfikowane dane Copernicus Climate Change Service."),
        )
    else:
        prec_prov = provenance(
            era5_archive.POWER_SOURCE, era5_archive.POWER_URL, "reanaliza",
            fetched_at=arch.get("fetched_at"), resolution="ok. 50 km (siatka 0,5° × 0,625°)", observed_at=data_end,
            note=(f"Archiwum ERA5 chwilowo niedostępne ({arch.get('fallback_reason') or 'brak odpowiedzi'}) – użyto zapasowej "
                  "reanalizy NASA POWER (MERRA-2 / GEOS-IT): opad dobowy PRECTOTCORR oraz wilgotność gleby jako WZGLĘDNE "
                  "nasycenie strefy korzeniowej (GWETROOT, 0–100%), a nie % objętości – nie porównywać z pojemnością polową "
                  f"i punktem więdnięcia. Rozdzielczość grubsza niż ERA5, niepewność większa. Dane od {arch['start']} do "
                  f"{data_end}. Źródło: NASA Langley Research Center POWER Project (dane publiczne)."),
        )
    res = {
        "months": rows,
        "source": provider,
        "data_end": data_end,
        "fallback_reason": arch.get("fallback_reason"),
        "normals_station": normals["station"] if normals else None,
        "provenance": {"precip": prec_prov, "normals": nprov},
    }
    _climate_cache.set(key, res, CLIMATE_TTL_S if provider == era5_archive.PROVIDER_ERA5 else CLIMATE_FALLBACK_TTL_S)
    return res

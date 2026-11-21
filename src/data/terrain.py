"""Nachylenie terenu działki z Numerycznego Modelu Terenu GUGiK (NMT, siatka 1 m, WCS 2.0.1).

Usługa: https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModelFormatTIFF
Coverage DTM_PL-KRON86-NH_TIFF, EPSG:2180, wysokości w m (PL-KRON86-NH), GeoTIFF float32.
Uwaga na osie: SUBSET=x(…) to easting, SUBSET=y(…) to northing (mimo axisLabels="y x" w DescribeCoverage).
Wartość dokładnie 0,0 to luka w modelu (np. wody) – traktujemy jako brak danych.
Duże działki pobieramy z mniejszą rozdzielczością (≤ MAX_PX na bok, SCALESIZE) – rozdzielczość trafia do proweniencji.
"""
from __future__ import annotations

import io
import logging
import math
from typing import Any, Dict, Optional

import numpy as np
from PIL import Image
from shapely.geometry.base import BaseGeometry

from src.core.cache import TTLCache
from src.data.cadastre import ParcelData, parcel_mask
from src.data.common import SOURCE_NMT, SourceError, friendly, http_request, iso_utc, provenance, record_error, record_ok

log = logging.getLogger(__name__)

WCS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModelFormatTIFF"
COVERAGE = "DTM_PL-KRON86-NH_TIFF"
MAX_PX = 1500
PAD_M = 3.0
TTL_S = 365 * 24 * 3600
_cache = TTLCache("nmt_slope", max_entries=20000, default_ttl=TTL_S)


def slope_stats(dem: np.ndarray, res_m: float, mask: np.ndarray) -> Optional[Dict[str, float]]:
    """Spadek [%] = 100·|∇z| (różnice centralne). Zwraca średnią, 90. percentyl i maks. w masce działki."""
    z = dem.astype(float)
    z[z == 0.0] = np.nan
    gy, gx = np.gradient(z, res_m)
    slope = 100.0 * np.hypot(gx, gy)
    vals = slope[mask & np.isfinite(slope)]
    total = int(mask.sum())
    if total == 0 or vals.size < max(4, 0.5 * total):
        return None
    zin = z[mask & np.isfinite(z)]
    return {"mean_pct": round(float(vals.mean()), 2), "p90_pct": round(float(np.percentile(vals, 90)), 2),
            "max_pct": round(float(vals.max()), 1), "elev_min_m": round(float(zin.min()), 1), "elev_max_m": round(float(zin.max()), 1),
            "valid_frac": round(vals.size / total, 3)}


def fetch_dem(bounds: tuple, px: int) -> np.ndarray:
    minx, miny, maxx, maxy = bounds
    params = {"SERVICE": "WCS", "VERSION": "2.0.1", "REQUEST": "GetCoverage", "CoverageId": COVERAGE, "FORMAT": "image/tiff",
              "SUBSET": [f"x({minx:.1f},{maxx:.1f})", f"y({miny:.1f},{maxy:.1f})"]}
    w, h = maxx - minx, maxy - miny
    if max(w, h) > px:
        params["SCALESIZE"] = f"x({max(2, round(w / max(w, h) * px))}),y({max(2, round(h / max(w, h) * px))})"
    resp = http_request("GET", WCS_URL, params=params, timeout=60.0, retries=1)
    if "tiff" not in (resp.headers.get("Content-Type") or "").lower():
        raise SourceError("NMT: usługa nie zwróciła GeoTIFF")
    img = Image.open(io.BytesIO(resp.content))
    if img.mode != "F":
        raise SourceError(f"NMT: nieoczekiwany typ rastra ({img.mode})")
    return np.array(img)


def parcel_slope(p: ParcelData) -> Dict[str, Any]:
    """{value: {mean_pct, p90_pct, …} | None, provenance}. Nie rzuca."""
    key = p.geometry_key() + ":v1"
    cached = _cache.get(key)
    if cached:
        return cached
    g: BaseGeometry = p.polygon_2180
    minx, miny, maxx, maxy = g.bounds
    minx, miny, maxx, maxy = math.floor(minx - PAD_M), math.floor(miny - PAD_M), math.ceil(maxx + PAD_M), math.ceil(maxy + PAD_M)
    try:
        dem = fetch_dem((minx, miny, maxx, maxy), MAX_PX)
    except (SourceError, OSError, ValueError) as exc:
        record_error(SOURCE_NMT, f"NMT: {friendly(exc)}")
        return {"value": None, "provenance": provenance("GUGiK NMT", WCS_URL, "brak", note=f"NMT niedostępny ({friendly(exc)}).")}
    record_ok(SOURCE_NMT)
    h, w = dem.shape
    res = (maxx - minx) / w
    xs = minx + (np.arange(w) + 0.5) * res
    ys = maxy - (np.arange(h) + 0.5) * ((maxy - miny) / h)
    mask = parcel_mask(g, xs, ys)
    stats = slope_stats(dem, res, mask)
    note = (f"Spadek terenu z NMT GUGiK (siatka 1 m, PL-KRON86-NH), liczony na siatce {res:.1f} m w granicach działki; "
            "średnia i 90. percentyl spadku [%].")
    if stats is None:
        out = {"value": None, "provenance": provenance("GUGiK NMT", WCS_URL, "brak", note="NMT bez wartości dla większości działki (luka w modelu).")}
    else:
        out = {"value": stats, "provenance": provenance("GUGiK NMT – numeryczny model terenu", WCS_URL, "pomiar", note=note,
                                                        fetched_at=iso_utc(), resolution=f"{res:.1f} m")}
    _cache.set(key, out)
    return out


def probe() -> str:
    resp = http_request("GET", WCS_URL, params={"SERVICE": "WCS", "REQUEST": "GetCapabilities"}, timeout=10.0, retries=0)
    if COVERAGE not in resp.text:
        raise SourceError("NMT: brak pokrycia DTM w GetCapabilities")
    return "OK"

"""Indeksy Sentinel-2 (NDVI, NDMI, NDWI) dla wyników „Filtruj działki” – JEDNYM zapytaniem dla wszystkich działek wyników.

Działki spełniające i „do sprawdzenia” łączymy w jedną geometrię (cadastre.merge_parcels) i liczymy dla niej to samo co
dla gospodarstwa: sentinel.analyze_current – najnowsza scena z ≥ MIN_VALID_FRACTION obszaru bez chmur, jedno zapytanie
Statistics API (wybór dnia), jedno Process API (raster 10 m z maską poligonów wyników) i katalog scen. Cache jak
w sentinel.py (klucz = geometria wyników), single-flight per klucz. Obszar wyników ograniczony do MAX_SPAN_KM × MAX_SPAN_KM;
przy większym – indeks liczony tylko dla działek w widocznym fragmencie mapy (parametr bbox).
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import box

from src.data import sentinel
from src.data.cadastre import ParcelData, merge_parcels
from src.data.common import missing

MAX_SPAN_KM = 10.0
MAX_PARCELS = 2000
_locks: Dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _guard:
        if len(_locks) > 2000:
            _locks.clear()
        return _locks.setdefault(key, threading.Lock())


def span_km(bounds: Tuple[float, float, float, float]) -> float:
    w, s, e, n = bounds
    lat = (s + n) / 2
    return max((e - w) * 111.32 * math.cos(math.radians(lat)), (n - s) * 110.57)


def results_index(parcels: List[ParcelData], view: Optional[Tuple[float, float, float, float]] = None,
                  max_span_km: float = MAX_SPAN_KM) -> Dict[str, Any]:
    """{layers: {ndvi, ndmi, ndwi}, acquired_on, provenance, count, span_km, too_large, seconds}. Nie rzuca."""
    t0 = time.monotonic()
    ps = parcels[:MAX_PARCELS]
    if view is not None:
        vb = box(*view)
        ps = [p for p in ps if p.polygon_wgs84.intersects(vb)]
    if not ps:
        return {"layers": None, "count": 0, "too_large": False,
                "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, "Brak działek wyników w tym fragmencie mapy.")}
    merged = merge_parcels(ps)
    sp = span_km(merged.polygon_wgs84.bounds)
    if sp > max_span_km:
        return {"layers": None, "count": len(ps), "span_km": round(sp, 1), "too_large": True,
                "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL,
                                      f"Działki wyników zajmują obszar ok. {sp:.0f} km – indeks liczymy dla obszaru do "
                                      f"{MAX_SPAN_KM:.0f} × {MAX_SPAN_KM:.0f} km. Przybliż mapę.")}
    key = merged.geometry_key()
    with _lock(key):
        res = sentinel.analyze_current(merged)   # cache sentinel (klucz = geometria) – drugi raz bez zapytań do CDSE
    layers = {"ndvi": res.get("layer"), "ndmi": res.get("layer_ndmi"), "ndwi": res.get("layer_ndwi")}
    nd = res.get("ndvi") or {}
    # statystyki z tych samych pikseli co mapa (maska poligonów wyników, 10 m): NDVI – średnia, min, maks (Statistics API);
    # NDMI i NDWI – tylko średnia z rastra (min/maks nie są liczone – nie podajemy ich)
    stats = {"ndvi": {"mean": nd.get("mean"), "min": nd.get("min"), "max": nd.get("max")},
             "ndmi": {"mean": (layers["ndmi"] or {}).get("mean")}, "ndwi": {"mean": (layers["ndwi"] or {}).get("mean")}}
    return {"layers": layers if any(layers.values()) else None, "acquired_on": nd.get("acquired_on"),
            "mean_ndvi": nd.get("mean"), "stats": stats, "area_ha": round(sum(p.area_ha for p in ps), 2),
            "provenance": res.get("provenance"), "warnings": res.get("warnings") or [],
            "count": len(ps), "span_km": round(sp, 1), "too_large": False, "seconds": round(time.monotonic() - t0, 2)}


# --------------------------------------------------------------------------------------
# Skupiska ≤ 10 × 10 km – indeks dla wyników o dużym zasięgu (każde skupisko osobno, ta sama rozdzielczość 10 m)
# --------------------------------------------------------------------------------------
CLUSTER_M = 10_000.0


def clusters(parcels: List[ParcelData]) -> List[Dict[str, Any]]:
    """Działki pogrupowane w komórki siatki 10 km (EPSG:2180, wg środka działki). Kolejność i identyfikatory stałe."""
    cells: Dict[Tuple[int, int], List[int]] = {}
    for k, p in enumerate(parcels[:MAX_PARCELS]):
        c = p.polygon_2180.centroid
        cells.setdefault((int(c.x // CLUSTER_M), int(c.y // CLUSTER_M)), []).append(k)
    out = []
    for (i, j), members in sorted(cells.items()):
        bs = [parcels[k].polygon_wgs84.bounds for k in members]
        bounds = (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))
        out.append({"id": f"{i}_{j}", "count": len(members), "bbox": [round(v, 6) for v in bounds], "members": members})
    return out


def cluster_index(parcels: List[ParcelData], cluster_id: str) -> Dict[str, Any]:
    """Indeks dla jednego skupiska (jedno zapytanie CDSE dla wszystkich działek skupiska)."""
    for c in clusters(parcels):
        if c["id"] == cluster_id:
            # komórka 10 km + zasięg działek przy jej brzegu – dopuszczamy do 12 km (raster nadal 10 m)
            r = results_index([parcels[k] for k in c["members"]], max_span_km=12.0)
            r["cluster"] = cluster_id
            return r
    return {"layers": None, "count": 0, "cluster": cluster_id, "too_large": False,
            "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, "Nie ma takiego skupiska działek.")}

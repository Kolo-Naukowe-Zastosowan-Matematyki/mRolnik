"""Obszary zagrożenia powodziowego (MZP) z ISOK / PGW Wody Polskie – WFS INSPIRE.

Usługa: https://wody.isok.gov.pl/wss/INSPIRE/INSPIRE_NZ_HY_MZPMRP_WFS, warstwa nz-core:HazardArea.
Scenariusz: likelihoodOfOccurrence → returnPeriod (10 = Q10%, 100 = Q1%, 500 = Q0,2%); opis w qualitativeLikelihood.
Wymagane outputFormat=application/json (GML z BBOX kończy się po stronie serwera błędem NullPointerException);
bbox bez CRS = EPSG:4258 w kolejności szerokość, długość. Poligony są olbrzymie (do 60 tys. wierzchołków), dlatego
pytamy raz na kafel 0,05°, przycinamy do kafla, upraszczamy do 1 m (EPSG:2180) i trzymamy w cache 90 dni.

Brak poligonu = działka poza obszarami z map zagrożenia powodziowego (mapy obejmują wybrane rzeki i wybrzeże);
to nie jest „brak danych”, ale opis w proweniencji to mówi wprost.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Any, Dict, List, Optional, Tuple

import shapely
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry

from src.core.cache import TTLCache
from src.data.cadastre import ParcelData, reproject, transformer_to_2180
from src.data.common import SOURCE_ISOK, SourceError, friendly, http_request, iso_utc, provenance, record_error, record_ok

log = logging.getLogger(__name__)

WFS_URL = "https://wody.isok.gov.pl/wss/INSPIRE/INSPIRE_NZ_HY_MZPMRP_WFS"
INFO_URL = "https://wody.isok.gov.pl/imap_kzgw/?gpmap=gpMZP"
TILE_DEG = 0.05
TTL_S = 90 * 24 * 3600
SIMPLIFY_M = 1.0
PAGE = 50
_cache = TTLCache("isok_tiles_v2", max_entries=20000, default_ttl=TTL_S)
_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def tiles_for(bounds_wgs84: Tuple[float, float, float, float]) -> List[Tuple[int, int]]:
    w, s, e, n = bounds_wgs84
    return [(i, j) for i in range(math.floor(w / TILE_DEG), math.floor(e / TILE_DEG) + 1)
            for j in range(math.floor(s / TILE_DEG), math.floor(n / TILE_DEG) + 1)]


def _return_period(props: Dict[str, Any]) -> Optional[float]:
    lo = ((props.get("likelihoodOfOccurrence") or {}).get("LikelihoodOfOccurrence") or {})
    q = ((lo.get("quantitativeLikelihood") or {}).get("QuantitativeLikelihood") or {})
    rp = q.get("returnPeriod")
    try:
        return float(rp) if rp is not None else None
    except (TypeError, ValueError):
        return None


def parse_features(data: Dict[str, Any], tile_2180: BaseGeometry) -> List[Dict[str, Any]]:
    """GeoJSON (lon, lat EPSG:4258 ≈ WGS84) → [{rp, label, category, wkb(hex), valid_to}] przycięte do kafla w EPSG:2180."""
    out = []
    for f in data.get("features") or []:
        props = f.get("properties") or {}
        rp = _return_period(props)
        if rp is None or not f.get("geometry"):
            continue
        try:
            g = reproject(shape(f["geometry"]), transformer_to_2180)
            if not g.is_valid:
                g = shapely.make_valid(g)
            g = g.intersection(tile_2180)
            if g.is_empty:
                continue
            g = shapely.simplify(g, SIMPLIFY_M, preserve_topology=True)
        except (ValueError, shapely.errors.ShapelyError) as exc:
            log.warning("ISOK: pominięto uszkodzoną geometrię: %s", exc)
            continue
        lo = ((props.get("likelihoodOfOccurrence") or {}).get("LikelihoodOfOccurrence") or {})
        cat = (((props.get("typeOfHazard") or {}).get("hazardCategory") or {}).get("@title")) or ""
        out.append({"rp": rp, "label": lo.get("qualitativeLikelihood") or "", "category": cat,
                    "valid_to": ((props.get("validityPeriod") or {}).get("endPosition")),
                    "wkb": shapely.to_wkb(g, hex=True)})
    return out


def _fetch_tile(i: int, j: int) -> Dict[str, Any]:
    w, s = i * TILE_DEG, j * TILE_DEG
    e, n = w + TILE_DEG, s + TILE_DEG
    tile_2180 = reproject(box(w, s, e, n), transformer_to_2180)
    feats: List[Dict[str, Any]] = []
    start = 0
    while True:
        params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": "nz-core:HazardArea",
                  "outputFormat": "application/json", "bbox": f"{s:.4f},{w:.4f},{n:.4f},{e:.4f}",
                  "count": str(PAGE), "startIndex": str(start)}
        resp = http_request("GET", WFS_URL, params=params, timeout=120.0, retries=1)
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceError("ISOK: odpowiedź nie jest JSON") from exc
        feats += parse_features(data, tile_2180)
        got = int(data.get("numberReturned") or len(data.get("features") or []))
        matched = data.get("numberMatched")
        start += got
        if got < PAGE or (isinstance(matched, int) and start >= matched):
            break
    return {"zones": feats, "fetched_at": iso_utc()}


def tile(i: int, j: int) -> Dict[str, Any]:
    key = f"{i},{j}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    with _lock(key):
        cached = _cache.get(key)
        if cached is not None:
            return cached
        try:
            res = _fetch_tile(i, j)
        except SourceError as exc:
            record_error(SOURCE_ISOK, f"ISOK: {friendly(exc)}")
            raise
        record_ok(SOURCE_ISOK)
        _cache.set(key, res)
        return res


def flood_share(p: ParcelData) -> Dict[str, Any]:
    """{value: {q10_pct, q1_pct, q02_pct}, provenance}; value None + kind 'brak' przy błędzie usługi. Nie rzuca."""
    g = p.polygon_2180
    try:
        tiles = [tile(i, j) for i, j in tiles_for(p.polygon_wgs84.bounds)]
    except SourceError as exc:
        return {"value": None, "provenance": provenance("ISOK – mapy zagrożenia powodziowego", INFO_URL, "brak",
                                                        note=f"Usługa WFS ISOK niedostępna ({friendly(exc)}).")}
    by_rp: Dict[str, List[BaseGeometry]] = {"q10": [], "q1": [], "q02": []}
    valid_to = set()
    for t in tiles:
        for z in t["zones"]:
            zg = shapely.from_wkb(z["wkb"])
            if not zg.intersects(g):
                continue
            rp = z["rp"]
            if z.get("valid_to"):
                valid_to.add(z["valid_to"])
            if rp <= 10:
                by_rp["q10"].append(zg)
            if rp <= 100:
                by_rp["q1"].append(zg)
            if rp <= 500:
                by_rp["q02"].append(zg)
    area = g.area or 1.0
    val = {k: round(100.0 * shapely.union_all(v).intersection(g).area / area, 1) if v else 0.0 for k, v in by_rp.items()}
    fetched = min((t.get("fetched_at") for t in tiles if t.get("fetched_at")), default=None)
    note = ("Udział powierzchni działki w obszarach szczególnego zagrożenia powodzią z map zagrożenia powodziowego (MZP, "
            "ISOK/PGW Wody Polskie): Q10% – woda raz na 10 lat, Q1% – raz na 100 lat; Q0,2% – obszar zagrożenia raz na 500 lat. "
            "Mapy obejmują wybrane rzeki i wybrzeże: 0% oznacza brak działki w obszarach z map, a nie gwarancję, że nie bywa zalewana. "
            "Geometrie uproszczone do 1 m.")
    if valid_to:
        note += f" Okres ważności map do {', '.join(sorted(valid_to))}."
    return {"value": val, "provenance": provenance("ISOK – mapy zagrożenia powodziowego (PGW Wody Polskie)", WFS_URL, "pomiar",
                                                   note=note, fetched_at=fetched, resolution="mapy 1:10 000")}


def probe() -> str:
    resp = http_request("GET", WFS_URL, params={"service": "WFS", "request": "GetCapabilities"}, timeout=15.0, retries=0)
    if "HazardArea" not in resp.text:
        raise SourceError("ISOK: brak warstwy HazardArea w GetCapabilities")
    return "OK"

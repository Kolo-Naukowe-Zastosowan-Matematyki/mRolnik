"""Cieki, kanały, rowy i wody powierzchniowe z BDOT10k (GUGiK) dla fragmentu mapy – do podświetlenia po najechaniu.

Dane z tego samego cache co filtr działek (bdot10k.py: SWRS_L, SWKN_L, SWRM_L, PTWP_A, pełne pliki GML powiatów).
Fragment (bbox) ograniczony do MAX_SPAN_KM × MAX_SPAN_KM i zoomu ≥ MIN_ZOOM; frontend prosi o stałe kafle, więc
odpowiedzi są buforowane per kafel i zoom. Geometria przycięta do kafla, w WGS84, uproszczona najwyżej do pół piksela
ekranu przy danym zoomie (BDOT10k w cache ma już uproszczenie 1 m). Obiekt przecięty granicą kafla ma ten sam `key`
we wszystkich kaflach – frontend podświetla wtedy cały ciek.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import shapely
import shapely.wkt
from shapely.geometry import box, mapping

from src.data import bdot10k
from src.data.cadastre import reproject, transformer_to_2180, transformer_to_wgs84
from src.data.common import SourceError, missing

log = logging.getLogger(__name__)

MIN_ZOOM = 13
MAX_SPAN_KM = 8.5
MAX_FEATURES = 4000
CLASSES = ("SWRS_L", "SWKN_L", "SWRM_L", "PTWP_A")
KIND = {"SWRS_L": "ciek", "SWKN_L": "kanał", "SWRM_L": "rów melioracyjny", "PTWP_A": "woda powierzchniowa"}
_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_CACHE_MAX = 400
_LOCK = threading.Lock()


class TooLarge(ValueError):
    """Fragment mapy za duży albo zoom za mały – „przybliż mapę”."""


def span_km(bbox: Tuple[float, float, float, float]) -> Tuple[float, float]:
    w, s, e, n = bbox
    lat = (s + n) / 2
    return (e - w) * 111.32 * math.cos(math.radians(lat)), (n - s) * 110.57


def tolerance_m(zoom: int, lat: float) -> float:
    """Pół piksela ekranu (kafle 256 px, Web Mercator) – uproszczenie niewidoczne na mapie."""
    return 0.5 * 156543.03 * math.cos(math.radians(lat)) / (2 ** zoom)


def _date(meta: Dict[str, Any]) -> Optional[str]:
    lm = meta.get("last_modified")
    if not lm:
        return None
    try:
        return parsedate_to_datetime(lm).date().isoformat()
    except (TypeError, ValueError):
        return None


def counties_for_bbox(bbox: Tuple[float, float, float, float]) -> List[str]:
    """Powiaty przecinające bbox: ULDK GetCountyByXY w środku, narożnikach i środkach boków (cache 90 dni);
    punkt już pokryty granicą znalezionego powiatu nie jest pytany ponownie."""
    from src.data import parcel_search
    w, s, e, n = bbox
    pts = [((s + n) / 2, (w + e) / 2), (s, w), (s, e), (n, w), (n, e), (s, (w + e) / 2), (n, (w + e) / 2), ((s + n) / 2, w), ((s + n) / 2, e)]
    out: List[str] = []
    geoms = []
    for lat, lon in pts:
        p = shapely.Point(lon, lat)
        if any(g.covers(p) for g in geoms):
            continue
        try:
            c = parcel_search._county_at(lat, lon)
        except SourceError:
            continue
        if not c or c["teryt"] in out:
            continue
        out.append(c["teryt"])
        try:
            geoms.append(shapely.wkt.loads(c["wkt"]))
        except (ValueError, shapely.errors.ShapelyError):
            pass
    return out


def _feature(cls: str, teryt: str, idx: int, geom, attrs: Dict[str, Any], version: Optional[str]) -> Dict[str, Any]:
    props: Dict[str, Any] = {"key": f"{teryt}:{cls}:{idx}", "cls": cls, "kind": KIND[cls], "version": version}
    name = attrs.get("nazwa")
    if name:
        props["name"] = name
    if attrs.get("rodzaj"):
        props["rodzaj"] = attrs["rodzaj"]
    if attrs.get("identyfikatorMPHP"):
        props["mphp"] = attrs["identyfikatorMPHP"]
    if attrs.get("szerokosc"):
        try:
            props["width_m"] = float(attrs["szerokosc"])
        except ValueError:
            pass
    return {"type": "Feature", "properties": props, "geometry": mapping(geom)}


def _round(obj: Any) -> Any:
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, (list, tuple)):
        return [_round(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _round(v) for k, v in obj.items()}
    return obj


def features(bbox: Tuple[float, float, float, float], zoom: int) -> Dict[str, Any]:
    """{features: [...GeoJSON...], count, truncated, provenance, seconds, cached}. Rzuca TooLarge."""
    w, s, e, n = bbox
    if not (w < e and s < n):
        raise ValueError("niepoprawny zasięg mapy")
    if zoom < MIN_ZOOM:
        raise TooLarge(f"Przybliż mapę (zoom co najmniej {MIN_ZOOM}), aby wskazać ciek.")
    sx, sy = span_km(bbox)
    if max(sx, sy) > MAX_SPAN_KM:
        raise TooLarge(f"Fragment mapy ok. {max(sx, sy):.0f} km – cieki wskazujemy dla fragmentu do {MAX_SPAN_KM:.0f} km. Przybliż mapę.")
    zb = min(int(zoom), 17)   # powyżej 17 uproszczenie i tak poniżej 1 m (dane w cache uproszczone do 1 m)
    key = f"{w:.5f},{s:.5f},{e:.5f},{n:.5f}@{zb}"
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return dict(hit, cached=True, seconds=0.0)
    t0 = time.monotonic()
    tile_wgs = box(w, s, e, n)
    tile = reproject(tile_wgs, transformer_to_2180)
    tol = tolerance_m(zb, (s + n) / 2)
    teryts = counties_for_bbox(bbox)
    if not teryts:
        return {"features": [], "count": 0, "truncated": False, "cached": False, "seconds": round(time.monotonic() - t0, 2),
                "provenance": missing("GUGiK BDOT10k", bdot10k.SOURCE_PAGE, "Nie ustalono powiatu dla tego fragmentu mapy (ULDK).")}
    feats: List[Dict[str, Any]] = []
    layers_used = []
    truncated = False
    for teryt in teryts:
        lays = bdot10k.ensure_layers(teryt, CLASSES)
        for cls in CLASSES:
            lay = lays.get(cls)
            if lay is None or not len(lay.geoms):
                continue
            layers_used.append(lay)
            idx = lay.tree.query(tile, predicate="intersects")
            if not len(idx):
                continue
            idx = np.sort(idx)
            clipped = shapely.intersection(lay.geoms[idx], tile)
            simp = shapely.simplify(clipped, tol, preserve_topology=True) if tol > 0.05 else clipped
            ver = _date(lay.meta)
            keep_i, keep_g = [], []
            for i, g in zip(idx, simp):
                if g is None or g.is_empty:
                    continue
                if g.geom_type == "GeometryCollection":
                    parts = [p for p in g.geoms if p.geom_type in ("LineString", "MultiLineString", "Polygon", "MultiPolygon")]
                    if not parts:
                        continue
                    g = shapely.union_all(parts)
                if g.geom_type in ("Point", "MultiPoint"):
                    continue
                if len(feats) + len(keep_i) >= MAX_FEATURES:
                    truncated = True
                    break
                keep_i.append(int(i))
                keep_g.append(g)
            if keep_g:
                wgs = reproject(np.array(keep_g, dtype=object), transformer_to_wgs84)   # jedno przeliczenie dla całej klasy
                feats.extend(_feature(cls, teryt, i, g, lay.attrs[i], ver) for i, g in zip(keep_i, wgs))
            if truncated:
                break
    feats = _round(feats)
    note = (f"Cieki, kanały i rowy (SWRS_L, SWKN_L, SWRM_L) i wody powierzchniowe (PTWP_A) przycięte do fragmentu mapy, "
            f"uproszczone do {tol:.1f} m (pół piksela przy zoomie {zb}).")
    res = {"features": feats, "count": len(feats), "truncated": truncated, "cached": False,
           "provenance": bdot10k.layer_provenance(layers_used, note=note), "seconds": round(time.monotonic() - t0, 2)}
    with _LOCK:
        _CACHE[key] = res
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    log.info("Cieki BDOT10k: kafel %s – %d obiektów, %.2f s", key, len(feats), res["seconds"])
    return res

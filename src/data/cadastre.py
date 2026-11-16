"""Działki ewidencyjne z usługi GUGiK ULDK (Usługa Lokalizacji Działek Katastralnych).

Dokumentacja usługi: https://uldk.gugik.gov.pl/opis.html
- GetParcelByXY: działka zawierająca punkt (xy=x,y,SRID; dla EPSG:4326 x=długość, y=szerokość),
- GetParcelById: działka po identyfikatorze TERYT (np. 302505_5.0014.114/1).
Odpowiedź tekstowa: pierwsza linia to status ("0" = sukces, "-1 ..." = brak/błąd), kolejna linia to pola
rozdzielone "|" w kolejności parametru `result`. Geometria w WKT z prefiksem SRID (np. "SRID=2180;POLYGON(...)").

Nigdy nie tworzymy działki zastępczej: gdy ULDK nie odpowie, zwracamy None, a analiza oznacza dane jako "brak".
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import shapely
from pyproj import Transformer
from shapely import wkt as shapely_wkt
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from src.core.cache import TTLCache
from src.data.common import (
    friendly,
    SOURCE_ULDK,
    SourceError,
    http_request,
    iso_utc,
    provenance,
    record_error,
    record_ok,
)
from src.data.presets import FARM_PRESETS  # noqa: F401  (zachowana ścieżka importu: src.data.cadastre.FARM_PRESETS)

log = logging.getLogger(__name__)

ULDK_URL = "https://uldk.gugik.gov.pl/"
ULDK_RESULT_FIELDS = "id,geom_wkt,voivodeship,county,commune,region"
ULDK_TIMEOUT_S = 5.0
ULDK_TTL_S = 30 * 24 * 3600

transformer_to_wgs84 = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)
transformer_to_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)

# Identyfikator TERYT działki: WWPPGG_R.OOOO.NR (numer może zawierać "/" i litery)
PARCEL_ID_RE = re.compile(r"^\d{6}_\d\.\d{4}(\.[0-9A-Za-z/\-]{1,30})?$")

_id_cache = TTLCache("uldk_id", max_entries=20000, default_ttl=ULDK_TTL_S)
_pt_cache = TTLCache("uldk_pt", max_entries=50000, default_ttl=ULDK_TTL_S)

# Indeks przestrzenny w pamięci: działki pobrane w tej sesji (do trafień punkt-w-wielokącie)
_INDEX_LOCK = threading.Lock()
_INDEX: Dict[str, "ParcelData"] = {}
_INDEX_MAX = 5000


@dataclass
class ParcelData:
    parcel_id: str             # TERYT, np. 302505_5.0014.114/1
    voivodeship: str
    county: str
    commune: str
    region: str                # obręb ewidencyjny
    area_ha: float             # powierzchnia geometryczna z EPSG:2180
    centroid_lat: float
    centroid_lon: float
    polygon_wgs84: BaseGeometry
    polygon_2180: BaseGeometry
    fetched_at: Optional[str] = None

    # --- serializacja do cache (JSON, bez pickle) ---
    def to_json(self) -> Dict[str, Any]:
        return {
            "parcel_id": self.parcel_id,
            "voivodeship": self.voivodeship,
            "county": self.county,
            "commune": self.commune,
            "region": self.region,
            "wkt_2180": self.polygon_2180.wkt,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "ParcelData":
        return _build_parcel(
            d["parcel_id"], d["wkt_2180"], d.get("voivodeship", ""), d.get("county", ""),
            d.get("commune", ""), d.get("region", ""), fetched_at=d.get("fetched_at"),
        )

    def exterior_coords_lonlat(self) -> List[List[float]]:
        """Zewnętrzny pierścień największego wielokąta jako [[lon, lat], ...] (dla GeoJSON/Leaflet)."""
        geom = self.polygon_wgs84
        if isinstance(geom, MultiPolygon):
            geom = max(geom.geoms, key=lambda g: g.area)
        if not isinstance(geom, Polygon):
            return []
        return [[round(x, 7), round(y, 7)] for x, y in geom.exterior.coords]

    def geometry_key(self) -> str:
        return hashlib.sha1(self.polygon_2180.wkt.encode("utf-8")).hexdigest()[:16]


def reproject(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    """Przeliczenie geometrii między układami (wektorowo, shapely.transform)."""
    def fn(arr: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(arr[:, 0], arr[:, 1])
        return np.column_stack([x, y])
    return shapely.transform(geom, fn)


def _build_parcel(parcel_id: str, raw_wkt: str, voiv: str, county: str, commune: str, region: str,
                  fetched_at: Optional[str] = None) -> ParcelData:
    geom_2180 = shapely_wkt.loads(raw_wkt)
    if geom_2180.is_empty or geom_2180.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("geometria działki nie jest wielokątem")
    geom_wgs84 = reproject(geom_2180, transformer_to_wgs84)
    c = geom_wgs84.centroid
    return ParcelData(
        parcel_id=parcel_id,
        voivodeship=voiv,
        county=county,
        commune=commune,
        region=region,
        area_ha=round(float(geom_2180.area) / 10000.0, 4),
        centroid_lat=float(c.y),
        centroid_lon=float(c.x),
        polygon_wgs84=geom_wgs84,
        polygon_2180=geom_2180,
        fetched_at=fetched_at,
    )


def parse_uldk_response(text: str) -> Optional[ParcelData]:
    """Parsuje odpowiedź ULDK (status w 1. linii, potem 'id|wkt|woj|powiat|gmina|obręb').

    Zwraca None przy statusie innym niż 0 lub braku wyników. Rzuca ValueError przy uszkodzonej odpowiedzi.
    """
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if not lines:
        raise ValueError("pusta odpowiedź ULDK")
    if lines[0] != "0":
        return None  # np. "-1 brak wyników"
    if len(lines) < 2:
        return None
    parts = lines[1].split("|")
    if len(parts) < 2:
        raise ValueError("niepełny rekord ULDK")
    parts += [""] * (6 - len(parts))
    parcel_id, raw_geom, voiv, county, commune, region = (p.strip() for p in parts[:6])
    srid = "2180"
    if raw_geom.upper().startswith("SRID="):
        head, _, raw_geom = raw_geom.partition(";")
        srid = head.split("=", 1)[1].strip()
    if srid != "2180":
        # Prosimy zawsze o srid=2180; inne układy przeliczamy do 2180
        tr = Transformer.from_crs(f"EPSG:{srid}", "EPSG:2180", always_xy=True)
        geom = reproject(shapely_wkt.loads(raw_geom), tr)
        raw_geom = geom.wkt
    return _build_parcel(parcel_id, raw_geom, voiv, county, commune, region, fetched_at=iso_utc())


def _uldk_call(params: Dict[str, str]) -> Optional[ParcelData]:
    """Wywołanie ULDK z urlencode (requests params), timeout 5 s, 1 ponowienie."""
    query = {"result": ULDK_RESULT_FIELDS, "srid": "2180", **params}
    try:
        resp = http_request("GET", ULDK_URL, params=query, timeout=ULDK_TIMEOUT_S, retries=1)
        resp.encoding = resp.encoding or "utf-8"
        parcel = parse_uldk_response(resp.text)
    except (SourceError, ValueError, shapely.errors.ShapelyError) as exc:
        record_error(SOURCE_ULDK, f"Błąd ULDK: {friendly(exc)}")
        log.warning("ULDK %s: %s", params.get("request"), exc)
        raise SourceError(str(exc)) from exc
    record_ok(SOURCE_ULDK)
    return parcel


def _index_add(p: ParcelData) -> None:
    with _INDEX_LOCK:
        if len(_INDEX) >= _INDEX_MAX:
            _INDEX.pop(next(iter(_INDEX)))
        _INDEX[p.parcel_id] = p


def _index_lookup(lon: float, lat: float) -> Optional[ParcelData]:
    pt = Point(lon, lat)
    with _INDEX_LOCK:
        candidates = list(_INDEX.values())
    for p in candidates:
        minx, miny, maxx, maxy = p.polygon_wgs84.bounds
        if minx <= lon <= maxx and miny <= lat <= maxy and p.polygon_wgs84.covers(pt):
            return p
    return None


def _remember(p: ParcelData) -> None:
    _id_cache.set(p.parcel_id, p.to_json())
    _index_add(p)


def fetch_parcel_by_xy(lon: float, lat: float, timeout: float = ULDK_TIMEOUT_S) -> Optional[ParcelData]:
    """Działka ewidencyjna zawierająca punkt WGS84. None = brak działki lub brak odpowiedzi ULDK.

    Cache: (1) działka z pamięci, której wielokąt zawiera punkt; (2) zapamiętane trafienie dokładnie tego
    punktu (6 miejsc po przecinku); (3) zapytanie do ULDK. Bez przybliżeń "w promieniu".
    """
    hit = _index_lookup(lon, lat)
    if hit is not None:
        return hit
    pkey = f"{lon:.6f},{lat:.6f}"
    pid = _pt_cache.get(pkey)
    if pid:
        cached = _id_cache.get(pid)
        if cached:
            try:
                p = ParcelData.from_json(cached)
                if p.polygon_wgs84.covers(Point(lon, lat)):
                    _index_add(p)
                    return p
            except (ValueError, KeyError, shapely.errors.ShapelyError) as exc:
                log.warning("Uszkodzony wpis cache ULDK %s: %s", pid, exc)
    try:
        p = _uldk_call({"request": "GetParcelByXY", "xy": f"{lon:.6f},{lat:.6f},4326"})
    except SourceError:
        return None
    if p is None:
        return None
    _remember(p)
    _pt_cache.set(pkey, p.parcel_id)
    return p


def fetch_parcel_by_id(parcel_id: str) -> Optional[ParcelData]:
    """Działka po identyfikatorze TERYT. None = niepoprawny identyfikator, brak działki lub brak odpowiedzi ULDK."""
    clean_id = (parcel_id or "").strip()
    if not clean_id or len(clean_id) > 60 or not PARCEL_ID_RE.match(clean_id):
        return None
    cached = _id_cache.get(clean_id)
    if cached:
        try:
            p = ParcelData.from_json(cached)
            _index_add(p)
            return p
        except (ValueError, KeyError, shapely.errors.ShapelyError) as exc:
            log.warning("Uszkodzony wpis cache ULDK %s: %s", clean_id, exc)
    try:
        p = _uldk_call({"request": "GetParcelById", "id": clean_id})
    except SourceError:
        return None
    if p is None:
        return None
    _remember(p)
    if p.parcel_id != clean_id:
        _id_cache.set(clean_id, p.to_json())
    return p


def parcel_provenance(p: Optional[ParcelData], note_if_missing: str = "") -> Dict[str, Any]:
    if p is None:
        return provenance(
            "GUGiK ULDK (EGiB)", "https://uldk.gugik.gov.pl/", "brak",
            note=note_if_missing or "Nie udało się pobrać granic działki z ULDK – działka nie jest wyświetlana ani zmyślana.",
        )
    return provenance(
        "GUGiK ULDK – Ewidencja Gruntów i Budynków", "https://uldk.gugik.gov.pl/", "pomiar",
        fetched_at=p.fetched_at,
        note="Granice i identyfikator działki z ewidencji gruntów (GUGiK ULDK); powierzchnia geometryczna policzona w EPSG:2180 (może różnić się od powierzchni ewidencyjnej).",
    )


def parcel_mask(polygon: BaseGeometry, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Wektoryzowana maska punkt-w-wielokącie (shapely.contains_xy) dla siatki xs × ys (te same jednostki co wielokąt).

    Zwraca tablicę bool o kształcie (len(ys), len(xs)) – wiersze = y.
    """
    X, Y = np.meshgrid(xs, ys)
    return shapely.contains_xy(polygon, X, Y)


def reset_memory_index() -> None:
    """Tylko dla testów."""
    with _INDEX_LOCK:
        _INDEX.clear()


__all__ = [
    "ParcelData", "FARM_PRESETS", "fetch_parcel_by_xy", "fetch_parcel_by_id", "merge_parcels", "parse_uldk_response",
    "parcel_provenance", "parcel_mask", "PARCEL_ID_RE",
]


def merge_parcels(parcels: List[ParcelData]) -> ParcelData:
    """Kilka działek jako jedna geometria (MultiPolygon). Statystyka Sentinel-2 liczona na takiej geometrii to średnia
    ze wszystkich pikseli zaznaczonych działek, czyli średnia ważona powierzchnią; klatka mapy obejmuje wszystkie działki."""
    if not parcels:
        raise ValueError("Brak działek do połączenia.")
    if len(parcels) == 1:
        return parcels[0]
    from shapely.ops import unary_union

    ps = sorted(parcels, key=lambda x: x.parcel_id)
    g2180 = unary_union([x.polygon_2180 for x in ps])
    gwgs = unary_union([x.polygon_wgs84 for x in ps])
    c = gwgs.centroid
    first = ps[0]
    return ParcelData(
        parcel_id=first.parcel_id, voivodeship=first.voivodeship, county=first.county, commune=first.commune,
        region=first.region, area_ha=g2180.area / 10_000.0, centroid_lat=c.y, centroid_lon=c.x,
        polygon_wgs84=gwgs, polygon_2180=g2180, fetched_at=first.fetched_at,
    )


def bbox_wgs84(p: ParcelData, pad_frac: float = 0.05) -> Tuple[float, float, float, float]:
    minx, miny, maxx, maxy = p.polygon_wgs84.bounds
    dx, dy = (maxx - minx) * pad_frac, (maxy - miny) * pad_frac
    return (minx - dx, miny - dy, maxx + dx, maxy + dy)

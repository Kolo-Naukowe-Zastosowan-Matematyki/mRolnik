"""Działki ewidencyjne z usług GUGiK: ULDK oraz zbiorczej usługi EGiB WFS (fallback).

Dokumentacja ULDK: https://uldk.gugik.gov.pl/opis.html
- GetParcelByXY: działka zawierająca punkt (xy=x,y,SRID; dla EPSG:4326 x=długość, y=szerokość),
- GetParcelById: działka po identyfikatorze TERYT (np. 302505_5.0014.114/1).
Odpowiedź tekstowa: pierwsza linia to status ("0" = sukces, "-1 ..." = brak/błąd), kolejna linia to pola
rozdzielone "|" w kolejności parametru `result`. Geometria w WKT z prefiksem SRID (np. "SRID=2180;POLYGON(...)").

Gdy ULDK nie odpowiada albo zwraca uszkodzoną odpowiedź, automatycznie odpytujemy zbiorczą usługę WFS EGiB GUGiK
(https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza, warstwa ms:dzialki, EPSG:2180).
Nigdy nie tworzymy działki zastępczej: gdy obie usługi GUGiK nie odpowiedzą, zwracamy None, a analiza oznacza dane jako "brak".
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

import numpy as np
import shapely
from pyproj import Transformer
from shapely import wkt as shapely_wkt
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from src.core.cache import TTLCache
from src.data.common import (
    friendly,
    SOURCE_EGIB,
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
EGIB_WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza"
ULDK_RESULT_FIELDS = "id,geom_wkt,voivodeship,county,commune,region"
ULDK_TIMEOUT_S = 5.0
EGIB_WFS_TIMEOUT_S = 8.0
ULDK_COOLDOWN_S = 300.0
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
_ULDK_FAIL_UNTIL = 0.0

# Indeks nazw jednostek TERYT (woj., powiat, gmina) do uzupełniania atrybutów z EGiB WFS
_TERYT_LOCK = threading.Lock()
_TERYT_MAPS: Optional[Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]] = None


def _teryt_lookup(parcel_id: str) -> Tuple[str, str, str]:
    """Kod TERYT działki (WWPPGG_R...) → (województwo, powiat bez prefiksu 'powiat ', gmina) z teryt_units.json."""
    global _TERYT_MAPS
    if _TERYT_MAPS is None:
        with _TERYT_LOCK:
            if _TERYT_MAPS is None:
                woj_m: Dict[str, str] = {}
                pow_m: Dict[str, str] = {}
                gm_m: Dict[str, str] = {}
                path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teryt_units.json")
                try:
                    with open(path, encoding="utf-8") as f:
                        doc = json.load(f)
                    for w in doc.get("woj") or []:
                        woj_m[str(w.get("code") or "")] = str(w.get("name") or "")
                    for pw in doc.get("powiat") or []:
                        name = str(pw.get("name") or "")
                        if name.lower().startswith("powiat "):
                            name = name[len("powiat "):].strip()
                        pow_m[str(pw.get("code") or "")] = name
                    for g in doc.get("gmina") or []:
                        code = str(g.get("code") or "")
                        name = str(g.get("name") or "")
                        gm_m[code] = name
                        if len(code) >= 6:
                            gm_m.setdefault(code[:6], name)
                except (OSError, ValueError):
                    pass
                _TERYT_MAPS = (woj_m, pow_m, gm_m)
    woj_m, pow_m, gm_m = _TERYT_MAPS
    head = (parcel_id or "").split(".", 1)[0]
    voiv = woj_m.get(head[:2], "")
    county = pow_m.get(head[:4], "")
    commune = gm_m.get(head, "") or gm_m.get(head[:6], "")
    return voiv, county, commune


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
    source: str = "uldk"       # "uldk" | "egib_wfs"

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
            "source": self.source,
        }

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "ParcelData":
        return _build_parcel(
            d["parcel_id"], d["wkt_2180"], d.get("voivodeship", ""), d.get("county", ""),
            d.get("commune", ""), d.get("region", ""), fetched_at=d.get("fetched_at"),
            source=d.get("source") or "uldk",
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
                  fetched_at: Optional[str] = None, source: str = "uldk") -> ParcelData:
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
        source=source,
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
    return _build_parcel(parcel_id, raw_geom, voiv, county, commune, region, fetched_at=iso_utc(), source="uldk")


def _uldk_in_cooldown() -> bool:
    with _INDEX_LOCK:
        return time.monotonic() < _ULDK_FAIL_UNTIL


def _mark_uldk_failed() -> None:
    global _ULDK_FAIL_UNTIL
    with _INDEX_LOCK:
        _ULDK_FAIL_UNTIL = time.monotonic() + ULDK_COOLDOWN_S


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
        _mark_uldk_failed()
        raise SourceError(str(exc)) from exc
    record_ok(SOURCE_ULDK)
    return parcel


def _egib_wfs_query(params: Dict[str, str], timeout: float = EGIB_WFS_TIMEOUT_S) -> List[Dict[str, Any]]:
    """Odpytuje zbiorczą usługę WFS EGiB GUGiK (ms:dzialki, GML 3.2, EPSG:2180) i parsuje rekordy."""
    from src.data.bdot10k_fast import parse_egib_page

    query = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": "ms:dzialki",
        **params,
    }
    try:
        resp = http_request("GET", EGIB_WFS_URL, params=query, timeout=timeout, retries=1)
        resp.encoding = resp.encoding or "utf-8"
        text = resp.text or ""
        if "ExceptionReport" in text[:600]:
            raise SourceError("EGiB WFS zwrócił wyjątek")
        rows, _ = parse_egib_page(text)
    except (SourceError, ValueError, shapely.errors.ShapelyError) as exc:
        record_error(SOURCE_EGIB, f"Błąd EGiB WFS: {friendly(exc)}")
        log.warning("EGiB WFS: %s", exc)
        raise SourceError(str(exc)) from exc
    record_ok(SOURCE_EGIB)
    return rows


def _parcel_from_egib_row(row: Dict[str, Any]) -> ParcelData:
    geom_2180 = shapely.from_wkb(row["wkb"])
    pid = str(row["id"]).strip()
    voiv, county, teryt_commune = _teryt_lookup(pid)
    commune = str(row.get("gmina") or "").strip() or teryt_commune
    region = str(row.get("obreb") or "").strip()
    return _build_parcel(
        pid, geom_2180.wkt, voiv, county, commune, region, fetched_at=iso_utc(), source="egib_wfs",
    )


def _egib_fetch_by_xy(lon: float, lat: float, timeout: float = EGIB_WFS_TIMEOUT_S) -> Optional[ParcelData]:
    """Działka ewidencyjna zawierająca punkt WGS84 ze zbiorczej usługi WFS EGiB GUGiK."""
    x, y = transformer_to_2180.transform(lon, lat)
    bbox = f"{y - 1.0:.2f},{x - 1.0:.2f},{y + 1.0:.2f},{x + 1.0:.2f},urn:ogc:def:crs:EPSG::2180"
    try:
        rows = _egib_wfs_query({"COUNT": "10", "BBOX": bbox}, timeout=timeout)
    except SourceError:
        return None
    if not rows:
        return None
    pt = Point(x, y)
    nearest: Optional[Tuple[float, Dict[str, Any]]] = None
    for row in rows:
        try:
            g = shapely.from_wkb(row["wkb"])
        except (KeyError, ValueError, shapely.errors.ShapelyError):
            continue
        if g.covers(pt):
            return _parcel_from_egib_row(row)
        d = float(g.distance(pt))
        if d <= 0.5 and (nearest is None or d < nearest[0]):
            nearest = (d, row)
    return _parcel_from_egib_row(nearest[1]) if nearest is not None else None


def _egib_fetch_by_id(parcel_id: str, timeout: float = EGIB_WFS_TIMEOUT_S) -> Optional[ParcelData]:
    """Działka po identyfikatorze TERYT ze zbiorczej usługi WFS EGiB GUGiK."""
    esc = xml_escape(parcel_id)
    flt = (
        '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0">'
        "<fes:PropertyIsEqualTo>"
        "<fes:ValueReference>ms:ID_DZIALKI</fes:ValueReference>"
        f"<fes:Literal>{esc}</fes:Literal>"
        "</fes:PropertyIsEqualTo>"
        "</fes:Filter>"
    )
    try:
        rows = _egib_wfs_query({"COUNT": "5", "FILTER": flt}, timeout=timeout)
    except SourceError:
        return None
    if not rows:
        return None
    for row in rows:
        if str(row.get("id") or "").strip() == parcel_id:
            return _parcel_from_egib_row(row)
    return _parcel_from_egib_row(rows[0])


def probe_uldk() -> str:
    """Lekki test ULDK: sprawdza czy serwis zwraca poprawny identyfikator działki (nie tylko HTTP 200)."""
    resp = http_request(
        "GET", ULDK_URL, params={"request": "GetParcelByXY", "xy": "17.2012,52.2020,4326", "result": "id"},
        timeout=ULDK_TIMEOUT_S, retries=0,
    )
    lines = [ln.strip() for ln in (resp.text or "").strip().splitlines() if ln.strip()]
    if len(lines) < 2 or lines[0] != "0" or not PARCEL_ID_RE.match(lines[1].split("|")[0].strip()):
        _mark_uldk_failed()
        raise SourceError("ULDK zwrócił niepoprawną odpowiedź (używany zapasowy GUGiK EGiB WFS)")
    return "OK"


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
    """Działka ewidencyjna zawierająca punkt WGS84. None = brak działki lub brak odpowiedzi usług GUGiK.

    Cache: (1) działka z pamięci, której wielokąt zawiera punkt; (2) zapamiętane trafienie dokładnie tego
    punktu (6 miejsc po przecinku); (3) zapytanie do ULDK, a w razie błędu/awarii ULDK – fallback do
    zbiorczej usługi WFS EGiB GUGiK. Bez przybliżeń "w promieniu" ani działek zastępczych.
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
    p: Optional[ParcelData] = None
    uldk_failed = _uldk_in_cooldown()
    if not uldk_failed:
        try:
            p = _uldk_call({"request": "GetParcelByXY", "xy": f"{lon:.6f},{lat:.6f},4326"})
        except SourceError:
            uldk_failed = True
    if p is None and uldk_failed:
        p = _egib_fetch_by_xy(lon, lat, timeout=max(timeout, EGIB_WFS_TIMEOUT_S))
    if p is None:
        return None
    _remember(p)
    _pt_cache.set(pkey, p.parcel_id)
    return p


def fetch_parcel_by_id(parcel_id: str) -> Optional[ParcelData]:
    """Działka po identyfikatorze TERYT. None = niepoprawny identyfikator, brak działki lub brak odpowiedzi GUGiK."""
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
    p: Optional[ParcelData] = None
    uldk_failed = _uldk_in_cooldown()
    if not uldk_failed:
        try:
            p = _uldk_call({"request": "GetParcelById", "id": clean_id})
        except SourceError:
            uldk_failed = True
    if p is None and uldk_failed:
        p = _egib_fetch_by_id(clean_id)
    if p is None:
        return None
    _remember(p)
    if p.parcel_id != clean_id:
        _id_cache.set(clean_id, p.to_json())
    return p


def parcel_provenance(p: Optional[ParcelData], note_if_missing: str = "") -> Dict[str, Any]:
    if p is None:
        return provenance(
            "GUGiK ULDK / EGiB WFS", ULDK_URL, "brak",
            note=note_if_missing or "Nie udało się pobrać granic działki z usług GUGiK (ULDK / EGiB WFS) – działka nie jest wyświetlana ani zmyślana.",
        )
    if p.source == "egib_wfs":
        return provenance(
            "GUGiK EGiB WFS – Ewidencja Gruntów i Budynków", EGIB_WFS_URL, "pomiar",
            fetched_at=p.fetched_at,
            note="Granice i identyfikator działki ze zbiorczej usługi WFS EGiB GUGiK; powierzchnia geometryczna policzona w EPSG:2180 (może różnić się od powierzchni ewidencyjnej).",
        )
    return provenance(
        "GUGiK ULDK – Ewidencja Gruntów i Budynków", ULDK_URL, "pomiar",
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
    global _ULDK_FAIL_UNTIL
    with _INDEX_LOCK:
        _INDEX.clear()
        _ULDK_FAIL_UNTIL = 0.0


__all__ = [
    "ParcelData", "FARM_PRESETS", "fetch_parcel_by_xy", "fetch_parcel_by_id", "merge_parcels", "parse_uldk_response",
    "parcel_provenance", "parcel_mask", "PARCEL_ID_RE", "probe_uldk", "EGIB_WFS_URL",
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

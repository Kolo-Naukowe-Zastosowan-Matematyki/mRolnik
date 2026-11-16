"""Hydrografia (cieki, rowy, kanały) w otoczeniu działki z usług rządowych OGC (WFS/WMS).

Źródła (konfigurowalne przez zmienne środowiskowe, bo adresy usług zmieniają się między wersjami Geoportalu):
- MROLNIK_HYDRO_WFS_URLS      – lista (po przecinku) adresów WFS z obiektami liniowymi sieci wodnej,
                                domyślnie usługa PGW Wody Polskie / KZGW (MPHP) i GUGiK BDOT10k,
- MROLNIK_HYDRO_WFS_TYPENAMES – opcjonalnie nazwy warstw WFS (bez tego: wykrywanie z GetCapabilities
                                po słowach kluczowych: SWRS = rów melioracyjny, SWKN = kanał, SWRM = rzeka/strumień, ciek, rów),
- MROLNIK_HYDRO_WMS_URL / MROLNIK_HYDRO_WMS_LAYERS – usługa WMS do wyświetlenia jako nakładka w przeglądarce
                                (domyślnie kompozycja BDOT10k GUGiK na mapy.geoportal.gov.pl).
Obiekty WFS pobieramy w EPSG:2180 w prostokącie ±1,5 km wokół działki, parsujemy GeoJSON albo GML
(bez GDAL), kolejność osi wykrywamy względem położenia działki, przeliczamy do WGS84. Cache 30 dni.
Gdy usługa nie odpowiada – pusta lista + proweniencja "brak" (nigdy rowy "z ręki").
"""
from __future__ import annotations

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shapely.geometry import LineString

from src.core.cache import TTLCache
from src.data.cadastre import transformer_to_2180, transformer_to_wgs84
from src.data.common import (
    friendly,
    SOURCE_HYDRO_WFS,
    SourceError,
    http_request,
    iso_utc,
    missing,
    provenance,
    record_error,
    record_ok,
)

log = logging.getLogger(__name__)


# Brak domyślnej publicznej usługi WFS z obiektami rowów (sprawdzone 2026-11-15):
# - wody.isok.gov.pl/.../KZGW/MPHP10_Cieki/MapServer/WFSServer → HTTP 401 (wymaga logowania),
# - mapy.geoportal.gov.pl/.../PZGIK/BDOT/WFS/PobieranieBDOT10k → jedyna warstwa "BDOT10k_powiaty"
#   (skorowidz paczek do pobrania, bez obiektów sieci wodnej).
# Domyślnie hydrografia jest więc pokazywana jako nakładka WMS BDOT10k; WFS można wskazać w MROLNIK_HYDRO_WFS_URLS.
DEFAULT_WFS_URLS: List[str] = []
DEFAULT_WMS_URL = "https://mapy.geoportal.gov.pl/wss/service/pub/guest/kompozycja_BDOT10k_WMS/MapServer/WMSServer"
# Warstwy hydrograficzne kompozycji BDOT10k sprawdzone w GetCapabilities 15.11.2026 (woda powierzchniowa, rzeki/strumienie,
# kanały – też okresowe, rowy melioracyjne, budowle hydrotechniczne, przepusty, nazwy). Ustalone na sztywno, bo filtr słów
# kluczowych łapał też np. „Teren piaszczysty lub żwi-ROW-y” (TPi1/TPi2). Usługa NIE obsługuje EPSG:3857 – tylko 4326/2180/217x.
DEFAULT_WMS_LAYERS = ["WPow", "rzOk", "rz", "kanOkr", "kan", "row", "BHydL", "Prze", "rzEt", "kanEt", "RowEt"]

KEYWORDS = ("swrs", "swkn", "swrm", "ciek", "cieki", "row", "rów", "rowy", "kanal", "kanał", "watercourse", "rzek", "hydro")
NAME_TAGS = ("nazwa", "name", "nazwa_rzeki", "nazwarzeki", "nazwa_cieku", "geographicalname", "text")
KIND_TAGS = ("x_kod", "kod", "rodzaj", "typ", "kategoria")
BBOX_HALF_M = 1500.0
MAX_FEATURES = 300

WMS_ONLY_STATUS = ("Tylko podgląd mapy (WMS BDOT10k, GUGiK) – brak publicznej usługi WFS z obiektami rowów; "
                   "wektory można włączyć zmienną MROLNIK_HYDRO_WFS_URLS")
WMS_ONLY_NOTE = ("Rowy i cieki widoczne na mapie jako warstwa WMS BDOT10k (GUGiK). Publiczne usługi WFS z obiektami sieci wodnej "
                 "wymagają logowania (PGW Wody Polskie) lub udostępniają tylko paczki do pobrania (BDOT10k), więc mRolnik "
                 "nie ma ich jako obiektów wektorowych do analizy.")

_caps_cache = TTLCache("hydro_caps", max_entries=50, default_ttl=7 * 24 * 3600)
_wfs_cache = TTLCache("hydro_wfs", max_entries=5000, default_ttl=30 * 24 * 3600)


def _env_list(name: str, default: Sequence[str]) -> List[str]:
    raw = os.environ.get(name, "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else list(default)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _matches(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in KEYWORDS)


def kind_label(code: str) -> str:
    c = (code or "").upper()
    if "SWRS" in c or "RÓW" in c or "ROW" == c[:3]:
        return "rów melioracyjny"
    if "SWKN" in c or "KANA" in c:
        return "kanał"
    if "SWRM" in c or "RZEK" in c or "STRUM" in c:
        return "rzeka / strumień"
    return "ciek"


# --------------------------------------------------------------------------------------
# GetCapabilities → nazwy warstw
# --------------------------------------------------------------------------------------
def parse_capabilities(xml_text: str, element: str) -> List[str]:
    """Nazwy warstw (FeatureType dla WFS, Layer dla WMS) pasujące do słów kluczowych hydrografii."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError("niepoprawny XML GetCapabilities") from exc
    names = []
    for el in root.iter():
        if _local(el.tag) != element.lower():
            continue
        name_el = next((c for c in el if _local(c.tag) == "name"), None)
        title_el = next((c for c in el if _local(c.tag) == "title"), None)
        name = (name_el.text or "").strip() if name_el is not None else ""
        title = (title_el.text or "").strip() if title_el is not None else ""
        if name and (_matches(name) or _matches(title)):
            names.append(name)
    return names


def _capabilities(url: str, service: str) -> List[str]:
    key = f"{service}|{url}"
    cached = _caps_cache.get(key)
    if cached is not None:
        if isinstance(cached, dict) and cached.get("error"):
            raise SourceError(cached["error"])
        return cached
    try:
        version = "2.0.0" if service == "WFS" else "1.3.0"
        resp = http_request("GET", url, params={"service": service, "request": "GetCapabilities", "version": version},
                            timeout=8.0, retries=0)
        names = parse_capabilities(resp.text, "FeatureType" if service == "WFS" else "Layer")
    except SourceError as exc:
        _caps_cache.set(key, {"error": str(exc)}, 3600)  # nie ponawiaj co żądanie
        raise
    _caps_cache.set(key, names)
    return names


# --------------------------------------------------------------------------------------
# Parsowanie obiektów (GeoJSON / GML) z wykrywaniem kolejności osi
# --------------------------------------------------------------------------------------
def _to_wgs84_line(coords: List[Tuple[float, float]], ref_xy: Tuple[float, float]) -> Optional[List[List[float]]]:
    """Ciąg współrzędnych w EPSG:2180 (dowolna kolejność osi) lub w stopniach → [[lon, lat], ...]."""
    if len(coords) < 2:
        return None
    ax, ay = sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords)
    if abs(ax) <= 180 and abs(ay) <= 180:  # serwer zwrócił stopnie
        ref_lon, ref_lat = transformer_to_wgs84.transform(*ref_xy)
        swap = abs(ax - ref_lat) + abs(ay - ref_lon) < abs(ax - ref_lon) + abs(ay - ref_lat)
        pts = [(b, a) if swap else (a, b) for a, b in coords]
        return [[round(x, 6), round(y, 6)] for x, y in pts]
    rx, ry = ref_xy
    swap = abs(ax - ry) + abs(ay - rx) < abs(ax - rx) + abs(ay - ry)
    pts = [(b, a) if swap else (a, b) for a, b in coords]
    line = LineString(pts).simplify(2.0)
    out = [list(transformer_to_wgs84.transform(x, y)) for x, y in line.coords]
    return [[round(x, 6), round(y, 6)] for x, y in out]


def _pairs(nums: List[float]) -> List[Tuple[float, float]]:
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def parse_gml_features(xml_text: str, ref_xy: Tuple[float, float]) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError("niepoprawny GML") from exc
    if _local(root.tag) in ("exceptionreport", "serviceexceptionreport"):
        raise SourceError("WFS zwrócił wyjątek")
    feats = []
    members = [el for el in root.iter() if _local(el.tag) in ("featuremember", "member")]
    for m in members:
        name, code = "", ""
        lines: List[List[Tuple[float, float]]] = []
        for el in m.iter():
            tag = _local(el.tag)
            txt = (el.text or "").strip()
            if tag in ("poslist", "coordinates") and txt:
                if tag == "coordinates":
                    nums = [float(v) for pair in txt.split() for v in pair.split(",")[:2]]
                else:
                    nums = [float(v) for v in txt.split()]
                dim = int(el.attrib.get("srsDimension", "2") or 2)
                if dim == 3:
                    nums = [v for i, v in enumerate(nums) if i % 3 != 2]
                lines.append(_pairs(nums))
            elif tag in NAME_TAGS and txt and not name:
                name = txt
            elif tag in KIND_TAGS and txt and not code:
                code = txt
        for ln in lines:
            coords = _to_wgs84_line(ln, ref_xy)
            if coords:
                feats.append(_feature(coords, name, code))
    return feats


def parse_geojson_features(data: Dict[str, Any], ref_xy: Tuple[float, float]) -> List[Dict[str, Any]]:
    feats = []
    for f in data.get("features") or []:
        geom = (f or {}).get("geometry") or {}
        props = (f or {}).get("properties") or {}
        lower = {str(k).lower(): v for k, v in props.items()}
        name = next((str(lower[k]) for k in NAME_TAGS if lower.get(k)), "")
        code = next((str(lower[k]) for k in KIND_TAGS if lower.get(k)), "")
        gtype, coords = geom.get("type"), geom.get("coordinates") or []
        parts = [coords] if gtype == "LineString" else (coords if gtype == "MultiLineString" else [])
        for part in parts:
            try:
                line = _to_wgs84_line([(float(c[0]), float(c[1])) for c in part], ref_xy)
            except (TypeError, ValueError, IndexError):
                continue
            if line:
                feats.append(_feature(line, name, code))
    return feats


def _feature(coords: List[List[float]], name: str, code: str) -> Dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"layer": "hydrografia", "name": name[:120] or None, "kind": kind_label(code or name),
                       "code": code[:20] or None, "demo": False},
    }


# --------------------------------------------------------------------------------------
# Pobieranie
# --------------------------------------------------------------------------------------
def _get_features(url: str, typename: str, bbox: Tuple[float, float, float, float], ref_xy: Tuple[float, float]) -> List[Dict[str, Any]]:
    minx, miny, maxx, maxy = bbox
    attempts = [
        f"{minx:.0f},{miny:.0f},{maxx:.0f},{maxy:.0f},EPSG:2180",                       # x=wschód, y=północ
        f"{miny:.0f},{minx:.0f},{maxy:.0f},{maxx:.0f},urn:ogc:def:crs:EPSG::2180",     # kolejność osi wg EPSG
    ]
    last_err: Optional[Exception] = None
    for bbox_param in attempts:
        params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": typename,
                  "srsName": "EPSG:2180", "bbox": bbox_param, "count": str(MAX_FEATURES)}
        for fmt in ("application/json", None):
            p = dict(params)
            if fmt:
                p["outputFormat"] = fmt
            try:
                resp = http_request("GET", url, params=p, timeout=10.0, retries=0)
            except SourceError as exc:
                last_err = exc
                continue
            body = (resp.text or "").lstrip()
            try:
                feats = parse_geojson_features(json.loads(body), ref_xy) if body.startswith("{") else parse_gml_features(body, ref_xy)
            except (SourceError, ValueError) as exc:
                last_err = exc
                continue
            if feats:
                return feats[:MAX_FEATURES]
            break  # poprawna, ale pusta odpowiedź – spróbuj innej kolejności osi
    if last_err:
        raise SourceError(str(last_err))
    return []


def hydrography_near(lat: float, lon: float) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Linie sieci wodnej (GeoJSON, WGS84) w promieniu ~1,5 km + proweniencja. Nie rzuca wyjątków."""
    x, y = transformer_to_2180.transform(lon, lat)
    gx, gy = round(x / 500) * 500, round(y / 500) * 500  # siatka 500 m → wspólny cache dla sąsiednich działek
    bbox = (gx - BBOX_HALF_M, gy - BBOX_HALF_M, gx + BBOX_HALF_M, gy + BBOX_HALF_M)
    key = f"{gx:.0f},{gy:.0f}"
    cached = _wfs_cache.get(key)
    if cached is not None:
        return cached["features"], cached["provenance"]
    configured_types = _env_list("MROLNIK_HYDRO_WFS_TYPENAMES", [])
    errors = []
    wfs_urls = _env_list("MROLNIK_HYDRO_WFS_URLS", DEFAULT_WFS_URLS)
    if not wfs_urls:
        record_ok(SOURCE_HYDRO_WFS, WMS_ONLY_STATUS)
        return [], missing(SOURCE_HYDRO_WFS, DEFAULT_WMS_URL, WMS_ONLY_NOTE)
    for url in wfs_urls:
        try:
            typenames = configured_types or _capabilities(url, "WFS")
            if not typenames:
                raise SourceError("brak warstw hydrograficznych w GetCapabilities")
            feats: List[Dict[str, Any]] = []
            for tn in typenames[:4]:
                feats += _get_features(url, tn, bbox, (x, y))
            host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
            prov = provenance(
                f"Hydrografia – usługa WFS ({host})", url, "pomiar", fetched_at=iso_utc(),
                note=(f"Cieki, rowy i kanały w promieniu ok. 1,5 km z rządowej usługi WFS (warstwy: {', '.join(typenames[:4])}). "
                      "Aktualność zgodna z bazą źródłową (BDOT10k / MPHP); stan techniczny rowów i zastawek nie jest w niej opisany."),
            )
            record_ok(SOURCE_HYDRO_WFS, f"{len(feats)} obiektów ({host})")
            _wfs_cache.set(key, {"features": feats, "provenance": prov})
            return feats, prov
        except SourceError as exc:
            errors.append(f"{url}: {friendly(exc)}")
            log.info("Hydrografia WFS %s: %s", url, exc)
    record_error(SOURCE_HYDRO_WFS, "; ".join(errors)[:300])
    return [], missing(SOURCE_HYDRO_WFS, DEFAULT_WMS_URL,
                       "Nie udało się pobrać obiektów sieci wodnej z usług WFS (PGW Wody Polskie / GUGiK). "
                       "Mapa może pokazać hydrografię jako warstwę WMS (jeśli dostępna).")


def probe() -> Tuple[bool, str]:
    """Test dla /api/sources/status?check=true: GetCapabilities skonfigurowanych WFS, a bez nich – usługi WMS."""
    wfs_urls = _env_list("MROLNIK_HYDRO_WFS_URLS", DEFAULT_WFS_URLS)
    errors = []
    for url in wfs_urls:
        try:
            resp = http_request("GET", url, params={"service": "WFS", "request": "GetCapabilities", "version": "2.0.0"},
                                timeout=8.0, retries=0)
            names = parse_capabilities(resp.text, "FeatureType")
            if not names:
                raise SourceError("usługa WFS nie ma warstw sieci wodnej (cieki/rowy/kanały)")
        except SourceError as exc:
            errors.append(f"{url}: {exc}")
            continue
        msg = f"WFS: {len(names)} warstw sieci wodnej ({', '.join(names[:4])})"
        record_ok(SOURCE_HYDRO_WFS, msg)
        return True, msg
    url = os.environ.get("MROLNIK_HYDRO_WMS_URL", "").strip() or DEFAULT_WMS_URL
    try:
        resp = http_request("GET", url, params={"service": "WMS", "request": "GetCapabilities", "version": "1.3.0"},
                            timeout=8.0, retries=0)
        layers = parse_capabilities(resp.text, "Layer")
        if not layers:
            raise SourceError("WMS bez warstw sieci wodnej")
    except SourceError as exc:
        errors.append(f"WMS {url}: {exc}")
        msg = "Hydrografia niedostępna: " + "; ".join(errors)
        record_error(SOURCE_HYDRO_WFS, msg[:300])
        return False, msg[:300]
    msg = WMS_ONLY_STATUS if not wfs_urls else ("WFS niedostępne (" + "; ".join(errors) + ") – działa tylko podgląd WMS BDOT10k")
    record_ok(SOURCE_HYDRO_WFS, msg[:300])
    return True, msg[:300]


def wms_layers() -> List[Dict[str, Any]]:
    """Opis warstwy WMS z hydrografią do wyświetlenia w przeglądarce (pusta lista, gdy nazw warstw nie da się ustalić)."""
    url = os.environ.get("MROLNIK_HYDRO_WMS_URL", "").strip() or DEFAULT_WMS_URL
    layers = _env_list("MROLNIK_HYDRO_WMS_LAYERS", DEFAULT_WMS_LAYERS if url == DEFAULT_WMS_URL else [])
    verified = bool(layers)
    if not layers:
        try:
            layers = _capabilities(url, "WMS")[:6]
            verified = bool(layers)
        except SourceError as exc:
            log.info("WMS hydrografii – GetCapabilities: %s", exc)
            return []
    if not layers:
        return []
    host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
    return [{
        "title": "Sieć wodna (BDOT10k, GUGiK)",
        "url": url, "host": host, "layers": layers, "version": "1.3.0", "format": "image/png", "transparent": True,
        "crs": "EPSG:4326" if url == DEFAULT_WMS_URL else "EPSG:3857", "attribution": "Źródło: GUGiK – Baza Danych Obiektów Topograficznych BDOT10k",
        "verified": verified,
    }]


__all__ = ["hydrography_near", "wms_layers", "probe", "parse_gml_features", "parse_geojson_features", "parse_capabilities"]

"""Miasta z Państwowego Rejestru Nazw Geograficznych (GUGiK PRNG, WFS 2.0).

Usługa: https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRNG/WFS/GeographicalNames
Warstwa ms:M1_UrzedoweNazwyMiejscowosci, filtr FES po stronie serwera: RODZAJOBIEKTU = 'miasto' i BBOX.
Odpowiedź tylko w GML 3.2 (JSON zwraca 400). Osie w EPSG:4326 (urn): szerokość, długość.
PRNG nie ma liczby mieszkańców – tę bierzemy z BDOT10k (ADMS_A/ADMS_P), łącząc po identyfikatorze PRNG.
"""
from __future__ import annotations

import logging
import math
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Tuple

from src.core.cache import TTLCache
from src.data.common import SOURCE_PRNG, SourceError, friendly, http_request, provenance, record_error, record_ok

log = logging.getLogger(__name__)

WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRNG/WFS/GeographicalNames"
TYPE_NAME = "ms:M1_UrzedoweNazwyMiejscowosci"
GRID_DEG = 0.1          # bbox zaokrąglany na zewnątrz do siatki 0,1° (lepsze trafienia w cache)
TTL_S = 30 * 24 * 3600
MS = "{http://mapserver.gis.umn.edu/mapserver}"
GML = "{http://www.opengis.net/gml/3.2}"

_cache = TTLCache("prng_towns", max_entries=5000, default_ttl=TTL_S)

_FILTER = ('<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" xmlns:gml="http://www.opengis.net/gml/3.2"><fes:And>'
           '<fes:PropertyIsEqualTo><fes:ValueReference>RODZAJOBIEKTU</fes:ValueReference><fes:Literal>miasto</fes:Literal>'
           '</fes:PropertyIsEqualTo><fes:BBOX><fes:ValueReference>msGeometry</fes:ValueReference>'
           '<gml:Envelope srsName="urn:ogc:def:crs:EPSG::4326"><gml:lowerCorner>{s} {w}</gml:lowerCorner>'
           '<gml:upperCorner>{n} {e}</gml:upperCorner></gml:Envelope></fes:BBOX></fes:And></fes:Filter>')


def parse_towns(xml_text: str) -> List[Dict[str, Any]]:
    """GML PRNG → [{name, lat, lon, prng_id, simc, gmina, powiat, woj, kind}]. Rzuca ValueError przy wyjątku usługi."""
    root = ET.fromstring(xml_text)
    if root.tag.endswith("ExceptionReport"):
        txt = " ".join(t.text or "" for t in root.iter() if t.tag.endswith("ExceptionText"))
        raise ValueError(f"PRNG: {txt[:200] or 'błąd usługi'}")
    out = []
    for feat in root.iter(f"{MS}M1_UrzedoweNazwyMiejscowosci"):
        pos = feat.find(f".//{GML}pos")
        if pos is None or not pos.text:
            continue
        try:
            lat, lon = (float(v) for v in pos.text.split()[:2])
        except ValueError:
            continue

        def g(tag: str) -> str:
            return (feat.findtext(f"{MS}{tag}") or "").strip()

        out.append({"name": g("NAZWAGLOWNA"), "lat": lat, "lon": lon, "prng_id": g("IDENTYFIKATORPRNG"), "simc": g("IDENTYFIKATORSIMC"),
                    "gmina": g("GMINA"), "powiat": g("POWIAT"), "woj": g("WOJEWODZTWO"), "kind": g("RODZAJOBIEKTU")})
    return [t for t in out if t["kind"] == "miasto" and t["name"]]


def _grid(bbox: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    w, s, e, n = bbox
    f = lambda v, up: round((math.ceil if up else math.floor)(v / GRID_DEG) * GRID_DEG, 4)  # noqa: E731
    return f(w, False), f(s, False), f(e, True), f(n, True)


def towns_in_bbox(bbox: Tuple[float, float, float, float]) -> List[Dict[str, Any]]:
    """Miasta (RODZAJOBIEKTU='miasto') w bbox WGS84 (w, s, e, n). Rzuca SourceError."""
    w, s, e, n = _grid(bbox)
    key = f"{w},{s},{e},{n}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": TYPE_NAME,
              "srsName": "urn:ogc:def:crs:EPSG::4326", "count": "2000",
              "filter": _FILTER.format(s=s, w=w, n=n, e=e)}
    try:
        resp = http_request("GET", WFS_URL, params=params, timeout=30.0, retries=1)
        resp.encoding = resp.encoding or "utf-8"
        towns = parse_towns(resp.text)
    except (SourceError, ValueError, ET.ParseError) as exc:
        record_error(SOURCE_PRNG, f"PRNG: {friendly(exc)}")
        raise SourceError(f"PRNG: {friendly(exc)}") from exc
    record_ok(SOURCE_PRNG, f"OK ({len(towns)} miast w obszarze)")
    _cache.set(key, towns)
    return towns


def prng_provenance() -> Dict[str, Any]:
    return provenance("GUGiK PRNG – Państwowy Rejestr Nazw Geograficznych", WFS_URL, "pomiar",
                      note="Urzędowe nazwy miejscowości (WFS, warstwa M1), filtr: rodzaj obiektu = miasto.")


def probe() -> str:
    resp = http_request("GET", WFS_URL, params={"service": "WFS", "request": "GetCapabilities"}, timeout=10.0, retries=0)
    if "M1_UrzedoweNazwyMiejscowosci" not in resp.text:
        raise SourceError("PRNG: brak warstwy nazw miejscowości w GetCapabilities")
    return "OK"

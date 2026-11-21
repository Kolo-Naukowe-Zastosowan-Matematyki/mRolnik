"""Działki ewidencyjne z obszaru: zbiorcza usługa WFS EGiB GUGiK (ms:dzialki) + jednostki administracyjne (GUS BDL, ULDK).

Usługa: https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza (WFS 2.0, GML 3.2, EPSG:2180).
Łączy powiatowe usługi WFS EGiB zgłoszone w EZiUDP – pokrycie zależy od starostw (gdy WFS powiatu nie odpowiada,
działki z tego obszaru oznaczamy „brak danych EGiB”, nie pusty wynik). BBOX w kolejności N, E (urn EPSG::2180),
maks. 1000 obiektów na stronę, dalej stronicowanie przez atrybut „next”; między stronami bywają duplikaty.

Obszar dzielimy na kafle 3 km i pobieramy tylko kafle przecinające zawężony obszar. Kafel: single-flight, cache
na dysku 30 dni (output/cache/egib/), limit 3 równoległych połączeń i ponowienia. Zapisujemy wyłącznie identyfikator,
numer, obręb, gminę, powierzchnię ewidencyjną i geometrię – bez klas użytków i grup rejestrowych.
Granice jednostek: ULDK (GetCommuneById / GetCountyById / GetVoivodeshipById). Listy jednostek: plik src/data/teryt_units.json
(GUGiK PRG, scripts/build_teryt_units.py); GUS BDL na żywo tylko, gdy pliku brak.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import zlib
from typing import Any, Dict, List, Optional, Tuple

import shapely
from shapely import wkt as shapely_wkt
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from src.core.cache import TTLCache
from src.core.config import settings
from src.data import bdot10k
from src.data.common import SOURCE_EGIB, SourceError, friendly, http_request, iso_utc, provenance, record_error, record_ok

log = logging.getLogger(__name__)

WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza"
ULDK_URL = "https://uldk.gugik.gov.pl/"
BDL_UNITS = "https://bdl.stat.gov.pl/api/v1/units"
TILE_M = 3000.0   # kafel 3 km: ok. 4× mniej zapytań niż 1,5 km (czas jednego zapytania zależy głównie od opóźnienia usługi)
TTL_S = 30 * 24 * 3600
PAGE = 1000
CONCURRENCY = 4
RETRY_WAITS_S = (2.0, 6.0, 15.0)
CACHE_DIR = settings.cache_dir / "egib" / f"t{int(TILE_M)}"   # osobny katalog na rozmiar kafla

_SEM = threading.BoundedSemaphore(CONCURRENCY)
_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_hits_cache = TTLCache("egib_hits", max_entries=50000, default_ttl=TTL_S)
_bound_cache = TTLCache("admin_bound", max_entries=5000, default_ttl=90 * 24 * 3600)
_units_cache = TTLCache("bdl_units", max_entries=2000, default_ttl=365 * 24 * 3600)


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _get(url: str, params: Optional[Dict[str, Any]], timeout: float) -> Any:
    last: Optional[Exception] = None
    for wait in (0.0,) + RETRY_WAITS_S:
        if wait:
            time.sleep(wait)
        with _SEM:
            try:
                resp = http_request("GET", url, params=params, timeout=timeout, retries=0)
            except SourceError as exc:
                last = exc
                continue
        if "ExceptionReport" in resp.text[:600]:
            last = SourceError("usługa WFS zwróciła wyjątek")
            continue
        return resp
    raise SourceError(f"EGiB WFS: brak odpowiedzi po {len(RETRY_WAITS_S) + 1} próbach") from last


# --------------------------------------------------------------------------------------
# Kafle
# --------------------------------------------------------------------------------------
def tile_box(i: int, j: int) -> BaseGeometry:
    return box(i * TILE_M, j * TILE_M, (i + 1) * TILE_M, (j + 1) * TILE_M)


def tiles_for_area(area: BaseGeometry) -> List[Tuple[int, int]]:
    """Kafle TILE_M (EPSG:2180) przecinające obszar."""
    if area.is_empty:
        return []
    minx, miny, maxx, maxy = area.bounds
    shapely.prepare(area)
    out = []
    for i in range(math.floor(minx / TILE_M), math.floor(maxx / TILE_M) + 1):
        for j in range(math.floor(miny / TILE_M), math.floor(maxy / TILE_M) + 1):
            if area.intersects(tile_box(i, j)):
                out.append((i, j))
    return out


def _bbox_param(i: int, j: int) -> str:
    minx, miny, maxx, maxy = tile_box(i, j).bounds
    return f"{miny:.0f},{minx:.0f},{maxy:.0f},{maxx:.0f},urn:ogc:def:crs:EPSG::2180"   # urn EPSG:2180: N, E


def hits(i: int, j: int) -> int:
    """Liczba działek w kaflu (resultType=hits, liczy też działki tylko dotykające kafla)."""
    key = f"{int(TILE_M)}:{i},{j}"
    c = _hits_cache.get(key)
    if c is not None:
        return int(c)
    resp = _get(WFS_URL, {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "ms:dzialki",
                          "RESULTTYPE": "hits", "BBOX": _bbox_param(i, j)}, 120.0)
    m = re.search(r'numberMatched="(\d+)"', resp.text)
    if not m:
        raise SourceError("EGiB WFS: brak liczby obiektów w odpowiedzi")
    n = int(m.group(1))
    _hits_cache.set(key, n)
    return n


def _tile_path(i: int, j: int) -> str:
    return str(CACHE_DIR / f"{i}_{j}.json.z")


def _read_tile(i: int, j: int) -> Optional[Dict[str, Any]]:
    p = _tile_path(i, j)
    try:
        with open(p, "rb") as f:
            doc = json.loads(zlib.decompress(f.read()).decode("utf-8"))
    except (OSError, ValueError, zlib.error):
        return None
    if time.time() - float(doc.get("stored_ts", 0)) > TTL_S:
        return None
    return doc


def _write_tile(i: int, j: int, doc: Dict[str, Any]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _tile_path(i, j)
    with open(p + ".tmp", "wb") as f:
        f.write(zlib.compress(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 1))
    os.replace(p + ".tmp", p)


def fetch_tile(i: int, j: int) -> Dict[str, Any]:
    """{rows: [{id, nr, obreb, gmina, pole_ha, wkb(hex)}], fetched_at, pages}. Rzuca SourceError (WFS powiatu nie odpowiada)."""
    doc = _read_tile(i, j)
    if doc is not None:
        return doc
    with _lock(f"{i},{j}"):
        doc = _read_tile(i, j)
        if doc is not None:
            return doc
        from src.data import bdot10k_fast
        url: Optional[str] = WFS_URL
        params: Optional[Dict[str, Any]] = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "ms:dzialki",
                                            "COUNT": str(PAGE), "BBOX": _bbox_param(i, j)}
        rows: Dict[str, Dict[str, Any]] = {}
        pages = 0
        try:
            while url and pages < 100:
                resp = _get(url, params, 180.0)
                resp.encoding = resp.encoding or "utf-8"
                page_rows, nxt = bdot10k.run_in_process(bdot10k_fast.parse_egib_page, resp.text)
                pages += 1
                for r in page_rows:
                    rows.setdefault(r["id"], dict(r, wkb=r["wkb"].hex()))   # deduplikacja po ID_DZIALKI
                url, params = (nxt, None) if nxt and len(page_rows) else (None, None)
        except SourceError as exc:
            record_error(SOURCE_EGIB, f"EGiB WFS: {friendly(exc)}")
            raise
        record_ok(SOURCE_EGIB)
        doc = {"rows": list(rows.values()), "fetched_at": iso_utc(), "stored_ts": time.time(), "pages": pages, "tile": [i, j]}
        _write_tile(i, j, doc)
        return doc


def egib_provenance(fetched_at: Optional[str] = None) -> Dict[str, Any]:
    return provenance("GUGiK – EGiB WFS (usługa zbiorcza)", WFS_URL, "pomiar", fetched_at=fetched_at,
                      note="Działki ewidencyjne (identyfikator, numer, obręb, gmina, powierzchnia ewidencyjna, granice) ze zbiorczej "
                           "usługi WFS EGiB GUGiK, złożonej z powiatowych usług WFS starostw – pokrycie i aktualność zależą od powiatu. "
                           "Pobierane w kaflach 3 km, cache 30 dni.")


# --------------------------------------------------------------------------------------
# Jednostki administracyjne
# --------------------------------------------------------------------------------------
_ID_RE = {"gmina": re.compile(r"^\d{6}_\d$"), "powiat": re.compile(r"^\d{4}$"), "woj": re.compile(r"^\d{2}$")}
_ULDK_REQ = {"gmina": "GetCommuneById", "powiat": "GetCountyById", "woj": "GetVoivodeshipById"}


def admin_boundary(level: str, code: str) -> BaseGeometry:
    """Granica jednostki w EPSG:2180 z ULDK (cache 90 dni). Rzuca SourceError."""
    if level not in _ULDK_REQ or not _ID_RE[level].match(code or ""):
        raise SourceError(f"niepoprawny kod jednostki: {code!r}")
    key = f"{level}:{code}"
    c = _bound_cache.get(key)
    if c:
        return shapely_wkt.loads(c)
    resp = http_request("GET", ULDK_URL, params={"request": _ULDK_REQ[level], "id": code, "result": "geom_wkt", "srid": "2180"},
                        timeout=60.0, retries=2)
    lines = [ln.strip() for ln in resp.text.strip().splitlines() if ln.strip()]
    if not lines or lines[0] != "0" or len(lines) < 2:
        raise SourceError(f"ULDK: brak granicy jednostki {code}")
    raw = lines[1].split("|")[-1]
    if raw.upper().startswith("SRID="):
        raw = raw.split(";", 1)[1]
    g = shapely_wkt.loads(raw)
    if not g.is_valid:
        g = shapely.make_valid(g)
    _bound_cache.set(key, g.wkt)
    return g


def _bdl_units(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    key = json.dumps(params, sort_keys=True)
    c = _units_cache.get(key)
    if c is not None:
        return c
    out: List[Dict[str, Any]] = []
    page = 0
    while page < 60:
        data = http_request("GET", BDL_UNITS, params=dict(params, format="json", lang="pl", **{"page-size": 100, "page": page}),
                            timeout=30.0, retries=2).json()
        out += data.get("results") or []
        if not (data.get("links") or {}).get("next"):
            break
        page += 1
    _units_cache.set(key, out)
    return out


def _bdl_powiat_id(teryt4: str, powiaty: List[Dict[str, Any]]) -> Optional[str]:
    return next((u["id"] for u in powiaty if u["id"][2:4] + u["id"][7:9] == teryt4), None)


GMINA_KIND = {"1": "gm. miejska", "2": "gm. wiejska", "3": "gm. miejsko-wiejska", "8": "dzielnica", "9": "delegatura"}


UNITS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teryt_units.json")
_units_file: Optional[Dict[str, Any]] = None


def _load_units_file() -> Optional[Dict[str, Any]]:
    """Stała lista jednostek z pliku (scripts/build_teryt_units.py, źródło GUGiK PRG); None, gdy pliku brak lub jest uszkodzony."""
    global _units_file
    if _units_file is None:
        try:
            with open(UNITS_FILE, encoding="utf-8") as f:
                doc = json.load(f)
            if doc.get("woj") and doc.get("powiat") and doc.get("gmina"):
                _units_file = doc
        except (OSError, ValueError):
            return None
    return _units_file


def units(woj: Optional[str] = None, powiat: Optional[str] = None) -> Dict[str, Any]:
    """Listy jednostek: województwa; powiaty województwa; gminy powiatu.

    Z pliku src/data/teryt_units.json (stała lista z PRG, bez zapytań przy otwarciu panelu); GUS BDL na żywo tylko,
    gdy pliku brak.
    """
    doc = _load_units_file()
    if doc is not None:
        out: Dict[str, Any] = {"woj": sorted(doc["woj"], key=lambda x: x["name"]),
                               "provenance": provenance(doc.get("source") or "GUGiK PRG", doc.get("source_url") or "", "pomiar",
                                                        fetched_at=doc.get("fetched_at"),
                                                        note=f"{doc.get('source')}, stan na {(doc.get('fetched_at') or '')[:10]}.")}
        if woj:
            out["powiat"] = sorted(({"code": p["code"], "name": p["name"]} for p in doc["powiat"] if p["woj"] == woj), key=lambda x: x["name"])
            if powiat:
                out["gmina"] = sorted(({"code": g["code"], "name": g["name"], "kind": g.get("kind", "")}
                                       for g in doc["gmina"] if g["powiat"] == powiat), key=lambda x: x["name"])
        return out
    return _units_bdl(woj, powiat)


def _units_bdl(woj: Optional[str] = None, powiat: Optional[str] = None) -> Dict[str, Any]:
    """Listy jednostek z GUS BDL: województwa; powiaty województwa; gminy powiatu (TERYT z identyfikatora BDL)."""
    out: Dict[str, Any] = {}
    out["woj"] = sorted(({"code": u["id"][2:4], "name": u["name"].lower()} for u in _bdl_units({"level": 2})), key=lambda x: x["name"])
    if woj:
        pw = _bdl_units({"level": 5})
        out["powiat"] = [{"code": u["id"][2:4] + u["id"][7:9], "name": u["name"]} for u in pw if u["id"][2:4] == woj]
        if powiat:
            pid = _bdl_powiat_id(powiat, pw)
            gm = _bdl_units({"level": 6, "parent-id": pid}) if pid else []
            # BDL trzyma też dawny rodzaj gminy (np. Zaniemyśl: wiejska → miejsko-wiejska od 2021) – jedna pozycja na TERYT,
            # pierwszeństwo: miejsko-wiejska, miejska, wiejska, dzielnica
            best: Dict[str, Dict[str, Any]] = {}
            rank = {"3": 0, "1": 1, "2": 2, "8": 3, "9": 4}
            for u in gm:
                k = u["id"][11]
                if k not in GMINA_KIND:
                    continue
                t6 = u["id"][2:4] + u["id"][7:9] + u["id"][9:11]
                cur = best.get(t6)
                if cur is None or rank[k] < rank[cur["code"][-1]]:
                    best[t6] = {"code": f"{t6}_{k}", "name": u["name"], "kind": GMINA_KIND[k]}
            out["gmina"] = sorted(best.values(), key=lambda x: x["name"])
    return out


def probe() -> str:
    resp = http_request("GET", WFS_URL, params={"SERVICE": "WFS", "REQUEST": "GetCapabilities"}, timeout=15.0, retries=0)
    if "ms:dzialki" not in resp.text:
        raise SourceError("EGiB WFS: brak warstwy działek w GetCapabilities")
    return "OK (zbiorcza usługa WFS EGiB)"

"""BDOT10k (GUGiK) – obiekty topograficzne per powiat do wyszukiwarki działek.

Źródło: paczki GML per powiat z opendata GUGiK
    https://opendata.geoportal.gov.pl/bdot10k/schemat2021/{WW}/{TERYT4}_GML.zip
Serwer obsługuje zapytania HTTP Range, więc NIE pobieramy całego ZIP-a (dla powiatu poznańskiego 123 MB):
czytamy katalog centralny ZIP-a (ok. 8 kB), a potem tylko potrzebne pliki klas (np. ADMS_A ≈ 0,25 MB,
SKJZ_L ≈ 2 MB skompresowane). Każdą klasę parsujemy strumieniowo (iterparse, czyszczenie elementów) i zapisujemy
kompaktowo w output/cache/bdot10k/{TERYT4}/{KLASA}.wkb (+ .json z atrybutami): geometrie EPSG:2180 uproszczone
do 1 m, tylko potrzebne atrybuty. TTL 180 dni. Wczytywanie leniwe, indeks przestrzenny STRtree.

Nigdy nie zgadujemy: brak klasy / błąd sieci = SourceError, a wyszukiwarka pokazuje „brak danych”.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from src.core.config import settings
from src.data.common import (
    SOURCE_BDOT10K,
    SourceError,
    friendly,
    http_request,
    iso_utc,
    provenance,
    record_error,
    record_ok,
)

log = logging.getLogger(__name__)

ZIP_URL = "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/{ww}/{teryt}_GML.zip"
SOURCE_PAGE = "https://www.geoportal.gov.pl/pl/dane/baza-danych-obiektow-topograficznych-bdot10k/"
FORMAT_VERSION = 2       # v2: blok WKB skompresowany zlib (ok. 3× mniej miejsca), szybki parser w procesach
TTL_S = 180 * 24 * 3600
SIMPLIFY_M = 1.0
BLOCK_MIN = 256 << 10   # odczyt ZIP-a przez Range: pierwszy blok 256 kB (małe klasy, nagłówki),
BLOCK_MAX = 8 << 20     # przy czytaniu po kolei blok rośnie ×2 do 8 MB (duże klasy, np. BUBD_A)
CLASS_WORKERS = 4       # klasy jednego powiatu pobierane równolegle (osobne połączenia)
PARSE_PROCS = max(1, min((os.cpu_count() or 2) - 1, 6))   # procesy parsujące GML (praca CPU, poza GIL)
LAYER_MAX_OBJECTS = 3_000_000   # warstwy w pamięci (LRU) do łącznie 3 mln obiektów (BUBD_A dużego powiatu ≈ 200 tys.)
HOST_CONCURRENCY = 4    # serwer opendata GUGiK zrywa połączenia przy wielu równoległych zapytaniach – limit globalny
RETRY_WAITS_S = (2.0, 6.0, 15.0)
_HOST_SEM = threading.BoundedSemaphore(HOST_CONCURRENCY)

CACHE_DIR = settings.cache_dir / "bdot10k"

# Klasa → (opis, atrybuty zachowywane w cache)
CLASSES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "SWRS_L": ("rzeki i strumienie", ("nazwa", "rodzaj", "identyfikatorMPHP", "szerokosc")),
    "SWKN_L": ("kanały", ("nazwa", "identyfikatorMPHP", "szerokosc")),
    "SWRM_L": ("rowy melioracyjne", ("szerokosc",)),
    "PTWP_A": ("wody powierzchniowe (wody stojące i płynące)", ("rodzaj", "nazwa", "identyfikatorMPHP")),
    "SKJZ_L": ("jezdnie", ("kategoriaZarzadzania", "klasaDrogi", "materialNawierzchni")),
    "SKDR_L": ("drogi", ("kategoriaZarzadzania", "klasaDrogi", "materialNawierzchni")),
    "SKTR_L": ("tory", ("rodzajPojazduSzynowego", "numerLinii")),
    "SULN_L": ("linie elektroenergetyczne", ("rodzaj", "informacjaDodatkowa")),
    "PTLZ_A": ("lasy i zadrzewienia", ("rodzaj",)),
    "BUBD_A": ("budynki", ("funkcjaOgolnaBudynku",)),
    "ADMS_A": ("miejscowości (granice)", ("nazwa", "rodzaj", "liczbaMieszkancow", "identyfikatorPRNG", "identyfikatorSIMC")),
    "ADMS_P": ("miejscowości (punkty)", ("nazwa", "rodzaj", "liczbaMieszkancow", "identyfikatorPRNG", "identyfikatorSIMC")),
}

# Atrybuty zapisane w cache przed dodaniem nazw / ID MPHP do klas wód (wpis bez pola "keep" = te atrybuty).
# Cache klasy, której zestaw atrybutów się zmienił, jest budowany ponownie przy pierwszym użyciu – pozostałe klasy zostają.
_LEGACY_KEEP: Dict[str, Tuple[str, ...]] = {"SWRS_L": ("nazwa", "rodzaj"), "SWKN_L": ("nazwa",), "SWRM_L": (), "PTWP_A": ("rodzaj",)}

GML = "{http://www.opengis.net/gml/3.2}"
_TERYT_RE = re.compile(r"^\d{4}$")


# --------------------------------------------------------------------------------------
# Odczyt ZIP-a przez HTTP Range
# --------------------------------------------------------------------------------------
def _range_get(url: str, headers: Dict[str, str], timeout: float) -> Any:
    """Zapytanie Range z globalnym limitem równoległości i ponowieniami z dłuższą przerwą (serwer zrywa połączenia)."""
    last: Optional[Exception] = None
    for wait in (0.0,) + RETRY_WAITS_S:
        if wait:
            time.sleep(wait)
        with _HOST_SEM:
            try:
                return http_request("GET", url, headers=headers, timeout=timeout, retries=0)
            except SourceError as exc:
                last = exc
    raise SourceError(f"BDOT10k: serwer nie odpowiedział po {len(RETRY_WAITS_S) + 1} próbach") from last


class RangeFile(io.RawIOBase):
    """Plik zdalny tylko do odczytu, czytany blokami (HTTP Range). zipfile potrzebuje seek/tell/read."""

    def __init__(self, url: str, on_bytes: Optional[Callable[[int], None]] = None):
        super().__init__()
        self.url = url
        self.block = BLOCK_MIN
        self.on_bytes = on_bytes
        self.pos = 0
        self._buf = b""
        self._bstart = 0
        self.downloaded = 0
        resp = _range_get(url, {"Range": "bytes=0-0"}, 30.0)
        cr = resp.headers.get("Content-Range", "")
        m = re.match(r"bytes\s+\d+-\d+/(\d+)", cr)
        if resp.status_code != 206 or not m:
            raise SourceError("serwer BDOT10k nie obsłużył zapytania częściowego (Range)")
        self.size = int(m.group(1))
        self.etag = resp.headers.get("ETag")
        self.last_modified = resp.headers.get("Last-Modified")

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        self.pos = max(0, min(self.pos, self.size))
        return self.pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        if not (self._bstart <= self.pos and self.pos + n <= self._bstart + len(self._buf)):
            sequential = self._buf and self._bstart <= self.pos <= self._bstart + len(self._buf)
            self.block = min(BLOCK_MAX, self.block * 2) if sequential else BLOCK_MIN
            end = min(self.size, self.pos + max(n, self.block)) - 1
            hdrs = {"Range": f"bytes={self.pos}-{end}"}
            if self.etag:
                hdrs["If-Range"] = self.etag
            resp = _range_get(self.url, hdrs, 90.0)
            if resp.status_code != 206:
                raise SourceError("plik BDOT10k zmienił się w trakcie pobierania – spróbuj ponownie")
            self._buf = resp.content
            self._bstart = self.pos
            self.downloaded += len(self._buf)
            if self.on_bytes:
                self.on_bytes(len(self._buf))
        off = self.pos - self._bstart
        data = self._buf[off:off + n]
        self.pos += len(data)
        return data

    def readinto(self, b: Any) -> int:
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


# --------------------------------------------------------------------------------------
# Parsowanie GML BDOT10k
# --------------------------------------------------------------------------------------
def _coords(text: Optional[str]) -> np.ndarray:
    vals = np.array((text or "").split(), dtype=float)
    if vals.size < 2 or vals.size % 2:
        return np.empty((0, 2))
    return vals.reshape(-1, 2)   # BDOT10k: kolejność E N (x, y) w EPSG:2180


def _ring(el: ET.Element) -> Optional[np.ndarray]:
    """gml:LinearRing (posList) albo gml:Ring (curveMember/Curve/segments)."""
    pl = el.find(f"{GML}posList")
    if pl is not None:
        return _coords(pl.text)
    parts = [_coords(p.text) for p in el.iter(f"{GML}posList")]
    parts = [p for p in parts if len(p)]
    if not parts:
        return None
    return np.vstack(parts)


def _polygon(el: ET.Element) -> Optional[Polygon]:
    ext = el.find(f"{GML}exterior")
    if ext is None or not len(ext):
        return None
    shell = _ring(ext[0])
    if shell is None or len(shell) < 4:
        return None
    holes = []
    for it in el.findall(f"{GML}interior"):
        if len(it):
            r = _ring(it[0])
            if r is not None and len(r) >= 4:
                holes.append(r)
    return Polygon(shell, holes)


def parse_geometry(el: ET.Element) -> Optional[BaseGeometry]:
    """Geometria GML 3.2 używana w BDOT10k → shapely (EPSG:2180)."""
    tag = el.tag[len(GML):] if el.tag.startswith(GML) else el.tag
    if tag == "Point":
        c = _coords((el.findtext(f"{GML}pos") or ""))
        return Point(c[0]) if len(c) else None
    if tag in ("LineString", "Curve"):
        parts = [_coords(p.text) for p in el.iter(f"{GML}posList")]
        parts = [p for p in parts if len(p) >= 2]
        if not parts:
            return None
        return LineString(np.vstack(parts)) if len(parts) == 1 or tag == "Curve" else MultiLineString(parts)
    if tag == "MultiCurve":
        lines = []
        for m in el.iter():
            mt = m.tag[len(GML):] if m.tag.startswith(GML) else ""
            if mt in ("LineString", "Curve"):
                g = parse_geometry(m)
                if g is not None:
                    lines.append(g)
        return MultiLineString(lines) if lines else None
    if tag == "Polygon":
        return _polygon(el)
    if tag in ("Surface", "MultiSurface"):
        polys = [p for p in (_polygon(m) for m in el.iter() if m.tag in (f"{GML}Polygon", f"{GML}PolygonPatch")) if p is not None]
        if not polys:
            return None
        return polys[0] if len(polys) == 1 else MultiPolygon(polys)
    return None


def parse_class_stream(stream: Any, keep: Sequence[str]) -> Tuple[List[bytes], List[Dict[str, Any]]]:
    """Strumieniowe parsowanie pliku klasy (np. ...__OT_SWRS_L.xml) → (WKB geometrii, atrybuty).

    Pamięć jest zwalniana po każdym obiekcie (root.clear()), więc działa też dla BUBD_A (setki MB XML).
    Obiekty bez poprawnej geometrii są pomijane (nie zgadujemy).
    """
    wkbs: List[bytes] = []
    attrs: List[Dict[str, Any]] = []
    keep_set = set(keep)
    ctx = ET.iterparse(stream, events=("start", "end"))
    root = None
    depth = 0
    for ev, el in ctx:
        if ev == "start":
            if root is None:
                root = el
            depth += 1
            continue
        depth -= 1
        # obiekt: element ot:OT_* bezpośrednio w gml:featureMember (głębokość 2 przy zamknięciu)
        if depth == 2 and "}OT_" in el.tag:
            geom = None
            rec: Dict[str, Any] = {}
            for ch in el:
                name = ch.tag.rsplit("}", 1)[-1]
                if name == "geometria":
                    if len(ch):
                        try:
                            geom = parse_geometry(ch[0])
                        except (ValueError, shapely.errors.ShapelyError):
                            geom = None
                elif name in keep_set and ch.text and ch.text.strip():
                    rec[name] = ch.text.strip()
            if geom is not None and not geom.is_empty:
                if not geom.is_valid and geom.geom_type in ("Polygon", "MultiPolygon"):
                    geom = shapely.make_valid(geom)
                g = shapely.simplify(geom, SIMPLIFY_M, preserve_topology=True) if geom.geom_type != "Point" else geom
                if not g.is_empty:
                    wkbs.append(shapely.to_wkb(g))
                    attrs.append(rec)
        if depth == 1 and root is not None:
            root.clear()   # zamknięty gml:featureMember – zwalniamy pamięć
    return wkbs, attrs


# --------------------------------------------------------------------------------------
# Cache na dysku
# --------------------------------------------------------------------------------------
def _paths(teryt: str, cls: str) -> Tuple[str, str]:
    d = CACHE_DIR / teryt
    return str(d / f"{cls}.wkb"), str(d / f"{cls}.json")


def _write_cache(teryt: str, cls: str, wkbs: Any, attrs: List[Dict[str, Any]], meta: Dict[str, Any],
                 offsets: Optional[List[int]] = None) -> None:
    """wkbs: lista WKB albo sklejony blok bajtów (wtedy z `offsets`)."""
    os.makedirs(CACHE_DIR / teryt, exist_ok=True)
    bin_path, json_path = _paths(teryt, cls)
    if offsets is None:
        offsets = [0]
        for w in wkbs:
            offsets.append(offsets[-1] + len(w))
        wkbs = b"".join(wkbs)
    tmp_bin, tmp_json = bin_path + ".tmp", json_path + ".tmp"
    with open(tmp_bin, "wb") as f:
        f.write(zlib.compress(wkbs, 1))
    doc = dict(meta, version=FORMAT_VERSION, teryt=teryt, cls=cls, keep=list(CLASSES[cls][1]) if cls in CLASSES else [],
               count=len(offsets) - 1, offsets=offsets, attrs=attrs)
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_bin, bin_path)
    os.replace(tmp_json, json_path)   # .json zapisywany jako ostatni = znacznik kompletnego wpisu


def _read_meta(teryt: str, cls: str) -> Optional[Dict[str, Any]]:
    bin_path, json_path = _paths(teryt, cls)
    if not (os.path.exists(json_path) and os.path.exists(bin_path)):
        return None
    try:
        with open(json_path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if doc.get("version") != FORMAT_VERSION or time.time() - float(doc.get("stored_ts", 0)) > TTL_S:
        return None
    if cls in CLASSES:
        want = list(CLASSES[cls][1])
        have = doc.get("keep", list(_LEGACY_KEEP.get(cls, want)))
        if list(have) != want:
            return None   # inny zestaw atrybutów (np. doszła nazwa cieku) – klasa zostanie pobrana ponownie
    return doc


@dataclass
class Layer:
    teryt: str
    cls: str
    geoms: np.ndarray
    attrs: List[Dict[str, Any]]
    meta: Dict[str, Any]
    tree: STRtree = field(init=False)

    def __post_init__(self) -> None:
        self.tree = STRtree(self.geoms)

    def provenance(self) -> Dict[str, Any]:
        return layer_provenance([self])


_LAYERS: "OrderedDict[Tuple[str, str], Layer]" = OrderedDict()
_LAYERS_LOCK = threading.Lock()
_TERYT_LOCKS: Dict[str, threading.Lock] = {}
_TERYT_LOCKS_GUARD = threading.Lock()


def _teryt_lock(teryt: str) -> threading.Lock:
    with _TERYT_LOCKS_GUARD:
        return _TERYT_LOCKS.setdefault(teryt, threading.Lock())


def _load_from_disk(teryt: str, cls: str) -> Optional[Layer]:
    doc = _read_meta(teryt, cls)
    if doc is None:
        return None
    bin_path, _ = _paths(teryt, cls)
    try:
        with open(bin_path, "rb") as f:
            blob = zlib.decompress(f.read())
    except (OSError, zlib.error):
        return None
    offs = doc["offsets"]
    if len(offs) != doc["count"] + 1 or offs[-1] != len(blob):
        log.warning("Uszkodzony cache BDOT10k %s/%s – pobieram ponownie", teryt, cls)
        return None
    wkbs = [blob[offs[i]:offs[i + 1]] for i in range(doc["count"])]
    geoms = shapely.from_wkb(np.array(wkbs, dtype=object)) if wkbs else np.array([], dtype=object)
    meta = {k: v for k, v in doc.items() if k not in ("offsets", "attrs")}
    return Layer(teryt, cls, geoms, doc["attrs"], meta)


def _remember(layer: Layer) -> None:
    with _LAYERS_LOCK:
        _LAYERS[(layer.teryt, layer.cls)] = layer
        _LAYERS.move_to_end((layer.teryt, layer.cls))
        while len(_LAYERS) > 1 and sum(len(l.geoms) for l in _LAYERS.values()) > LAYER_MAX_OBJECTS:
            _LAYERS.popitem(last=False)


def _cached_layer(teryt: str, cls: str) -> Optional[Layer]:
    with _LAYERS_LOCK:
        lay = _LAYERS.get((teryt, cls))
        if lay is not None:
            _LAYERS.move_to_end((teryt, cls))
            return lay
    lay = _load_from_disk(teryt, cls)
    if lay is not None:
        _remember(lay)
    return lay


def zip_url(teryt: str) -> str:
    return ZIP_URL.format(ww=teryt[:2], teryt=teryt)


ProgressFn = Callable[[str], None]


def _download(teryt: str, classes: List[str], progress: Optional[ProgressFn]) -> None:
    """Pobiera (Range) i parsuje brakujące klasy jednego powiatu; wynik zapisuje do cache na dysku."""
    url = zip_url(teryt)
    rf = RangeFile(url)
    try:
        zf = zipfile.ZipFile(rf)
    except zipfile.BadZipFile as exc:
        raise SourceError(f"BDOT10k {teryt}: uszkodzony plik ZIP") from exc
    members = {m.filename.rsplit("__OT_", 1)[-1][:-4]: m for m in zf.infolist() if "__OT_" in m.filename and m.filename.endswith(".xml")}
    meta = {"source_url": url, "last_modified": rf.last_modified, "etag": rf.etag, "fetched_at": iso_utc(),
            "stored_ts": time.time(), "zip_size": rf.size}
    for cls in classes:
        if cls not in members:
            # klasy nie ma w paczce powiatu = w powiecie brak takich obiektów (np. brak linii kolejowych)
            _write_cache(teryt, cls, [], [], dict(meta, absent=True))
    todo = sorted((c for c in classes if c in members), key=lambda c: -members[c].compress_size)   # największe najpierw
    if progress and todo:
        mb = sum(members[c].compress_size for c in todo) / 1e6
        progress(f"Pobieranie BDOT10k – powiat {teryt}: {len(todo)} klas, {mb:.1f} MB (z paczki {rf.size / 1e6:.0f} MB)")

    def one(cls: str) -> None:
        # osobne połączenie na wątek (RangeFile nie jest wątkowo bezpieczny); skompresowane bajty klasy jednym
        # zapytaniem Range, parsowanie w procesie potomnym (bdot10k_fast, wynik identyczny z parse_class_stream)
        m = members[cls]
        t = time.monotonic()
        raw = _member_raw(rf if cls == todo[0] else RangeFile(url), m)
        blob, offsets, attrs = _parse_in_process(raw, m.compress_type, CLASSES[cls][1])
        _write_cache(teryt, cls, blob, attrs, meta, offsets=offsets)
        log.info("BDOT10k %s/%s: %d obiektów, %.1f MB skompr., %.1f s", teryt, cls, len(offsets) - 1, m.compress_size / 1e6,
                 time.monotonic() - t)

    if todo:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(CLASS_WORKERS, len(todo)), thread_name_prefix=f"mrolnik-bdot{teryt}") as ex:
            for f in [ex.submit(one, c) for c in todo]:
                f.result()


def _member_raw(rf: "RangeFile", m: zipfile.ZipInfo) -> bytes:
    """Skompresowane bajty pliku z ZIP-a (nagłówek lokalny 30 B + nazwa + extra, potem dane)."""
    import struct
    rf.seek(m.header_offset)
    head = rf.read(30)
    if len(head) != 30 or head[:4] != b"PK\x03\x04":
        raise SourceError("uszkodzony nagłówek pliku w ZIP")
    n, e = struct.unpack("<HH", head[26:30])
    rf.seek(m.header_offset + 30 + n + e)
    raw = rf.read(m.compress_size)
    if len(raw) != m.compress_size:
        raise SourceError("niepełne dane pliku w ZIP")
    return raw


_PROC_POOL: Any = None
_PROC_LOCK = threading.Lock()


def run_in_process(fn: Callable[..., Any], *args: Any) -> Any:
    """Zadanie CPU (parsowanie GML) w puli procesów; gdy pula niedostępna – w bieżącym procesie (wynik ten sam)."""
    global _PROC_POOL
    if PARSE_PROCS > 1 and not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            with _PROC_LOCK:
                if _PROC_POOL is None:
                    from concurrent.futures import ProcessPoolExecutor
                    _PROC_POOL = ProcessPoolExecutor(max_workers=PARSE_PROCS)
                pool = _PROC_POOL
            return pool.submit(fn, *args).result()
        except Exception as exc:  # noqa: BLE001 – np. BrokenProcessPool: liczymy lokalnie
            if isinstance(exc, (ValueError, SourceError)):
                raise
            log.warning("Pula procesów niedostępna (%s) – obliczenia w wątku", exc)
            with _PROC_LOCK:
                _PROC_POOL = None
    return fn(*args)


def _parse_in_process(raw: bytes, compress_type: int, keep: Sequence[str]) -> Tuple[bytes, List[int], List[Dict[str, Any]]]:
    from src.data import bdot10k_fast
    return run_in_process(bdot10k_fast.parse_raw, raw, compress_type, tuple(keep))


def ensure_layers(teryt: str, classes: Iterable[str], progress: Optional[ProgressFn] = None) -> Dict[str, Layer]:
    """Warstwy BDOT10k powiatu (z pamięci, z dysku albo pobrane). Rzuca SourceError, gdy pobranie się nie uda."""
    if not _TERYT_RE.match(teryt or ""):
        raise SourceError(f"niepoprawny kod powiatu: {teryt!r}")
    wanted = [c for c in dict.fromkeys(classes) if c in CLASSES]
    out: Dict[str, Layer] = {}
    missing_cls = []
    for c in wanted:
        lay = _cached_layer(teryt, c)
        if lay is None:
            missing_cls.append(c)
        else:
            out[c] = lay
    if not missing_cls:
        return out
    with _teryt_lock(teryt):   # jedno pobieranie naraz na powiat (inne wątki czekają i biorą wynik z dysku)
        still = [c for c in missing_cls if _cached_layer(teryt, c) is None]
        if still:
            try:
                _download(teryt, still, progress)
            except (SourceError, OSError, ET.ParseError, zipfile.BadZipFile, EOFError) as exc:
                record_error(SOURCE_BDOT10K, f"BDOT10k {teryt}: {friendly(exc)}")
                raise SourceError(f"BDOT10k {teryt}: {friendly(exc)}") from exc
            record_ok(SOURCE_BDOT10K, f"OK (powiat {teryt})")
        for c in missing_cls:
            lay = _cached_layer(teryt, c)
            if lay is None:
                raise SourceError(f"BDOT10k {teryt}: nie udało się wczytać klasy {c}")
            out[c] = lay
    return out


# --------------------------------------------------------------------------------------
# Zapytania przestrzenne
# --------------------------------------------------------------------------------------
AttrPred = Optional[Callable[[Dict[str, Any]], bool]]


def nearest(layers: Sequence[Layer], geom: BaseGeometry, radius: float, pred: AttrPred = None
            ) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
    """Najmniejsza odległość [m] od geometrii do obiektu warstw w promieniu `radius` (0 = styka się / przecina).

    (None, None), gdy w promieniu nie ma żadnego obiektu spełniającego `pred` – to wynik, nie brak danych.
    """
    best_d: Optional[float] = None
    best_a: Optional[Dict[str, Any]] = None
    env = shapely.box(*geom.buffer(radius, quad_segs=2).bounds) if radius > 0 else shapely.box(*geom.bounds)
    for lay in layers:
        if not len(lay.geoms):
            continue
        idx = lay.tree.query(env)
        if pred is not None:
            idx = np.array([i for i in idx if pred(lay.attrs[i])], dtype=int)
        if not len(idx):
            continue
        d = shapely.distance(geom, lay.geoms[idx])
        j = int(np.argmin(d))
        if d[j] <= radius and (best_d is None or d[j] < best_d):
            best_d, best_a = float(d[j]), lay.attrs[idx[j]]
    return best_d, best_a


def within(layers: Sequence[Layer], geom: BaseGeometry, radius: float, pred: AttrPred = None) -> List[Tuple[BaseGeometry, Dict[str, Any]]]:
    """Obiekty warstw (geometria, atrybuty) w odległości ≤ radius od geometrii."""
    out = []
    env = shapely.box(*geom.buffer(radius, quad_segs=2).bounds)
    for lay in layers:
        if not len(lay.geoms):
            continue
        for i in lay.tree.query(env):
            if pred is not None and not pred(lay.attrs[i]):
                continue
            g = lay.geoms[i]
            if shapely.distance(geom, g) <= radius:
                out.append((g, lay.attrs[i]))
    return out


def covered_fraction(layers: Sequence[Layer], geom: BaseGeometry, pred: AttrPred = None) -> float:
    """Udział powierzchni geometrii pokryty poligonami warstw (0–1)."""
    if geom.area <= 0:
        return 0.0
    parts = []
    for lay in layers:
        if not len(lay.geoms):
            continue
        for i in lay.tree.query(geom):
            if pred is not None and not pred(lay.attrs[i]):
                continue
            g = lay.geoms[i]
            if g.geom_type in ("Polygon", "MultiPolygon") and g.intersects(geom):
                parts.append(g.intersection(geom))
    if not parts:
        return 0.0
    return float(min(1.0, shapely.union_all(parts).area / geom.area))


def layer_provenance(layers: Sequence[Layer], note: str = "") -> Dict[str, Any]:
    if not layers:
        return provenance("GUGiK BDOT10k", SOURCE_PAGE, "brak", note=note or "Brak warstw BDOT10k.")
    teryts = sorted({l.teryt for l in layers})
    classes = sorted({l.cls for l in layers})
    dates = sorted({l.meta.get("last_modified") for l in layers if l.meta.get("last_modified")})
    fetched = min((l.meta.get("fetched_at") for l in layers if l.meta.get("fetched_at")), default=None)
    observed = None
    if dates:
        try:
            from email.utils import parsedate_to_datetime
            observed = iso_utc(min(parsedate_to_datetime(d) for d in dates))
        except (TypeError, ValueError):
            observed = None
    txt = (f"Baza Danych Obiektów Topograficznych BDOT10k, powiaty {', '.join(teryts)}; klasy {', '.join(classes)}. "
           f"Wersja pliku: {', '.join(dates) if dates else 'nieznana'}. Geometrie uproszczone do {SIMPLIFY_M:.0f} m; "
           "odległości liczone w EPSG:2180 od granicy działki. ")
    return provenance(f"GUGiK BDOT10k ({', '.join(classes)})", SOURCE_PAGE, "pomiar", note=(txt + note).strip(),
                      observed_at=observed, fetched_at=fetched, resolution="1:10 000")


def reset_memory() -> None:
    """Tylko dla testów."""
    with _LAYERS_LOCK:
        _LAYERS.clear()

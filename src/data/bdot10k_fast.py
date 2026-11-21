"""Szybki parser plików klas BDOT10k (GML 3.2) – do uruchamiania w osobnych procesach.

Wynik jest identyczny z wzorcowym parserem xml.etree (bdot10k.parse_class_stream; porównanie w testach):
te same obiekty w tej samej kolejności, te same atrybuty i bajt w bajt te same geometrie WKB.
Szybciej, bo: (1) tekst dzielimy na obiekty po znaczniku </gml:featureMember> i czytamy wyrażeniami regularnymi
(praca w C, bez 2 mln zdarzeń iterparse), (2) posList przez numpy, (3) naprawa, upraszczanie i WKB wektorowo
na całej tablicy geometrii. Moduł ma minimalne importy (szybki start procesu potomnego na Windows).
"""
from __future__ import annotations

import codecs
import re
import zlib
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from xml.sax.saxutils import unescape

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon

SIMPLIFY_M = 1.0
FM_END = "</gml:featureMember>"
_ENT = {"&quot;": '"', "&apos;": "'"}
_ATTR = re.compile(r"<ot:(\w+)(?:\s[^>]*)?>([^<]*)</ot:\1>")
_POSLIST = re.compile(r"<gml:posList(?:\s[^>]*)?>([^<]*)</gml:posList>")
_POS = re.compile(r"<gml:pos(?:\s[^>]*)?>([^<]*)</gml:pos>")
_FIRST_TAG = re.compile(r"<gml:(\w+)")
_LINES = re.compile(r"<gml:(LineString|Curve)\b.*?</gml:\1>", re.S)
_POLYS = re.compile(r"<gml:(Polygon|PolygonPatch)\b.*?</gml:\1>", re.S)
_EXT = re.compile(r"<gml:exterior>(.*?)</gml:exterior>", re.S)
_INT = re.compile(r"<gml:interior>(.*?)</gml:interior>", re.S)


def _xy(text: str) -> np.ndarray:
    v = np.array(text.split(), dtype=float)
    if v.size < 2 or v.size % 2:
        return np.empty((0, 2))
    return v.reshape(-1, 2)


def _ring(block: str) -> Optional[np.ndarray]:
    parts = [p for p in (_xy(t) for t in _POSLIST.findall(block)) if len(p)]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else np.vstack(parts)


def _polygon(block: str) -> Optional[Polygon]:
    m = _EXT.search(block)
    if not m:
        return None
    shell = _ring(m.group(1))
    if shell is None or len(shell) < 4:
        return None
    holes = []
    for im in _INT.finditer(block):
        r = _ring(im.group(1))
        if r is not None and len(r) >= 4:
            holes.append(r)
    return Polygon(shell, holes)


def _line(block: str):
    parts = [p for p in (_xy(t) for t in _POSLIST.findall(block)) if len(p) >= 2]
    if not parts:
        return None
    return LineString(parts[0] if len(parts) == 1 else np.vstack(parts))


def geometry(geo: str):
    m = _FIRST_TAG.search(geo)
    if not m:
        return None
    tag = m.group(1)
    if tag == "Point":
        pm = _POS.search(geo)
        c = _xy(pm.group(1)) if pm else np.empty((0, 2))
        return Point(c[0]) if len(c) else None
    if tag in ("LineString", "Curve"):
        return _line(geo)
    if tag == "MultiCurve":
        lines = [g for g in (_line(lm.group(0)) for lm in _LINES.finditer(geo)) if g is not None]
        return MultiLineString(lines) if lines else None
    if tag == "Polygon":
        return _polygon(geo)
    if tag in ("Surface", "MultiSurface"):
        polys = [p for p in (_polygon(pm.group(0)) for pm in _POLYS.finditer(geo)) if p is not None]
        if not polys:
            return None
        return polys[0] if len(polys) == 1 else MultiPolygon(polys)
    return None


def iter_members(chunks: Iterable[str]) -> Iterator[str]:
    """Teksty kolejnych obiektów (gml:featureMember) ze strumienia fragmentów tekstu."""
    buf = ""
    for ch in chunks:
        buf += ch
        start = 0
        while True:
            j = buf.find(FM_END, start)
            if j < 0:
                break
            yield buf[start:j]
            start = j + len(FM_END)
        buf = buf[start:]


def _tok(text: str) -> Optional[List[str]]:
    """Tokeny posList jak w _xy: None, gdy nie da się ułożyć par (x, y)."""
    t = text.split()
    return t if len(t) >= 2 and len(t) % 2 == 0 else None


def _spec(geo: str) -> Any:
    """Szybka ścieżka dla prostych geometrii: ('poly', [tokeny pierścieni]) / ('line', tokeny); inaczej geometria shapely."""
    m = _FIRST_TAG.search(geo)
    if not m:
        return None
    tag = m.group(1)
    if tag == "Polygon" and "<gml:Ring" not in geo:
        em = _EXT.search(geo)
        if not em:
            return None
        shell = [t for t in (_tok(x) for x in _POSLIST.findall(em.group(1))) if t]
        if not shell:
            return None
        shell = shell[0] if len(shell) == 1 else [v for t in shell for v in t]
        if len(shell) < 8:
            return None
        rings = [shell]
        for im in _INT.finditer(geo):
            parts = [t for t in (_tok(x) for x in _POSLIST.findall(im.group(1))) if t]
            if parts:
                r = parts[0] if len(parts) == 1 else [v for t in parts for v in t]
                if len(r) >= 8:
                    rings.append(r)
        return ("poly", rings)
    if tag in ("LineString", "Curve"):
        parts = [t for t in (_tok(x) for x in _POSLIST.findall(geo)) if t and len(t) >= 4]
        if not parts:
            return None
        return ("line", parts[0] if len(parts) == 1 else [v for t in parts for v in t])
    return geometry(geo)


def _build_fast(specs: List[Any]) -> List[Any]:
    """Specyfikacje → geometrie shapely; wielokąty i linie hurtowo (from_ragged_array), wynik jak z konstruktorów."""
    out: List[Any] = list(specs)
    polys = [i for i, s in enumerate(specs) if isinstance(s, tuple) and s[0] == "poly"]
    lines = [i for i, s in enumerate(specs) if isinstance(s, tuple) and s[0] == "line"]
    if polys:
        toks: List[str] = []
        ring_off = [0]
        geom_off = [0]
        for i in polys:
            for r in specs[i][1]:
                toks.extend(r)
                ring_off.append(ring_off[-1] + len(r) // 2)
            geom_off.append(len(ring_off) - 1)
        xy = np.array(toks, dtype=float).reshape(-1, 2)
        ro = np.array(ring_off)
        closed = (xy[ro[:-1]] == xy[ro[1:] - 1]).all(axis=1)
        go = np.array(geom_off)
        ok_geom = np.array([closed[go[k]:go[k + 1]].all() for k in range(len(polys))])
        if ok_geom.all():
            built = shapely.from_ragged_array(shapely.GeometryType.POLYGON, xy, (ro, go))
            for k, i in enumerate(polys):
                out[i] = built[k]
        else:   # pierścień niezamknięty: konstruktor Polygon (domyka pierścień) – tak jak wzorcowy parser
            for k, i in enumerate(polys):
                rings = [xy[ro[j]:ro[j + 1]] for j in range(go[k], go[k + 1])]
                out[i] = Polygon(rings[0], rings[1:])
    if lines:
        toks = []
        off = [0]
        for i in lines:
            toks.extend(specs[i][1])
            off.append(off[-1] + len(specs[i][1]) // 2)
        xy = np.array(toks, dtype=float).reshape(-1, 2)
        built = shapely.from_ragged_array(shapely.GeometryType.LINESTRING, xy, (np.array(off),))
        for k, i in enumerate(lines):
            out[i] = built[k]
    return out


def parse_members(members: Iterable[str], keep: Sequence[str]) -> Tuple[List[bytes], List[Dict[str, Any]]]:
    keep_set = set(keep)
    specs: List[Any] = []
    attrs: List[Dict[str, Any]] = []
    for m in members:
        if "<ot:OT_" not in m:
            continue
        gi = m.find("<ot:geometria")
        ge = m.find("</ot:geometria>")
        if gi < 0 or ge <= gi:
            continue
        try:
            spec = _spec(m[gi:ge])
        except (ValueError, shapely.errors.ShapelyError):
            spec = None
        if spec is None or (not isinstance(spec, tuple) and spec.is_empty):
            continue
        rec: Dict[str, Any] = {}
        for name, val in _ATTR.findall(m, 0, gi):
            if name in keep_set:
                v = val.strip()
                if v:
                    rec[name] = unescape(v, _ENT)
        for name, val in _ATTR.findall(m, ge):
            if name in keep_set:
                v = val.strip()
                if v:
                    rec[name] = unescape(v, _ENT)
        specs.append(spec)
        attrs.append(rec)
    if not specs:
        return [], []
    geoms = _build_fast(specs)
    arr = np.array(geoms, dtype=object)
    tid = shapely.get_type_id(arr)
    poly = (tid == 3) | (tid == 6)
    bad = poly & ~shapely.is_valid(arr)
    if bad.any():
        arr[bad] = shapely.make_valid(arr[bad])
    nonpt = tid != 0
    if nonpt.any():
        arr[nonpt] = shapely.simplify(arr[nonpt], SIMPLIFY_M, preserve_topology=True)
    ok = ~shapely.is_empty(arr)
    wkbs = list(shapely.to_wkb(arr[ok]))
    attrs = [a for a, k in zip(attrs, ok) if k]
    return wkbs, attrs


def _text_chunks_from_raw(raw: bytes, compress_type: int, size: int = 1 << 20) -> Iterator[str]:
    dec = codecs.getincrementaldecoder("utf-8-sig")()
    if compress_type == 0:
        for i in range(0, len(raw), size):
            yield dec.decode(raw[i:i + size])
    else:
        z = zlib.decompressobj(-15)
        for i in range(0, len(raw), size):
            yield dec.decode(z.decompress(raw[i:i + size]))
        yield dec.decode(z.flush())
    yield dec.decode(b"", final=True)


def parse_raw(raw: bytes, compress_type: int, keep: Sequence[str]) -> Tuple[bytes, List[int], List[Dict[str, Any]]]:
    """Zadanie dla procesu: skompresowane (deflate) bajty pliku klasy z ZIP-a → (WKB sklejone, przesunięcia, atrybuty)."""
    if compress_type not in (0, 8):
        raise ValueError(f"nieobsługiwana kompresja ZIP: {compress_type}")
    wkbs, attrs = parse_members(iter_members(_text_chunks_from_raw(raw, compress_type)), keep)
    offsets = [0]
    for w in wkbs:
        offsets.append(offsets[-1] + len(w))
    return b"".join(wkbs), offsets, attrs


def parse_stream_fast(stream: Any, keep: Sequence[str]) -> Tuple[List[bytes], List[Dict[str, Any]]]:
    """Ten sam wynik co parse_raw, ale z otwartego strumienia bajtów (np. zipfile.open) – do testów i małych plików."""
    dec = codecs.getincrementaldecoder("utf-8-sig")()

    def chunks() -> Iterator[str]:
        while True:
            b = stream.read(1 << 20)
            if not b:
                yield dec.decode(b"", final=True)
                return
            yield dec.decode(b)

    return parse_members(iter_members(chunks()), keep)


# --------------------------------------------------------------------------------------
# Strona odpowiedzi zbiorczego WFS EGiB (ms:dzialki) – też w procesie potomnym
# --------------------------------------------------------------------------------------
EGIB_FIELDS = ("ID_DZIALKI", "NUMER_DZIALKI", "NAZWA_OBREBU", "NAZWA_GMINY", "POLE_EWIDENYJNE")
_EG_FIELD = re.compile(r"<ms:(ID_DZIALKI|NUMER_DZIALKI|NAZWA_OBREBU|NAZWA_GMINY|POLE_EWIDENYJNE)>([^<]*)</ms:\1>")
_EG_NEXT = re.compile(r'\bnext="([^"]+)"')


def parse_egib_page(text: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Strona GML 3.2 → ([{id, nr, obreb, gmina, pole_ha, wkb}], adres następnej strony | None).

    Bierzemy WYŁĄCZNIE identyfikator, numer, obręb, gminę, powierzchnię ewidencyjną i geometrię (inne pola, np. klasy
    użytków czy grupy rejestrowe, są pomijane i nigdzie nie zapisywane). Współrzędne w odpowiedzi: EPSG:2180 w kolejności
    N, E (urn) – zamieniamy na E, N jak w całej aplikacji.
    """
    head = text[:4000]
    nm = _EG_NEXT.search(head)
    nxt = unescape(nm.group(1)) if nm else None
    rows: List[Dict[str, Any]] = []
    geoms: List[Any] = []
    for m in text.split("</wfs:member>"):
        gi = m.find("<ms:geom>")
        ge = m.find("</ms:geom>")
        if gi < 0 or ge <= gi:
            continue
        f = dict(_EG_FIELD.findall(m))
        if not f.get("ID_DZIALKI"):
            continue
        try:
            g = geometry(m[gi + 9:ge])
        except (ValueError, shapely.errors.ShapelyError):
            g = None
        if g is None or g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        try:
            pole = float(f.get("POLE_EWIDENYJNE", "").replace(",", ".")) if f.get("POLE_EWIDENYJNE") else None
        except ValueError:
            pole = None
        rows.append({"id": unescape(f["ID_DZIALKI"], _ENT).strip(), "nr": unescape(f.get("NUMER_DZIALKI", ""), _ENT).strip(),
                     "obreb": unescape(f.get("NAZWA_OBREBU", ""), _ENT).strip(), "gmina": unescape(f.get("NAZWA_GMINY", ""), _ENT).strip(),
                     "pole_ha": pole})
        geoms.append(g)
    if geoms:
        arr = shapely.transform(np.array(geoms, dtype=object), lambda c: c[:, ::-1])   # (N, E) → (E, N)
        bad = ~shapely.is_valid(arr)
        if bad.any():
            arr[bad] = shapely.make_valid(arr[bad])
        for r, w in zip(rows, shapely.to_wkb(arr)):
            r["wkb"] = w
    return rows, nxt

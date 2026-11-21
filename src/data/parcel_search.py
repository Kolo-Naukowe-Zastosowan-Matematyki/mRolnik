"""Wyszukiwarka działek: filtrowanie puli działek użytkownika kryteriami z otwartych danych.

Pula = działki z „Moich działek” (gospodarstwa) i pola dodane przez użytkownika; geometria z GUGiK ULDK.
Kryterium ma tryb „konieczne” (odrzuca) albo „mile widziane” (tylko punkty). Brak danych dla kryterium
koniecznego NIE odrzuca działki – działka trafia do grupy „do sprawdzenia”.

Kolejność (najpierw tanie): granice ULDK → powierzchnia, kształt, administracja → (tylko działki, które przeszły)
BDOT10k: wody, drogi, linie, kolej, zabudowa, las; miasta (PRNG + BDOT10k ADMS) → NMT (spadek) → ISOK (powódź)
→ NDVI z cache → scalanie sąsiednich działek. Każda wartość ma proweniencję; brak danych = „brak danych”, nie zero.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import shapely
import shapely.wkt
from shapely.geometry import Point, mapping
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from src.data import bdot10k, egib, flood, places_prng, sentinel, terrain
from src.data.bdot10k import Layer
from src.data.cadastre import ParcelData, _build_parcel, fetch_parcel_by_id, fetch_parcel_by_xy, parcel_provenance, reproject, transformer_to_2180, \
    transformer_to_wgs84
from src.data.common import (
    SOURCE_BDOT10K,
    SOURCE_EGIB,
    SOURCE_ISOK,
    SOURCE_NMT,
    SOURCE_PRNG,
    SourceError,
    friendly,
    http_request,
    iso_utc,
    missing,
    provenance,
)

log = logging.getLogger(__name__)

SEARCH_SOURCES = (SOURCE_BDOT10K, SOURCE_PRNG, SOURCE_NMT, SOURCE_ISOK, SOURCE_EGIB)
MAX_POOL = 300
JOB_TTL_S = 3600
MAX_JOBS = 30
_IO = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mrolnik-search")
_RUNNER = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mrolnik-searchjob")

# --------------------------------------------------------------------------------------
# Definicje kryteriów
# --------------------------------------------------------------------------------------
LABELS: Dict[str, str] = {
    "area": "Powierzchnia", "shape": "Kształt", "admin": "Położenie administracyjne",
    "water": "Odległość od wód", "town": "Odległość od miasta", "slope": "Nachylenie terenu", "flood": "Zagrożenie powodziowe",
    "road": "Dostęp do drogi", "power": "Linia elektroenergetyczna", "rail": "Linia kolejowa", "buildings": "Odległość od zabudowy",
    "forest": "Udział lasu i zadrzewień", "ndvi": "Kondycja roślin (NDVI)", "merge": "Możliwość scalenia",
}
ORDER = list(LABELS)
CHEAP = ("area", "shape", "admin")
BDOT_CRIT = ("water", "road", "power", "rail", "buildings", "forest")

WATER_KINDS = {
    "rivers": [("SWRS_L", None), ("PTWP_A", lambda a: "płyn" in (a.get("rodzaj") or ""))],
    "canals": [("SWKN_L", None)],
    "ditches": [("SWRM_L", None)],
    "standing": [("PTWP_A", lambda a: "stoj" in (a.get("rodzaj") or ""))],
}
ROAD_CATS = {"krajowa": "krajowa", "wojewodzka": "wojewódzka", "powiatowa": "powiatowa", "gminna": "gminna", "wewnetrzna": "wewnętrzna"}
SHAPE_PRESETS = {"any": None, "compact": ("pp", 0.5), "rect": ("rect", 0.85)}


def radius_for(key: str, c: Dict[str, Any]) -> float:
    """Promień przeszukiwania BDOT10k [m]: próg ×2 (żeby pokazać też odległość ponad progiem), min. 500 m, maks. 5 km."""
    if key == "forest":
        return 0.0
    d = float(c.get("dist_m") or 0)
    return float(min(5000.0, max(500.0, 2 * d)))


# --------------------------------------------------------------------------------------
# Geometria: kształt, scalanie
# --------------------------------------------------------------------------------------
def shape_metrics(g: BaseGeometry, with_width: bool = True) -> Dict[str, Optional[float]]:
    """Zwartość Polsby-Popper, wydłużenie i prostokątność (względem minimalnego obróconego prostokąta), min. szerokość [m]."""
    a, per = g.area, g.length
    pp = 4 * math.pi * a / (per * per) if per > 0 else None
    mrr = shapely.minimum_rotated_rectangle(g)
    elong = rect = None
    if mrr.geom_type == "Polygon":
        xs, ys = mrr.exterior.coords.xy
        s1 = math.hypot(xs[1] - xs[0], ys[1] - ys[0])
        s2 = math.hypot(xs[2] - xs[1], ys[2] - ys[1])
        short, long_ = min(s1, s2), max(s1, s2)
        elong = long_ / short if short > 0 else None
        rect = a / mrr.area if mrr.area > 0 else None
    return {"pp": None if pp is None else round(pp, 3), "elongation": None if elong is None else round(elong, 2),
            "rect": None if rect is None else round(rect, 3), "min_width_m": round(min_width(g), 1) if with_width else None}


def min_width(g: BaseGeometry, tol: float = 0.25) -> float:
    """Szerokość ≈ 2·r, gdzie r = największy promień, dla którego bufor ujemny jest niepusty (bisekcja)."""
    lo, hi = 0.0, math.sqrt(g.area / math.pi) + 1.0
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if g.buffer(-mid, quad_segs=4).is_empty:
            hi = mid
        else:
            lo = mid
    return 2 * lo


def merge_groups(geoms: Dict[int, BaseGeometry], gap_m: float = 1.0) -> List[List[int]]:
    """Grupy działek przylegających (odstęp ≤ gap_m), union-find na STRtree. Zwraca listy kluczy (≥ 1 element)."""
    keys = list(geoms)
    if not keys:
        return []
    arr = [geoms[k] for k in keys]
    tree = STRtree(arr)
    parent = list(range(len(keys)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, g in enumerate(arr):
        for j in tree.query(g.buffer(gap_m, quad_segs=2)):
            if j != i and arr[i].distance(arr[j]) <= gap_m:
                ri, rj = find(i), find(int(j))
                if ri != rj:
                    parent[ri] = rj
    groups: Dict[int, List[int]] = {}
    for i in range(len(keys)):
        groups.setdefault(find(i), []).append(keys[i])
    return list(groups.values())


# --------------------------------------------------------------------------------------
# Ocena
# --------------------------------------------------------------------------------------
def crit(status: str, value: Any = None, text: str = "", note: str = "") -> Dict[str, Any]:
    return {"status": status, "value": value, "text": text, "note": note}


def _f(v: float, d: int = 1) -> str:
    """Liczba z przecinkiem dziesiętnym (pl)."""
    return f"{v:.{d}f}".replace(".", ",")


def _pop(n: int) -> str:
    return f"{n:,}".replace(",", "\u00a0")


def _m(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:.0f} m" if v < 1000 else f"{_f(v / 1000)} km"


def _cmp_dist(d: Optional[float], c: Dict[str, Any], radius: float, what: str) -> Dict[str, Any]:
    """Ocena odległości d (None = brak obiektu w promieniu) wobec progu „≤” albo „≥”."""
    thr = float(c.get("dist_m") or 0)
    op = c.get("op") or "le"
    text = f"{what}: {_m(d)}" if d is not None else f"{what}: ponad {_m(radius)}"
    if op == "le":
        ok = d is not None and d <= thr
    else:
        ok = d is None or d >= thr
    return crit("ok" if ok else "fail", None if d is None else round(d, 1), text)


def evaluate_cheap(p: ParcelData, c: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    g = p.polygon_2180
    if "area" in c:
        ha = g.area / 10_000
        lo, hi = c["area"].get("min_ha"), c["area"].get("max_ha")
        ok = (lo is None or ha >= lo) and (hi is None or ha <= hi)
        out["area"] = crit("ok" if ok else "fail", round(ha, 2), f"{_f(ha, 2)} ha")
    if "shape" in c:
        sm = shape_metrics(g)
        preset = SHAPE_PRESETS.get(c["shape"].get("preset") or "any")
        ok = True
        if preset:
            k, thr = preset
            ok = sm[k] is not None and sm[k] >= thr
        mw = c["shape"].get("min_width_m")
        if mw:
            ok = ok and sm["min_width_m"] >= mw
        txt = f"zwartość {_f(sm['pp'], 2)}, prostokątność {_f(sm['rect'], 2)}, szer. min. ok. {sm['min_width_m']:.0f} m" \
            if sm["pp"] is not None and sm["rect"] is not None else "kształt nieokreślony"
        out["shape"] = crit("ok" if ok else "fail", sm, txt)
    if "admin" in c:
        a = c["admin"]
        checks = [(a.get("voivodeship"), p.voivodeship), (a.get("county"), p.county), (a.get("commune"), p.commune)]
        ok = all(not want or (have or "") in want for want, have in checks)
        out["admin"] = crit("ok" if ok else "fail", {"voivodeship": p.voivodeship, "county": p.county, "commune": p.commune},
                            f"gm. {p.commune}, {p.county}")
    return out


def overall(row_crit: Dict[str, Dict[str, Any]], cfg: Dict[str, Dict[str, Any]]) -> Tuple[str, float, List[str], List[str]]:
    """(status: spelnia | do_sprawdzenia | nie | w_toku, wynik 0–100, zawiodły, brak danych)."""
    failed, unknown, pending = [], [], False
    pts = tot = 0.0
    for k, c in cfg.items():
        r = row_crit.get(k)
        req = bool(c.get("required"))
        w = 2.0 if req else 1.0
        if r is None or r["status"] in ("pending",):
            pending = True
            continue
        if r["status"] == "skipped":
            continue
        tot += w
        if r["status"] == "ok":
            pts += w
        elif r["status"] == "fail":
            if req:
                failed.append(LABELS[k])
        elif r["status"] == "brak":
            unknown.append(LABELS[k])
    req_unknown = [LABELS[k] for k, c in cfg.items() if c.get("required") and (row_crit.get(k) or {}).get("status") == "brak"]
    if failed:
        st = "nie"
    elif pending:
        st = "w_toku"
    elif req_unknown:
        st = "do_sprawdzenia"
    else:
        st = "spelnia"
    return st, round(100.0 * pts / tot, 0) if tot else 0.0, failed, unknown


# --------------------------------------------------------------------------------------
# Zadanie (job)
# --------------------------------------------------------------------------------------
class Job:
    def __init__(self, pool: List[Dict[str, Any]], cfg: Dict[str, Dict[str, Any]], area: Optional[Dict[str, Any]] = None):
        self.id = uuid.uuid4().hex
        self.area = area          # tryb „wszystkie działki w obszarze (EGiB)”: {level, code, name}
        self.too_big: Optional[Dict[str, Any]] = None
        self.egib_stats: Optional[Dict[str, int]] = None
        self.preset: Dict[int, ParcelData] = {}
        self.created = time.time()
        self.pool = pool
        self.cfg = cfg
        self.lock = threading.Lock()
        self.state = "running"
        self.stage = "Start"
        self.message = "Przygotowanie wyszukiwania…"
        self.done = 0
        self.total = len(pool)
        self.rows: List[Dict[str, Any]] = []
        self.provenance: Dict[str, Dict[str, Any]] = {}
        self.groups: List[Dict[str, Any]] = []
        self.zones: Dict[str, Any] = {}
        self.warnings: List[str] = []
        self.timings: Dict[str, float] = {}
        self.stage_progress: Dict[str, Tuple[int, int]] = {}
        self.stage_messages: Dict[str, str] = {}
        self.stage_started: Dict[str, float] = {}
        self.found: Optional[int] = None   # tryb EGiB: działek znalezionych dotąd (do opisu postępu)
        self.final_count = 0                 # działek gotowych (wiersze 0..final_count-1 – partie kończą się po kolei)
        self.total_expected: Optional[int] = None
        self.finishing = False
        self.error: Optional[str] = None
        self.parcels: Dict[int, ParcelData] = {}

    def progress(self, stage: str, message: str, done: Optional[int] = None, total: Optional[int] = None) -> None:
        """Postęp etapu; etapy mogą biec równolegle – done/total to suma po etapach, komunikat = ostatni z etapów."""
        with self.lock:
            self.stage, self.message = stage, message
            d, t = self.stage_progress.get(stage, (0, 0))
            self.stage_progress[stage] = (d if done is None else done, t if total is None else total)
            self.done = sum(x for x, _ in self.stage_progress.values())
            self.total = sum(y for _, y in self.stage_progress.values())
            msgs = self.stage_messages
            msgs[stage] = message
            self.stage_started.setdefault(stage, time.monotonic())

    def step_info(self) -> Dict[str, Any]:
        """Postęp „po ludzku”: krok k z N, nazwa, procent całości (rośnie przez wszystkie kroki), czas do końca pobierania
        z ewidencji (tylko tam tempo jest równe i szacunek rzetelny), liczba działek znalezionych i spełniających dotąd."""
        with self.lock:
            prog = dict(self.stage_progress)
            started = dict(self.stage_started)
        if self.area:
            steps = ["Wyznaczam obszar", "Pobieram i sprawdzam działki", "Kończę"]
        else:
            steps = ["Sprawdzam działki", "Kończę"]
        if self.state != "running":
            return {"n": len(steps), "of": len(steps), "label": "Gotowe" if self.state == "done" else "Przerwano", "pct": 100,
                    "eta_s": None, "found": self.found}

        def frac_of(names: Sequence[str]) -> float:
            d = sum(prog[n][0] for n in names if n in prog)
            t = sum(prog[n][1] for n in names if n in prog)
            return min(1.0, d / t) if t else 0.0

        eta = None
        if self.finishing:
            cur, frac = len(steps) - 1, 0.5
        elif self.area and "EGiB" in prog:
            cur, frac = 1, frac_of(["EGiB"])
            if "EGiB" in started and 0.15 <= frac < 1.0:
                el = time.monotonic() - started["EGiB"]
                eta = int(round(el * (1 - frac) / frac / 5.0) * 5) or 5
        elif self.area:
            cur, frac = 0, frac_of(["Obszar", "Obszar-miasta"])
        else:
            cur, frac = 0, (self.final_count / self.total_expected) if self.total_expected else 0.0
        pct = int(round(100 * (cur + frac) / len(steps)))
        return {"n": cur + 1, "of": len(steps), "label": steps[cur], "pct": min(99, pct), "eta_s": eta, "found": self.found}

    def snapshot(self, since: Optional[int] = None, cand_since: Optional[int] = None) -> Dict[str, Any]:
        """Stan zadania. Bez `since` – wszystkie wiersze. Z `since` (przyrost podczas filtrowania) – tylko gotowe działki od
        wiersza `since` (`results_new`) i nowi kandydaci od `cand_since` (`candidates_new`: przeszli tanie kryteria, czekają na resztę);
        liczniki zawsze dla całości. Geometria tylko dla działek do pokazania na mapie."""
        with self.lock:
            rows = [dict(r, criteria=dict(r["criteria"])) for r in self.rows]
        for r in rows:
            r["status"], r["score"], r["failed"], r["unknown"] = overall(r["criteria"], self.cfg)
            if r.get("no_geometry"):
                r["status"], r["score"] = "brak_geometrii", 0.0
            elif not r.get("final", True):
                r["status"] = "w_toku"   # partia jeszcze nie skończona – nie liczymy jej jako „spełnia”, zanim nie ma wszystkich kryteriów
            if since is None and len(rows) > MAX_POOL and (self.state == "running" or r["status"] in ("nie", "brak_geometrii")):
                r["geometry"] = []
        counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("spelnia", "do_sprawdzenia", "nie", "w_toku", "brak_geometrii")}
        counts["brak_geometrii"] = sum(1 for r in rows if r.get("no_geometry"))
        counts["gotowe"] = self.final_count
        extra: Dict[str, Any] = {}
        if since is not None:
            cs = cand_since or 0
            fresh = [r for r in rows if r.get("final", True) and r["key"] >= since]
            for r in fresh:
                if r["status"] not in ("spelnia", "do_sprawdzenia", "w_toku"):
                    r["geometry"] = []
            extra = {"results_new": fresh, "cursor": self.final_count, "cand_cursor": len(rows),
                     "candidates_new": [{"key": r["key"], "geometry": r["geometry"]} for r in rows
                                        if not r.get("final", True) and r["key"] >= cs and r["status"] == "w_toku" and r["geometry"]]}
            rows = []
        return {
            **extra,
            "job_id": self.id, "state": self.state, "stage": self.stage, "message": self.message,
            "done": self.done, "total": self.total, "pool_size": self.total_expected or len(self.pool),
            "stages": {k: {"done": d, "total": t, "message": self.stage_messages.get(k, "")} for k, (d, t) in self.stage_progress.items()},
            "step": self.step_info(),
            "counts": counts, "results": rows, "groups": self.groups, "zones": self.zones,
            "criteria": {k: {"label": LABELS[k], **v} for k, v in self.cfg.items()},
            "provenance": self.provenance, "warnings": self.warnings, "timings_s": self.timings, "error": self.error,
            "source": "egib" if self.area else "mine", "area": self.area, "too_big": self.too_big, "egib_stats": self.egib_stats,
            "generated_at": iso_utc(),
        }


_JOBS: Dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def start_job(pool: List[Dict[str, Any]], cfg: Dict[str, Dict[str, Any]], area: Optional[Dict[str, Any]] = None) -> Job:
    job = Job(pool[:MAX_POOL], cfg, area)
    with _JOBS_LOCK:
        now = time.time()
        for k in [k for k, j in _JOBS.items() if now - j.created > JOB_TTL_S]:
            _JOBS.pop(k, None)
        while len(_JOBS) >= MAX_JOBS:
            _JOBS.pop(min(_JOBS, key=lambda k: _JOBS[k].created), None)
        _JOBS[job.id] = job
    _RUNNER.submit(_run_safe, job)
    return job


def get_job(job_id: str) -> Optional[Job]:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def _run_safe(job: Job) -> None:
    try:
        run(job)
        job.state = "done"
        job.progress("Gotowe", "Wyszukiwanie zakończone.")
    except Exception as exc:  # noqa: BLE001 – błąd zadania nie może zatrzymać serwera
        log.exception("Błąd wyszukiwania działek")
        job.error = "Wyszukiwanie przerwane przez błąd serwera."
        job.state = "error"
        job.progress("Błąd", job.error)


def _resolve(item: Dict[str, Any]) -> Optional[ParcelData]:
    if isinstance(item.get("_parcel"), ParcelData):   # działka z EGiB WFS – geometria już jest
        return item["_parcel"]
    if item.get("parcel_id"):
        p = fetch_parcel_by_id(item["parcel_id"])
        if p is not None:
            return p
    return fetch_parcel_by_xy(float(item["lon"]), float(item["lat"]))


def _set(job: Job, idx: int, key: str, value: Dict[str, Any]) -> None:
    with job.lock:
        job.rows[idx]["criteria"][key] = value


def _alive(job: Job) -> List[int]:
    """Działki z geometrią, które nie zawiodły żadnego kryterium koniecznego (do drogich etapów)."""
    out = []
    with job.lock:
        for i, r in enumerate(job.rows):
            if r.get("no_geometry"):
                continue
            if any(job.cfg[k].get("required") and v["status"] == "fail" for k, v in r["criteria"].items() if k in job.cfg):
                continue
            out.append(i)
    return out


def _skip_dead(job: Job, keys: Sequence[str], alive: Sequence[int]) -> None:
    alive_set = set(alive)
    with job.lock:
        for i, r in enumerate(job.rows):
            if i in alive_set:
                continue
            for k in keys:
                if k in job.cfg and k not in r["criteria"]:
                    r["criteria"][k] = crit("skipped", text="pominięte – działka odrzucona wcześniej" if not r.get("no_geometry")
                                            else "brak geometrii ULDK")


BATCH = 80   # działek na partię: każda partia przechodzi pełny zestaw kryteriów i od razu trafia do wyników


def run(job: Job) -> None:
    """Filtrowanie strumieniowe: działki partiami (EGiB – w miarę pobierania kafli), każda partia: tanie kryteria → drogie
    (BDOT10k, miasta, NMT, ISOK) → NDVI z cache, po czym jest gotowa (GET ?since= zwraca ją od razu). Na końcu scalanie
    i strefy. Kryteria są liczone per działka tak samo jak przy przebiegu wsadowym (test porównawczy w testach)."""
    t0 = time.monotonic()
    log.info("Filtrowanie %s: start (%s, kryteria: %s)", job.id[:8],
             f"EGiB {job.area.get('level')} {job.area.get('code')}" if job.area else f"{len(job.pool)} działek",
             ", ".join(job.cfg))
    if job.area:
        source = _egib_batches(job)
    else:
        pool = list(job.pool)
        job.pool = []
        job.total_expected = len(pool)
        source = (pool[k:k + BATCH] for k in range(0, len(pool), BATCH))
    for items in source:
        if items:
            _process_batch(job, items)
    job.finishing = True
    t = time.monotonic()
    if "merge" in job.cfg:
        job.progress("Koniec", "Szukanie grup przylegających działek…")
        _stage_merge(job)
    _finalize_zones(job)
    job.timings["finish"] = round(time.monotonic() - t, 2)
    job.timings["total"] = round(time.monotonic() - t0, 2)
    snap = job.snapshot(since=10 ** 9)
    c = snap["counts"]
    log.info("Filtrowanie %s: koniec %.1f s; pula %d, po tanich kryteriach %d, oceniono %d, spełnia %d, do sprawdzenia %d; etapy %s",
             job.id[:8], job.timings["total"], len(job.pool) + ((job.egib_stats or {}).get("cheap_rejected") or 0),
             len(job.pool), len(job.rows), c.get("spelnia", 0), c.get("do_sprawdzenia", 0), job.timings)


def _acc(job: Job, name: str, dt: float) -> None:
    job.timings[name] = round(job.timings.get(name, 0.0) + dt, 2)


def _process_batch(job: Job, items: List[Dict[str, Any]]) -> None:
    cfg = job.cfg
    t0 = time.monotonic()
    base = len(job.pool)
    job.pool.extend(items)
    idx = list(range(base, base + len(items)))
    # 1. Geometria (ULDK; działki z EGiB mają ją już w puli)
    futs = [_IO.submit(_resolve, it) for it in items]
    new_rows = []
    for i, it, f in zip(idx, items, futs):
        try:
            p = f.result(timeout=60)
        except Exception:  # noqa: BLE001
            p = None
        row = {"key": i, "parcel_id": (p.parcel_id if p else it.get("parcel_id")) or None, "name": it.get("id_tag") or (p.parcel_id if p else "Działka"),
               "farm": it.get("farm") or "", "lat": it["lat"], "lon": it["lon"], "criteria": {},
               "area_ha": round(p.area_ha, 2) if p else None, "commune": p.commune if p else None, "county": p.county if p else None,
               "voivodeship": p.voivodeship if p else None, "geometry": p.exterior_coords_lonlat() if p else [],
               "no_geometry": p is None, "pole_ewid_ha": it.get("pole_ewid_ha"), "final": False}
        if p is None:
            row["note"] = "brak geometrii ULDK"
        else:
            job.parcels[i] = p
        new_rows.append(row)
    with job.lock:
        job.rows.extend(new_rows)
    if "parcel" not in job.provenance:
        job.provenance["parcel"] = (egib.egib_provenance(job.provenance.get("_egib_fetched")) if job.area
                                    else parcel_provenance(next(iter(job.parcels.values()), None)))
    _acc(job, "uldk", time.monotonic() - t0)
    if not job.area:
        job.progress("ULDK", f"Granice działek z ULDK ({base + len(items)} z {job.total_expected or len(job.pool)})", base + len(items),
                     job.total_expected or len(job.pool))

    # 2. Tanie kryteria
    t = time.monotonic()
    mine = [i for i in idx if i in job.parcels]
    for i in mine:
        for k, v in evaluate_cheap(job.parcels[i], {k: cfg[k] for k in CHEAP if k in cfg}).items():
            _set(job, i, k, v)
    for k in CHEAP:
        if k in cfg and k not in job.provenance:
            job.provenance[k] = provenance("GUGiK ULDK – granice działek", "https://uldk.gugik.gov.pl/", "pomiar",
                                           note={"area": "Powierzchnia geometryczna z granic działki w EPSG:2180 (może różnić się od ewidencyjnej).",
                                                 "shape": "Zwartość Polsby-Popper 4πA/P², prostokątność A/pole min. obróconego prostokąta, "
                                                          "szerokość min. ≈ 2× największy bufor ujemny (z granic działki, EPSG:2180).",
                                                 "admin": "Województwo, powiat i gmina z ULDK."}[k])
    _skip_dead(job, ["admin", "area", "shape"], mine)
    _acc(job, "cheap", time.monotonic() - t)

    # 3. Drogie etapy dla działek partii, które przeszły tanie kryteria
    in_batch = set(idx)
    alive = [i for i in _alive(job) if i in in_batch]
    bdot_keys = [k for k in BDOT_CRIT if k in cfg]

    def timed(name: str, fn: Callable[[], None]) -> None:
        t = time.monotonic()
        log.debug("Filtrowanie %s: etap %s start (%d działek)", job.id[:8], name, len(alive))
        try:
            fn()
        finally:
            _acc(job, name, time.monotonic() - t)
            log.debug("Filtrowanie %s: etap %s koniec (%.1f s)", job.id[:8], name, time.monotonic() - t)

    def run_stages(st: List[Tuple[str, Callable[[], None]]]) -> None:
        if st:
            with ThreadPoolExecutor(max_workers=len(st), thread_name_prefix="mrolnik-stage") as ex:
                for f in [ex.submit(timed, n, fn) for n, fn in st]:
                    f.result()

    stages: List[Tuple[str, Callable[[], None]]] = []
    if alive and bdot_keys:
        stages.append(("bdot10k", lambda: _stage_bdot(job, alive, bdot_keys)))
    if alive and "town" in cfg:
        stages.append(("town", lambda: _stage_town(job, alive)))
    t = time.monotonic()
    if not job.area:
        # Moje działki (mała pula): wszystko naraz (NMT i ISOK też)
        if alive and "slope" in cfg:
            stages.append(("slope", lambda: _stage_parallel(job, alive, "slope", "NMT", "Liczenie nachylenia z NMT", _slope_one)))
        if alive and "flood" in cfg:
            stages.append(("flood", lambda: _stage_flood(job, alive)))
        run_stages(stages)
    else:
        # EGiB (duża pula): NMT i ISOK tylko dla działek, które przeszły BDOT10k i miasta
        run_stages(stages)
        rest = [i for i in _alive(job) if i in in_batch]
        st2: List[Tuple[str, Callable[[], None]]] = []
        if rest and "slope" in cfg:
            st2.append(("slope", lambda: _stage_parallel(job, rest, "slope", "NMT", "Liczenie nachylenia z NMT", _slope_one)))
        if rest and "flood" in cfg:
            st2.append(("flood", lambda: _stage_flood(job, rest)))
        run_stages(st2)
    _acc(job, "expensive_wall", time.monotonic() - t)
    _skip_dead(job, bdot_keys + ["town", "slope", "flood"], _alive(job))

    # 4. NDVI z cache
    alive = [i for i in _alive(job) if i in in_batch]
    if "ndvi" in cfg:
        for i in alive:
            _set(job, i, "ndvi", _ndvi_one(job, i))
        job.provenance.setdefault("ndvi", provenance(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, "pomiar",
                                                     note="Średnie NDVI z ostatniej analizy działki (Sentinel-2, data zdjęcia przy wartości); "
                                                          "tylko z pamięci podręcznej – bez nowych zapytań do CDSE."))
    _skip_dead(job, ["ndvi"], _alive(job))
    with job.lock:
        for r in new_rows:
            r["final"] = True
        job.final_count += len(new_rows)
    log.info("Filtrowanie %s: partia %d–%d gotowa (%.1f s)", job.id[:8], base + 1, base + len(items), time.monotonic() - t0)


def _finalize_zones(job: Job) -> None:
    """Strefy do przełącznika „Pokaż strefy kryteriów” – złożone z partii."""
    parts = getattr(job, "water_parts", None)
    if parts:
        try:
            job.zones["water"] = {"label": job.water_label, "geojson": _geojson_wgs(shapely.union_all(parts), 5.0)}
        except (ValueError, shapely.errors.ShapelyError) as exc:
            log.warning("Strefa wód: %s", exc)
    towns = getattr(job, "town_zone_items", None)
    if towns:
        job.zones["town"] = {"label": job.town_zone_label, "items": list(towns.values())[:30]}


# --------------------------------------------------------------------------------------
# Tryb „wszystkie działki w obszarze” (EGiB WFS)
# --------------------------------------------------------------------------------------
MAX_EGIB = 2000          # działek do oceny drogimi kryteriami na przebieg (po tanich filtrach: powierzchnia, kształt)
MAX_EGIB_RAW = 20000     # działek pobieranych z EGiB WFS na przebieg (ok. jedna gmina; ponad to – komunikat „zawęź obszar”)
AREA_LABEL = {"gmina": "gmina", "powiat": "powiat", "woj": "województwo"}


def narrowing_zones(job: Job, boundary: BaseGeometry) -> Tuple[BaseGeometry, List[str]]:
    """Obszar do pobrania działek = granica jednostki ∩ strefy włączonych kryteriów „≤” (miasto, wody).

    Kryteriów „≥” (np. daleko od zabudowy) i powodzi nie używamy do zawężania – nie wycinamy działek, które mogłyby
    spełnić kryterium; te liczą się później na pobranych działkach. Zwraca (obszar, opisy zastosowanych stref).
    """
    cfg = job.cfg
    area = boundary
    used: List[str] = []
    if "town" in cfg:
        c = cfg["town"]
        max_km = float(c.get("max_km") or 10)
        min_pop = c.get("min_pop")
        job.progress("Obszar", "Zawężanie obszaru: miasta w pobliżu (PRNG, BDOT10k)…", 1, 4)
        try:
            cand, _ = _town_candidates(job, [boundary], max_km, stage="Obszar-miasta")
        except SourceError as exc:
            job.warnings.append(f"Nie zawężono obszaru po odległości od miasta (PRNG: {friendly(exc)}).")
            cand = None
        if cand is not None:
            # miasto z nieznaną liczbą mieszkańców zostaje w strefie (żeby nie wyciąć działek błędnie)
            keep = [tw for tw in cand if not min_pop or tw["pop"] is None or tw["pop"] >= min_pop]
            zone = shapely.union_all([(tw["poly"] if tw["poly"] is not None else tw["pt"]).buffer(max_km * 1000, quad_segs=16)
                                      for tw in keep]) if keep else shapely.Polygon()
            area = area.intersection(zone)
            used.append(f"≤ {_f(max_km)} km od miasta" + (f" (≥ {_pop(min_pop)} mieszk.)" if min_pop else ""))
    if "water" in cfg and (cfg["water"].get("op") or "le") == "le" and not area.is_empty:
        c = cfg["water"]
        d = float(c.get("dist_m") or 0)
        job.progress("Obszar", "Zawężanie obszaru: bufor cieków i wód (BDOT10k)…", 2, 4)
        ring = area.buffer(d, quad_segs=2)
        pts = [ring.representative_point()]
        for part in getattr(ring, "geoms", [ring]):
            ext = part.exterior
            pts += [ext.interpolate(k / 24, normalized=True) for k in range(24)]
        teryts: List[str] = []
        for pt in pts:
            lon, lat = transformer_to_wgs84.transform(pt.x, pt.y)
            try:
                ci = _county_at(lat, lon)
            except SourceError:
                continue
            if ci and ci["teryt"] not in teryts:
                teryts.append(ci["teryt"])
        classes = sorted({cls for k in (c.get("kinds") or list(WATER_KINDS)) for cls, _ in WATER_KINDS.get(k, [])})
        feats: List[BaseGeometry] = []
        failed = []
        for t in teryts:
            try:
                lays = bdot10k.ensure_layers(t, classes, progress=lambda m: job.progress("Obszar", m))
            except SourceError as exc:
                failed.append(f"{t}: {friendly(exc)}")
                continue
            for kind in c.get("kinds") or list(WATER_KINDS):
                for cls, pred in WATER_KINDS.get(kind, []):
                    feats += [g for g, _ in bdot10k.within([lays[cls]], area, d, pred)]
        if failed:
            job.warnings.append("Nie zawężono obszaru po wodach – BDOT10k niedostępny: " + "; ".join(failed[:3]))
        else:
            zone = shapely.union_all(shapely.buffer(feats, d, quad_segs=4)) if feats else shapely.Polygon()
            area = area.intersection(zone)
            used.append(f"≤ {d:.0f} m od wód")
    return area, used


def in_unit(parcel_id: str, level: str, code: str) -> bool:
    """Czy działka (TERYT WWPPGG_R.OOOO.NR) leży w jednostce; gmina miejsko-wiejska (_3) obejmuje miasto (_4) i obszar wiejski (_5)."""
    if level == "woj":
        return parcel_id[:2] == code
    if level == "powiat":
        return parcel_id[:4] == code
    if parcel_id[:6] != code[:6] or len(parcel_id) < 8:
        return False
    return parcel_id[7] in "345" if code[7] == "3" else parcel_id[7] == code[7]


def _cheap_pass(g: BaseGeometry, cfg: Dict[str, Dict[str, Any]]) -> bool:
    """Wstępny filtr działek EGiB: te same progi powierzchni i kształtu co evaluate_cheap (szerokość tylko, gdy jest próg)."""
    if "area" in cfg:
        ha = g.area / 10_000
        lo, hi = cfg["area"].get("min_ha"), cfg["area"].get("max_ha")
        if (lo is not None and ha < lo) or (hi is not None and ha > hi):
            return False
    if "shape" in cfg:
        c = cfg["shape"]
        preset = SHAPE_PRESETS.get(c.get("preset") or "any")
        if preset:
            m = shape_metrics(g, with_width=False)
            k, thr = preset
            if m[k] is None or m[k] < thr:
                return False
        if c.get("min_width_m") and min_width(g) < c["min_width_m"]:
            return False
    return True


def _egib_batches(job: Job):
    """Generator partii działek z EGiB: granica → zawężenie → kafle (pobierane w tle, przetwarzane w miarę napływu)."""
    from concurrent.futures import as_completed

    a = job.area
    level, code = a["level"], a["code"]
    label = f"{AREA_LABEL[level]} {a.get('name') or code}"
    job.progress("Obszar", f"Granica: {label} (ULDK)…", 0, 4)
    try:
        boundary = egib.admin_boundary(level, code)
    except SourceError as exc:
        job.error = f"Nie pobrano granicy jednostki ({label}) z ULDK: {friendly(exc)}."
        return
    t = time.monotonic()
    area, used = narrowing_zones(job, boundary)
    _acc(job, "area", time.monotonic() - t)
    job.progress("Obszar", "Obszar wyznaczony.", 4, 4)
    job.zones["area"] = {"label": f"obszar pobrania działek: {label}" + (f", {', '.join(used)}" if used else ""),
                         "geojson": _geojson_wgs(area, 25.0) if not area.is_empty else None}
    if area.is_empty:
        job.warnings.append(f"W obszarze {label} nie ma miejsc spełniających kryteria zawężające ({', '.join(used)}).")
        return
    tiles = egib.tiles_for_area(area)
    t_egib = time.monotonic()
    est = 0.0
    raw_est = 0
    if level != "gmina":
        # powiat / województwo: szacunek przed pobraniem geometrii (suma hits w kaflach – to samo liczy limit pobierania)
        job.progress("EGiB", f"Szacowanie liczby działek EGiB: kafel 0 z {len(tiles)}", 0, 2 * len(tiles))
        done = 0
        lock = threading.Lock()

        def one_hits(tl: Tuple[int, int]) -> None:
            nonlocal est, raw_est, done
            try:
                n = egib.hits(*tl)
                frac = area.intersection(egib.tile_box(*tl)).area / egib.tile_box(*tl).area
            except SourceError:
                n, frac = 0, 0.0
            with lock:
                est += n * frac
                raw_est += n
                done += 1
                job.progress("EGiB", f"Szacowanie liczby działek EGiB: kafel {done} z {len(tiles)}", done)

        with ThreadPoolExecutor(max_workers=egib.CONCURRENCY, thread_name_prefix="mrolnik-egibh") as ex:
            list(ex.map(one_hits, tiles))
        if raw_est > MAX_EGIB_RAW:   # szybkie odrzucenie: zanim pobierzemy geometrię
            job.too_big = {"estimate": int(round(est, -2)), "to_fetch": raw_est, "limit": MAX_EGIB, "limit_raw": MAX_EGIB_RAW, "area": label,
                           "narrowing": used, "early": True,
                           "message": f"Obszar ({label}{', ' + ', '.join(used) if used else ''}) ma ok. {int(round(est, -2))} działek, a do pobrania "
                                      f"z ewidencji byłoby ok. {int(round(raw_est, -2))} (kafle obejmują też okolicę) – limit to {MAX_EGIB_RAW} "
                                      "na przebieg. Zawęź obszar (gmina zamiast powiatu, mniejsza odległość od miasta lub od cieków)."}
            return
        offset, total = len(tiles), 2 * len(tiles)
    else:
        offset, total = 0, len(tiles)   # gmina: bez osobnego szacunku – limit pobierania pilnuje przebiegu
    shapely.prepare(area)
    seen: set = set()
    stats = {"fetched": 0, "in_area": 0, "cheap_rejected": 0}
    job.egib_stats = stats
    cheap_cfg = {k: job.cfg[k] for k in ("area", "shape") if k in job.cfg}
    accepted = 0
    failed_tiles: List[Tuple[int, int]] = []
    fetched: List[str] = []
    pending: List[Dict[str, Any]] = []
    stopped = False
    job.progress("EGiB", f"Pobieranie działek EGiB: kafel 0 z {len(tiles)}", offset, total)
    ex = ThreadPoolExecutor(max_workers=egib.CONCURRENCY, thread_name_prefix="mrolnik-egib")
    try:
        futs = {ex.submit(egib.fetch_tile, *tl): tl for tl in tiles}
        for n_done, fut in enumerate(as_completed(futs), start=1):
            try:
                doc = fut.result()
            except SourceError:
                failed_tiles.append(futs[fut])
                doc = None
            if doc:
                fetched.append(doc.get("fetched_at") or "")
                job.provenance["_egib_fetched"] = min((f for f in fetched if f), default=None)
                for r in doc["rows"]:
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                    g = shapely.from_wkb(bytes.fromhex(r["wkb"]))
                    if not in_unit(r["id"], level, code) or not area.intersects(g) or area.touches(g):
                        continue   # działka innej jednostki albo tylko styka się z obszarem
                    stats["in_area"] += 1
                    if not _cheap_pass(g, cheap_cfg):
                        stats["cheap_rejected"] += 1
                        continue
                    try:
                        pcl = _build_parcel(r["id"], g.wkt, a.get("woj_name") or "", a.get("powiat_name") or "",
                                            r.get("gmina") or "", r.get("obreb") or "")
                    except (ValueError, shapely.errors.ShapelyError):
                        continue
                    accepted += 1
                    pending.append({"lat": pcl.centroid_lat, "lon": pcl.centroid_lon, "parcel_id": r["id"],
                                    "id_tag": f"Dz. {r.get('nr') or r['id'].rsplit('.', 1)[-1]}",
                                    "farm": f"EGiB · obręb {r.get('obreb') or '?'}", "_parcel": pcl, "pole_ewid_ha": r.get("pole_ha")})
                stats["fetched"] = len(seen)
                job.found = accepted
            job.progress("EGiB", f"Pobieranie działek EGiB: kafel {n_done} z {len(tiles)} ({accepted} działek)", offset + n_done)
            if accepted > MAX_EGIB or len(seen) > MAX_EGIB_RAW:
                stopped = True
                for f in futs:
                    f.cancel()
                break
            if len(pending) >= BATCH:
                batch, pending = pending, []
                yield batch          # partia idzie do oceny; kolejne kafle pobierają się w tym czasie w tle
        _acc(job, "egib", time.monotonic() - t_egib)
        if stopped:
            shown = len(job.pool)
            if accepted > MAX_EGIB:
                msg = (f"W obszarze ({label}) ponad {MAX_EGIB} działek spełnia już powierzchnię i kształt (pobrano {len(seen)} działek EGiB). "
                       f"Pobieranie przerwano – wyniki są niepełne (sprawdzono {shown} działek). Zawęź obszar albo kryteria.")
            else:
                msg = (f"Obszar ({label}) ma ponad {MAX_EGIB_RAW} działek EGiB. Pobieranie przerwano – wyniki są niepełne "
                       f"(sprawdzono {shown} działek). Zawęź obszar.")
            job.too_big = {"estimate": int(round(max(est, len(seen)), -2)), "limit": MAX_EGIB, "limit_raw": MAX_EGIB_RAW, "area": label,
                           "narrowing": used, "partial": True, "message": msg}
            return
        if pending:
            yield pending
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    if stats["cheap_rejected"]:
        job.warnings.append(f"Z {stats['in_area']} działek EGiB w zawężonym obszarze {stats['cheap_rejected']} nie spełnia powierzchni "
                            f"lub kształtu – nie są pokazywane w wynikach (do dalszej oceny: {accepted}).")
    if failed_tiles:
        bad: List[str] = []
        for tl in failed_tiles[:6]:
            c = egib.tile_box(*tl).centroid
            lon, lat = transformer_to_wgs84.transform(c.x, c.y)
            try:
                ci = _county_at(lat, lon)
                nm = f"{ci['name']} ({ci['teryt']})" if ci else "?"
            except SourceError:
                nm = "?"
            if nm not in bad:
                bad.append(nm)
        job.warnings.append(f"Brak danych EGiB dla powiatu {', '.join(bad)} ({len(failed_tiles)} z {len(tiles)} kafli) – "
                            "usługa WFS starostwa nie odpowiedziała; działki z tego obszaru nie zostały sprawdzone.")
    if not accepted and not failed_tiles:
        job.warnings.append(f"W zawężonym obszarze ({label}) nie ma działek ewidencyjnych.")


def _stage_parallel(job: Job, idxs: List[int], key: str, stage: str, msg: str, fn: Callable[[Job, int], Dict[str, Any]],
                    workers: int = 4) -> None:
    job.progress(stage, f"{msg} (0 z {len(idxs)})…", 0, len(idxs))
    done = 0
    lock = threading.Lock()

    def one(i: int) -> None:
        nonlocal done
        try:
            r = fn(job, i)
        except Exception as exc:  # noqa: BLE001
            log.exception("Kryterium %s", key)
            r = crit("brak", text="brak danych", note=f"błąd obliczeń ({type(exc).__name__})")
        _set(job, i, key, r)
        with lock:
            done += 1
            job.progress(stage, f"{msg} ({done} z {len(idxs)})…", done)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"mrolnik-{key}") as ex:
        list(ex.map(one, idxs))


# --- BDOT10k ---------------------------------------------------------------------------
_ULDK_SEM = threading.BoundedSemaphore(3)   # ULDK zrywa połączenia przy wielu równoległych zapytaniach


def _county_at(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """county.county_at (cache 90 dni) z limitem 3 równoległych zapytań i jednym ponowieniem po przerwie."""
    from src.data import county as county_mod
    for wait in (0.0, 3.0):
        if wait:
            time.sleep(wait)
        with _ULDK_SEM:
            try:
                return county_mod.county_at(lat, lon)
            except SourceError:
                pass
    raise SourceError("ULDK: nie ustalono powiatu (serwis nie odpowiedział)")


_COUNTY_GEOM: Dict[str, Optional[BaseGeometry]] = {}
_COUNTY_GEOM_LOCK = threading.Lock()


def _county_geom(teryt: str) -> Optional[BaseGeometry]:
    """Granica powiatu (EPSG:2180, ULDK GetCountyById, cache 90 dni) – przygotowana do szybkich testów „zawiera”."""
    with _COUNTY_GEOM_LOCK:
        if teryt in _COUNTY_GEOM:
            return _COUNTY_GEOM[teryt]
    try:
        g = egib.admin_boundary("powiat", teryt)
        shapely.prepare(g)
    except SourceError:
        g = None   # bez granicy: sprawdzamy sąsiednie powiaty po punktach (jak dotąd)
    with _COUNTY_GEOM_LOCK:
        _COUNTY_GEOM[teryt] = g
    return g


def _counties_for(p: ParcelData, radius_m: float) -> List[str]:
    """Powiaty przecinające działkę z buforem `radius_m` (ULDK GetCountyByXY w środku i na obwodzie bufora)."""
    from src.data import county as county_mod

    pts = [(p.centroid_lat, p.centroid_lon)]
    if radius_m > 0:
        buf = p.polygon_2180.buffer(radius_m, quad_segs=2)
        n = 12
        for part in getattr(buf, "geoms", [buf]):   # bufor bywa MultiPolygon – próbkujemy obwód każdej części
            for k in range(n):
                pt = part.exterior.interpolate(k / n, normalized=True)
                lon, lat = transformer_to_wgs84.transform(pt.x, pt.y)
                pts.append((lat, lon))
    out: List[str] = []
    geoms: List[BaseGeometry] = []
    own = p.parcel_id[:4] if re.match(r"^\d{6}_\d", p.parcel_id or "") else None
    if own:
        out.append(own)   # powiat działki z identyfikatora TERYT – bez zapytania do ULDK
        pts = pts[1:]
        cb = _county_geom(own)
        if cb is not None and (radius_m <= 0 or cb.contains(p.polygon_2180.buffer(radius_m, quad_segs=2))):
            return out    # działka z buforem w całości w swoim powiecie – sąsiednie powiaty niepotrzebne
    for lat, lon in pts:
        if any(g.covers(Point(lon, lat)) for g in geoms):
            continue
        try:
            c = _county_at(lat, lon)
        except SourceError:
            continue
        if not c:
            continue
        if c["teryt"] not in out:
            out.append(c["teryt"])
            try:
                geoms.append(shapely.wkt.loads(c["wkt"]))
            except (ValueError, shapely.errors.ShapelyError):
                pass
    return out


def _classes_for(cfg: Dict[str, Dict[str, Any]], key: str) -> List[str]:
    c = cfg[key]
    if key == "water":
        kinds = c.get("kinds") or list(WATER_KINDS)
        return list(dict.fromkeys(cls for k in kinds for cls, _ in WATER_KINDS.get(k, [])))
    return {"road": ["SKJZ_L", "SKDR_L"], "power": ["SULN_L"], "rail": ["SKTR_L"], "buildings": ["BUBD_A"], "forest": ["PTLZ_A"]}[key]


def _stage_bdot(job: Job, alive: List[int], keys: List[str]) -> None:
    cfg = job.cfg
    classes = sorted({cls for k in keys for cls in _classes_for(cfg, k)})
    rmax = max([radius_for(k, cfg[k]) for k in keys] + [0.0])
    job.progress("Powiaty", f"Ustalanie powiatów dla {len(alive)} działek (ULDK)…", 0, len(alive))
    per_parcel: Dict[int, List[str]] = {}
    futs = {i: _IO.submit(_counties_for, job.parcels[i], rmax) for i in alive}
    for n, (i, f) in enumerate(futs.items()):
        try:
            per_parcel[i] = f.result(timeout=120)
        except Exception:  # noqa: BLE001
            per_parcel[i] = []
        job.progress("Powiaty", f"Ustalanie powiatów ({n + 1} z {len(alive)})…", n + 1)
    teryts = sorted({t for v in per_parcel.values() for t in v})
    layers: Dict[str, Dict[str, Layer]] = {}
    errors: Dict[str, str] = {}
    if classes and teryts:
        job.progress("BDOT10k", f"Wczytywanie BDOT10k dla {len(teryts)} powiatów…", 0, len(teryts))
        done = 0

        def load(t: str) -> None:
            nonlocal done
            try:
                layers[t] = bdot10k.ensure_layers(t, classes, progress=lambda m: job.progress("BDOT10k", m))
            except SourceError as exc:
                errors[t] = friendly(exc)
            done += 1
            job.progress("BDOT10k", f"BDOT10k: wczytano {done} z {len(teryts)} powiatów", done)

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="mrolnik-bdot") as ex:
            list(ex.map(load, teryts))
    for t, e in errors.items():
        job.warnings.append(f"BDOT10k dla powiatu {t} niedostępny ({e}) – kryteria z tego powiatu oznaczono „brak danych”.")
    all_layers = [l for d in layers.values() for l in d.values()]
    for k in keys:
        job.provenance[k] = bdot10k.layer_provenance([l for l in all_layers if l.cls in _classes_for(cfg, k)])
    job.progress("BDOT10k", f"Liczenie odległości dla {len(alive)} działek…", 0, len(alive))
    for n, i in enumerate(alive):
        p = job.parcels[i]
        ters = per_parcel.get(i) or []
        missing_t = [t for t in ters if t not in layers] or ([] if ters else ["?"])
        for k in keys:
            if missing_t:
                _set(job, i, k, crit("brak", text="brak danych", note="BDOT10k niedostępny dla powiatu działki" if ters else
                                     "nie ustalono powiatu działki (ULDK)"))
                continue
            try:
                _set(job, i, k, _bdot_one(cfg, k, p.polygon_2180, [layers[t] for t in ters]))
            except (ValueError, shapely.errors.ShapelyError) as exc:
                _set(job, i, k, crit("brak", text="brak danych", note=f"błąd geometrii ({exc})"))
        job.progress("BDOT10k", f"Liczenie odległości ({n + 1} z {len(alive)})…", n + 1)
    if "water" in keys:
        _water_zones(job, alive, per_parcel, layers)


def _bdot_one(cfg: Dict[str, Dict[str, Any]], key: str, g: BaseGeometry, lay_sets: List[Dict[str, Layer]]) -> Dict[str, Any]:
    c = cfg[key]
    radius = radius_for(key, c)
    if key == "forest":
        frac = bdot10k.covered_fraction([d["PTLZ_A"] for d in lay_sets], g)
        pct = round(100 * frac, 1)
        thr = c.get("max_pct")
        ok = thr is None or pct <= thr
        return crit("ok" if ok else "fail", pct, f"las i zadrzewienia: {_f(pct)}% działki")
    if key == "water":
        best: Optional[float] = None
        best_a = None
        for kind in c.get("kinds") or list(WATER_KINDS):
            for cls, pred in WATER_KINDS.get(kind, []):
                d, a = bdot10k.nearest([s[cls] for s in lay_sets], g, radius, pred)
                if d is not None and (best is None or d < best):
                    best, best_a = d, a
        r = _cmp_dist(best, c, radius, "najbliższa woda")
        if best_a and best_a.get("nazwa"):
            r["text"] += f" ({best_a['nazwa']})"
        return r
    if key == "road":
        cats = {ROAD_CATS[x] for x in (c.get("categories") or ["krajowa", "wojewodzka", "powiatowa", "gminna"]) if x in ROAD_CATS}
        pred = lambda a: (a.get("kategoriaZarzadzania") or "") in cats  # noqa: E731
        d1, a1 = bdot10k.nearest([s["SKJZ_L"] for s in lay_sets], g, radius, pred)
        d2, a2 = bdot10k.nearest([s["SKDR_L"] for s in lay_sets], g, radius, pred)
        cand = [(d, a) for d, a in ((d1, a1), (d2, a2)) if d is not None]
        d, a = min(cand, key=lambda x: x[0]) if cand else (None, None)
        r = _cmp_dist(d, dict(c, op="le"), radius, "droga")
        if a and a.get("kategoriaZarzadzania"):
            r["text"] += f" ({a['kategoriaZarzadzania']})"
        return r
    cls = {"power": "SULN_L", "rail": "SKTR_L", "buildings": "BUBD_A"}[key]
    pred = (lambda a: "kolej" in (a.get("rodzajPojazduSzynowego") or "kolej")) if key == "rail" else None
    d, a = bdot10k.nearest([s[cls] for s in lay_sets], g, radius, pred)
    what = {"power": "linia energetyczna", "rail": "tory kolejowe", "buildings": "najbliższy budynek"}[key]
    r = _cmp_dist(d, c, radius, what)
    if key == "power" and a and a.get("rodzaj"):
        r["text"] += f" ({a['rodzaj'].replace('linia elektroenergetyczna ', '')})"
    return r


def _geojson_wgs(g: BaseGeometry, tol_m: float) -> Optional[Dict[str, Any]]:
    if g.is_empty:
        return None
    g = shapely.simplify(g, tol_m, preserve_topology=True)
    return mapping(shapely.set_precision(reproject(g, transformer_to_wgs84), 1e-6))


def _water_zones(job: Job, alive: List[int], per_parcel: Dict[int, List[str]], layers: Dict[str, Dict[str, Layer]]) -> None:
    """Bufor wód (próg kryterium) w otoczeniu działek – do przełącznika „Pokaż strefy kryteriów”."""
    c = job.cfg["water"]
    d = float(c.get("dist_m") or 0)
    if d <= 0:
        return
    parts = []
    for i in alive:
        ters = [t for t in per_parcel.get(i) or [] if t in layers]
        g = job.parcels[i].polygon_2180
        for kind in c.get("kinds") or list(WATER_KINDS):
            for cls, pred in WATER_KINDS.get(kind, []):
                for wg, _ in bdot10k.within([layers[t][cls] for t in ters], g, d, pred):
                    parts.append(wg)
    if not parts:
        return
    try:
        zone = shapely.union_all([shapely.buffer(x, d, quad_segs=4) for x in parts[:4000]])
        env = shapely.union_all([job.parcels[i].polygon_2180.buffer(2 * d + 200) for i in alive])
        if not hasattr(job, "water_parts"):
            job.water_parts = []
        job.water_parts.append(zone.intersection(env))     # składane z partii w _finalize_zones
        job.water_label = f"strefa {'≤' if (c.get('op') or 'le') == 'le' else '<'} {d:.0f} m od wód"
    except (ValueError, shapely.errors.ShapelyError) as exc:
        log.warning("Strefa wód: %s", exc)


# --- miasta -----------------------------------------------------------------------------
def _town_candidates(job: Job, ref_geoms: List[BaseGeometry], max_km: float, stage: str = "Miasta") -> Tuple[List[Dict[str, Any]], List[Layer]]:
    """Miasta PRNG w promieniu max_km + 15 km od geometrii (EPSG:2180) z granicą i liczbą mieszkańców z BDOT10k ADMS."""
    env = shapely.union_all([shapely.box(*g.bounds) for g in ref_geoms]).buffer((max_km + 15) * 1000, quad_segs=1)
    w, s_, e, n = reproject(shapely.box(*env.bounds), transformer_to_wgs84).bounds
    towns = places_prng.towns_in_bbox((w, s_, e, n))   # SourceError → wywołujący
    cand = []
    for tw in towns:
        x, y = transformer_to_2180.transform(tw["lon"], tw["lat"])
        tp = Point(x, y)
        if any(g.distance(tp) <= (max_km + 15) * 1000 for g in ref_geoms):
            cand.append(dict(tw, pt=tp))
    job.progress(stage, f"Granice i liczba mieszkańców {len(cand)} miast (BDOT10k ADMS)…", 0, len(cand))
    adms_layers: List[Layer] = []
    adms_err: List[str] = []

    def county_of(tw: Dict[str, Any]) -> Optional[str]:
        try:
            ci = _county_at(tw["lat"], tw["lon"])
        except SourceError as exc:
            adms_err.append(f"{tw['name']}: {friendly(exc)}")
            return None
        return ci["teryt"] if ci else None

    teryt_of = list(_IO.map(county_of, cand))
    by_teryt: Dict[str, Dict[str, Layer]] = {}

    def load(t: str) -> None:
        try:
            by_teryt[t] = bdot10k.ensure_layers(t, ["ADMS_A", "ADMS_P"], progress=lambda m: job.progress(stage, m))
        except SourceError as exc:
            adms_err.append(f"powiat {t}: {friendly(exc)}")

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="mrolnik-adms") as ex:
        list(ex.map(load, sorted({t for t in teryt_of if t})))
    for k, (tw, t) in enumerate(zip(cand, teryt_of)):
        tw["poly"], tw["pop"], tw["from_center"] = None, None, True
        lay = by_teryt.get(t or "", {})
        for cls in ("ADMS_A", "ADMS_P"):
            L = lay.get(cls)
            if L is None:
                continue
            if L not in adms_layers:
                adms_layers.append(L)
            for g, a in zip(L.geoms, L.attrs):
                if (a.get("identyfikatorPRNG") and a.get("identyfikatorPRNG") == tw["prng_id"]) or \
                        (not a.get("identyfikatorPRNG") and a.get("nazwa") == tw["name"] and a.get("rodzaj") == "miasto"):
                    if cls == "ADMS_A" and g.geom_type in ("Polygon", "MultiPolygon"):
                        tw["poly"], tw["from_center"] = g, False
                    if tw["pop"] is None and a.get("liczbaMieszkancow"):
                        try:
                            tw["pop"] = int(a["liczbaMieszkancow"])
                        except ValueError:
                            pass
        job.progress(stage, f"Granice i liczba mieszkańców miast ({k + 1} z {len(cand)})…", k + 1)
    if adms_err:
        w_ = "Dla części miast brak granic z BDOT10k – odległość liczona od punktu centrum z PRNG: " + "; ".join(adms_err[:5])
        if w_ not in job.warnings:
            job.warnings.append(w_)
    return cand, adms_layers


def _stage_town(job: Job, alive: List[int]) -> None:
    c = job.cfg["town"]
    max_km = float(c.get("max_km") or 10)
    min_pop = c.get("min_pop")
    if not alive:
        return
    job.progress("Miasta", "Wyszukiwanie miast w pobliżu (PRNG)…")
    pts2180 = {i: job.parcels[i].polygon_2180 for i in alive}
    try:
        cand, adms_layers = _town_candidates(job, list(pts2180.values()), max_km)
    except SourceError as exc:
        for i in alive:
            _set(job, i, "town", crit("brak", text="brak danych", note=f"PRNG niedostępny ({friendly(exc)})"))
        job.provenance["town"] = missing("GUGiK PRNG", places_prng.WFS_URL, f"PRNG niedostępny ({friendly(exc)})")
        return
    for i in alive:
        g = pts2180[i]
        best = None
        unknown_near = None
        for tw in cand:
            d = g.distance(tw["poly"] if tw["poly"] is not None else tw["pt"])
            if min_pop and (tw["pop"] is None):
                if d <= max_km * 1000 and (unknown_near is None or d < unknown_near[0]):
                    unknown_near = (d, tw)
                continue
            if min_pop and tw["pop"] < min_pop:
                continue
            if best is None or d < best[0]:
                best = (d, tw)
        if best is None and unknown_near is None:
            _set(job, i, "town", crit("fail", None, f"brak miasta{' ≥ ' + _pop(min_pop) + ' mieszk.' if min_pop else ''} "
                                                   f"w promieniu {max_km + 15:.0f} km"))
            continue
        if best is None or (unknown_near and best[0] > max_km * 1000):
            d, tw = unknown_near
            _set(job, i, "town", crit("brak", round(d / 1000, 2), f"{tw['name']} {_f(d / 1000)} km (brak liczby mieszkańców)",
                                      note="liczba mieszkańców nieznana w BDOT10k"))
            continue
        d, tw = best
        km = d / 1000
        ok = km <= max_km
        txt = f"{tw['name']}: {_f(km)} km" + (" od centrum" if tw["from_center"] else " od granicy")
        if tw["pop"]:
            txt += f", {_pop(tw['pop'])} mieszk."
        _set(job, i, "town", crit("ok" if ok else "fail", round(km, 2), txt))
    note = ("Miasta (rodzaj obiektu = miasto) z PRNG; granica i liczba mieszkańców z BDOT10k (ADMS_A/ADMS_P), "
            "odległość od granicy działki do granicy miasta (EPSG:2180). Gdy granicy brak – od punktu centrum z PRNG.")
    lp = bdot10k.layer_provenance(adms_layers, note) if adms_layers else places_prng.prng_provenance()
    job.provenance["town"] = dict(lp, source=f"GUGiK PRNG + {lp['source']}" if adms_layers else lp["source"])
    zones = []
    for tw in cand:
        if tw["poly"] is not None and (not min_pop or (tw["pop"] or 0) >= min_pop):
            zg = _geojson_wgs(tw["poly"].buffer(max_km * 1000, quad_segs=8), 100.0)
            if zg:
                zones.append({"name": tw["name"], "pop": tw["pop"], "geojson": zg})
    if zones:
        if not hasattr(job, "town_zone_items"):
            job.town_zone_items = {}
        for z in zones:
            job.town_zone_items.setdefault(z["name"], z)    # składane z partii w _finalize_zones
        job.town_zone_label = f"do {_f(max_km)} km od miasta"


# --- NMT, ISOK, NDVI --------------------------------------------------------------------
def _slope_one(job: Job, i: int) -> Dict[str, Any]:
    c = job.cfg["slope"]
    r = terrain.parcel_slope(job.parcels[i])
    job.provenance.setdefault("slope", r["provenance"])
    if r["provenance"].get("kind") == "pomiar":
        job.provenance["slope"] = r["provenance"]
    v = r["value"]
    if v is None:
        return crit("brak", text="brak danych", note=r["provenance"].get("note", ""))
    ok = True
    if c.get("max_mean_pct") is not None:
        ok = v["mean_pct"] <= c["max_mean_pct"]
    if c.get("max_p90_pct") is not None:
        ok = ok and v["p90_pct"] <= c["max_p90_pct"]
    return crit("ok" if ok else "fail", v, f"spadek śr. {_f(v['mean_pct'])}%, 90% pow. ≤ {_f(v['p90_pct'])}%")


def _flood_one(job: Job, i: int) -> Dict[str, Any]:
    c = job.cfg["flood"]
    r = flood.flood_share(job.parcels[i])
    if r["provenance"].get("kind") == "pomiar" or "flood" not in job.provenance:
        job.provenance["flood"] = r["provenance"]
    v = r["value"]
    if v is None:
        return crit("brak", text="brak danych", note=r["provenance"].get("note", ""))
    zone = c.get("zone") or "q1"
    share = v[zone]
    mode = c.get("mode") or "outside"
    ok = share <= 0.0 if mode == "outside" else share <= float(c.get("max_pct") or 0)
    lab = {"q10": "Q10%", "q1": "Q1%", "q02": "Q0,2%"}[zone]
    txt = (f"poza strefą {lab}" if share <= 0 else f"{_f(share)}% w strefie {lab}")
    if zone != "q10" and v["q10"] > 0:
        txt += f" (Q10%: {_f(v['q10'])}%)"
    return crit("ok" if ok else "fail", v, txt)


def _stage_flood(job: Job, alive: List[int]) -> None:
    """Kafle ISOK potrzebne dla działek – każdy raz, po 3 naraz (uprzejmy limit); potem udziały z cache."""
    tiles = sorted({t for i in alive for t in flood.tiles_for(job.parcels[i].polygon_wgs84.bounds)})
    job.progress("ISOK", f"Strefy powodziowe ISOK: pobieranie {len(tiles)} kafli…", 0, len(tiles) + len(alive))
    done = 0
    lock = threading.Lock()

    def fetch(t: Tuple[int, int]) -> None:
        nonlocal done
        try:
            flood.tile(*t)
        except SourceError:
            pass   # brak kafla → flood_share zwróci „brak danych” dla działek z tego kafla
        with lock:
            done += 1
            job.progress("ISOK", f"Strefy powodziowe ISOK: kafel {done} z {len(tiles)}", done)

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="mrolnik-isok") as ex:
        list(ex.map(fetch, tiles))
    for n, i in enumerate(alive):
        try:
            r = _flood_one(job, i)
        except Exception as exc:  # noqa: BLE001
            log.exception("Kryterium flood")
            r = crit("brak", text="brak danych", note=f"błąd obliczeń ({type(exc).__name__})")
        _set(job, i, "flood", r)
        job.progress("ISOK", "Strefy powodziowe: udział działek", len(tiles) + n + 1)


def _ndvi_one(job: Job, i: int) -> Dict[str, Any]:
    c = job.cfg["ndvi"]
    cur = sentinel.cached_current(job.parcels[i])
    nd = (cur or {}).get("ndvi") or {}
    if nd.get("mean") is None:
        return crit("brak", text="brak danych – otwórz gospodarstwo, aby przeliczyć",
                    note="Brak NDVI w pamięci podręcznej (działka nie była ostatnio analizowana albo nie było bezchmurnego zdjęcia).")
    ok = c.get("min_mean") is None or nd["mean"] >= c["min_mean"]
    day = nd.get("acquired_on") or ""
    return crit("ok" if ok else "fail", nd["mean"], f"NDVI {_f(nd['mean'], 2)} ({day})")


# --- scalanie ----------------------------------------------------------------------------
def _stage_merge(job: Job) -> None:
    c = job.cfg["merge"]
    min_ha = float(c.get("min_ha") or 0)
    alive = _alive(job)
    job.progress("Scalanie", "Szukanie grup przylegających działek…")
    geoms = {i: job.parcels[i].polygon_2180 for i in alive}
    groups = [g for g in merge_groups(geoms, gap_m=1.0)]
    out = []
    in_group: Dict[int, Tuple[int, float, int]] = {}
    for gi, members in enumerate(sorted(groups, key=lambda m: -sum(geoms[k].area for k in m))):
        ha = sum(geoms[k].area for k in members) / 10_000
        for k in members:
            in_group[k] = (gi, ha, len(members))
        if len(members) >= 2:
            union = shapely.union_all([geoms[k].buffer(0.6) for k in members]).buffer(-0.6)
            out.append({"id": gi, "members": members, "area_ha": round(ha, 2), "meets": ha >= min_ha,
                        "names": [job.rows[k]["name"] for k in members], "geojson": _geojson_wgs(union, 1.0)})
    for i in alive:
        gi, ha, n = in_group[i]
        if n < 2:
            _set(job, i, "merge", crit("fail", round(ha, 2), "brak przylegających działek w puli"))
        else:
            ok = ha >= min_ha
            _set(job, i, "merge", crit("ok" if ok else "fail", round(ha, 2),
                                       f"grupa {n} działek: {_f(ha, 2)} ha", note=f"group:{gi}"))
    job.groups = out
    job.provenance["merge"] = provenance("GUGiK ULDK – granice działek", "https://uldk.gugik.gov.pl/", "pomiar",
                                         note="Grupy działek z puli, których granice stykają się lub są oddalone o ≤ 1 m "
                                              "(liczone spośród działek, które spełniły pozostałe kryteria konieczne).")
    _skip_dead(job, ["merge"], alive)


def admin_values(pool: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Jednostki administracyjne działek z puli (ULDK, z cache) – do kaskadowych list „województwo → powiat → gmina”.

    Kody TERYT z identyfikatora działki (WWPPGG_R…): województwo = 2 cyfry, powiat = 4, gmina = 7 znaków; nazwy z ULDK.
    """
    futs = [_IO.submit(_resolve, it) for it in pool[:MAX_POOL]]
    items: List[Dict[str, Any]] = []
    for i, f in enumerate(futs):
        try:
            p = f.result(timeout=60)
        except Exception:  # noqa: BLE001
            p = None
        if p is None or not re.match(r"^\d{6}_\d", p.parcel_id or ""):
            items.append({"i": i, "resolved": False})
            continue
        t = p.parcel_id
        items.append({"i": i, "resolved": True, "parcel_id": t, "woj": t[:2], "woj_name": p.voivodeship, "powiat": t[:4],
                      "powiat_name": p.county, "gmina": t[:8], "gmina_name": p.commune})
    return {"items": items, "resolved": sum(1 for x in items if x["resolved"]), "pool_size": len(pool)}


# --------------------------------------------------------------------------------------
# Testy źródeł
# --------------------------------------------------------------------------------------
def _probe_bdot() -> str:
    resp = http_request("GET", bdot10k.zip_url("3025"), headers={"Range": "bytes=0-0"}, timeout=10.0, retries=0)
    if resp.status_code != 206:
        raise SourceError("BDOT10k: serwer nie obsłużył zapytania częściowego")
    return "OK (paczki powiatowe GML)"


def probes() -> List[Tuple[str, Callable[[], Any]]]:
    return [(SOURCE_BDOT10K, _probe_bdot), (SOURCE_PRNG, places_prng.probe), (SOURCE_NMT, terrain.probe), (SOURCE_ISOK, flood.probe), (SOURCE_EGIB, egib.probe)]


# --------------------------------------------------------------------------------------
# Rozgrzewanie cache (w tle po starcie serwera; MROLNIK_WARMUP=0 wyłącza)
# --------------------------------------------------------------------------------------
WARM_CLASSES = ("SWRS_L", "SWKN_L", "SWRM_L", "PTWP_A", "SKJZ_L", "SKDR_L", "SKTR_L", "SULN_L", "PTLZ_A", "BUBD_A", "ADMS_A", "ADMS_P")


def warm_up(pool: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """BDOT10k dla powiatów działek z „Moich działek” – po jednym powiecie naraz (niski priorytet, nie blokuje zapytań).

    Dane są te same, co przy zwykłym filtrowaniu (to samo pobranie i cache), tylko pobrane zawczasu.
    """
    from src.data import county as county_mod
    from src.data.presets import FARM_PRESETS

    t0 = time.monotonic()
    items = pool if pool is not None else [p for ps in FARM_PRESETS.values() for p in ps]
    teryts: List[str] = []
    for it in items:
        pid = str(it.get("parcel_id") or "")
        t = pid[:4] if re.match(r"^\d{6}_", pid) else None
        if t is None:
            try:
                c = _county_at(float(it["lat"]), float(it["lon"]))
                t = c["teryt"] if c else None
            except (SourceError, KeyError, ValueError):
                t = None
        if t and t not in teryts:
            teryts.append(t)
    done, failed = 0, []
    for t in teryts:
        try:
            bdot10k.ensure_layers(t, WARM_CLASSES)
            done += 1
        except SourceError as exc:
            failed.append(f"{t}: {friendly(exc)}")
    log.info("Rozgrzano BDOT10k dla %d/%d powiatów w %.0f s", done, len(teryts), time.monotonic() - t0)
    return {"counties": done, "failed": failed, "seconds": round(time.monotonic() - t0, 1)}


def result_parcels(job: Job) -> List[ParcelData]:
    """Geometrie działek wyników do pokazania na mapie (spełniające i do sprawdzenia)."""
    snap_rows = job.snapshot()["results"]
    return [job.parcels[r["key"]] for r in snap_rows
            if r["status"] in ("spelnia", "do_sprawdzenia") and r["key"] in job.parcels]

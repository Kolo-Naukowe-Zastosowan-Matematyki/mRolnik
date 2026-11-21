"""Wyszukiwarka działek: POST /api/parcel-search → {job_id}; GET /api/parcel-search/{job_id} → postęp i wyniki.

Uwierzytelnienie dokłada server.py (include_router z require_user). Tutaj walidacja wejścia i limity.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.data import parcel_search

router = APIRouter(tags=["szukaj-dzialki"])

PARCEL_ID_PATTERN = r"^\d{6}_\d\.\d{4}(\.[0-9A-Za-z/\-]{1,30})?$"
_JOB_RE = re.compile(r"^[0-9a-f]{32}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PoolParcel(_Strict):
    lat: float = Field(ge=49.0, le=55.0)
    lon: float = Field(ge=14.0, le=24.2)
    parcel_id: Optional[str] = Field(default=None, max_length=60, pattern=PARCEL_ID_PATTERN)
    id_tag: Optional[str] = Field(default=None, max_length=120)
    farm: Optional[str] = Field(default=None, max_length=160)


class _Crit(_Strict):
    enabled: bool = True
    required: bool = True


class AreaC(_Crit):
    min_ha: Optional[float] = Field(default=None, ge=0, le=100_000)
    max_ha: Optional[float] = Field(default=None, ge=0, le=100_000)


class ShapeC(_Crit):
    preset: Literal["any", "compact", "rect"] = "any"
    min_width_m: Optional[float] = Field(default=None, ge=0, le=5000)


class DistC(_Crit):
    op: Literal["le", "ge"] = "le"
    dist_m: float = Field(default=100, ge=0, le=5000)


class WaterC(DistC):
    kinds: List[Literal["rivers", "canals", "ditches", "standing"]] = Field(default_factory=lambda: ["rivers", "canals", "ditches", "standing"],
                                                                         min_length=1, max_length=4)


class TownC(_Crit):
    max_km: float = Field(default=10, gt=0, le=50)
    min_pop: Optional[int] = Field(default=None, ge=0, le=5_000_000)


class SlopeC(_Crit):
    max_mean_pct: Optional[float] = Field(default=5, ge=0, le=100)
    max_p90_pct: Optional[float] = Field(default=None, ge=0, le=200)


class FloodC(_Crit):
    mode: Literal["outside", "max_share"] = "outside"
    zone: Literal["q10", "q1", "q02"] = "q1"
    max_pct: Optional[float] = Field(default=None, ge=0, le=100)


class RoadC(_Crit):
    dist_m: float = Field(default=20, ge=0, le=2000)
    categories: List[Literal["krajowa", "wojewodzka", "powiatowa", "gminna", "wewnetrzna"]] = Field(
        default_factory=lambda: ["krajowa", "wojewodzka", "powiatowa", "gminna"], min_length=1, max_length=5)


class ForestC(_Crit):
    max_pct: float = Field(default=10, ge=0, le=100)


class NdviC(_Crit):
    min_mean: Optional[float] = Field(default=0.4, ge=-1, le=1)


class MergeC(_Crit):
    min_ha: float = Field(default=5, gt=0, le=100_000)


class AdminC(_Crit):
    voivodeship: List[str] = Field(default_factory=list, max_length=20)
    county: List[str] = Field(default_factory=list, max_length=60)
    commune: List[str] = Field(default_factory=list, max_length=200)


class Criteria(_Strict):
    area: Optional[AreaC] = None
    shape: Optional[ShapeC] = None
    admin: Optional[AdminC] = None
    water: Optional[WaterC] = None
    town: Optional[TownC] = None
    slope: Optional[SlopeC] = None
    flood: Optional[FloodC] = None
    road: Optional[RoadC] = None
    power: Optional[DistC] = None
    rail: Optional[DistC] = None
    buildings: Optional[DistC] = None
    forest: Optional[ForestC] = None
    ndvi: Optional[NdviC] = None
    merge: Optional[MergeC] = None


class AreaSel(_Strict):
    level: Literal["gmina", "powiat", "woj"]
    code: str = Field(max_length=10)
    name: Optional[str] = Field(default=None, max_length=120)
    woj_name: Optional[str] = Field(default=None, max_length=60)
    powiat_name: Optional[str] = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _code(self) -> "AreaSel":
        pat = {"gmina": r"^\d{6}_\d$", "powiat": r"^\d{4}$", "woj": r"^\d{2}$"}[self.level]
        if not re.match(pat, self.code):
            raise ValueError("Niepoprawny kod TERYT jednostki.")
        return self


class SearchRequest(_Strict):
    source: Literal["mine", "egib"] = "mine"
    pool: List[PoolParcel] = Field(default_factory=list, max_length=parcel_search.MAX_POOL)
    area: Optional[AreaSel] = None
    criteria: Criteria

    @model_validator(mode="after")
    def _some(self) -> "SearchRequest":
        c = self.criteria
        if not any(v is not None and v.enabled for v in c.__dict__.values()):
            raise ValueError("Włącz co najmniej jedno kryterium.")
        if self.source == "mine" and not self.pool:
            raise ValueError("Brak działek w wybranym zakresie.")
        if self.source == "egib":
            if self.area is None:
                raise ValueError("Wybierz co najmniej gminę (albo powiat lub województwo z kryterium zawężającym obszar).")
            narrows = (c.town is not None and c.town.enabled) or (c.water is not None and c.water.enabled and c.water.op == "le")
            if self.area.level != "gmina" and not narrows:
                raise ValueError("Obszar jest za duży: dla powiatu lub województwa włącz „Odległość od miasta” albo "
                                 "„Odległość od cieków” (najwyżej X m) albo wybierz gminę.")
        return self


@router.post("/api/parcel-search")
def start_search(req: SearchRequest) -> Dict[str, Any]:
    cfg: Dict[str, Dict[str, Any]] = {}
    for k in parcel_search.ORDER:
        v = getattr(req.criteria, k)
        if v is not None and v.enabled:
            cfg[k] = v.model_dump(exclude={"enabled"})
    if req.source == "egib":
        job = parcel_search.start_job([], cfg, area=req.area.model_dump())
    else:
        job = parcel_search.start_job([p.model_dump() for p in req.pool], cfg)
    return {"job_id": job.id, "pool_size": len(job.pool)}


@router.get("/api/parcel-search/units")
def units(woj: Optional[str] = Query(None, pattern=r"^\d{2}$"), powiat: Optional[str] = Query(None, pattern=r"^\d{4}$")) -> Dict[str, Any]:
    from src.data import egib
    from src.data.common import SourceError, friendly
    try:
        return egib.units(woj, powiat)
    except SourceError as exc:
        raise HTTPException(status_code=503, detail=f"Nie wczytano listy jednostek (GUS BDL): {friendly(exc)}")


class PoolRequest(_Strict):
    pool: List[PoolParcel] = Field(min_length=1, max_length=parcel_search.MAX_POOL)


@router.post("/api/parcel-search/admin-values")
def admin_values(req: PoolRequest) -> Dict[str, Any]:
    return parcel_search.admin_values([p.model_dump() for p in req.pool])


@router.get("/api/parcel-search/{job_id}")
def search_status(job_id: str, since: Optional[int] = Query(None, ge=0, description="Przyrost: gotowe działki od tego wiersza"),
                  cand_since: Optional[int] = Query(None, ge=0, description="Przyrost: kandydaci od tego wiersza")) -> Dict[str, Any]:
    if not _JOB_RE.match(job_id):
        raise HTTPException(status_code=422, detail="Niepoprawny identyfikator zadania.")
    job = parcel_search.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Nie ma takiego wyszukiwania (wygasło po godzinie albo serwer został uruchomiony ponownie).")
    return job.snapshot(since=since, cand_since=cand_since)


@router.get("/api/parcel-search/{job_id}/index/clusters")
def search_index_clusters(job_id: str) -> Dict[str, Any]:
    """Skupiska działek wyników (≤ 10 × 10 km) – indeks liczony osobno dla każdego (GET …/index?cluster=ID)."""
    if not _JOB_RE.match(job_id):
        raise HTTPException(status_code=422, detail="Niepoprawny identyfikator zadania.")
    job = parcel_search.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Nie ma takiego wyszukiwania (wygasło po godzinie albo serwer został uruchomiony ponownie).")
    if job.state == "running":
        raise HTTPException(status_code=409, detail="Filtrowanie jeszcze trwa.")
    from src.data import result_index
    cl = result_index.clusters(parcel_search.result_parcels(job))
    return {"clusters": [{k: v for k, v in c.items() if k != "members"} for c in cl]}


@router.get("/api/parcel-search/{job_id}/index")
def search_index(job_id: str,
                 w: Optional[float] = Query(None, ge=14.0, le=24.2), s: Optional[float] = Query(None, ge=49.0, le=55.0),
                 e: Optional[float] = Query(None, ge=14.0, le=24.2), n: Optional[float] = Query(None, ge=49.0, le=55.0),
                 cluster: Optional[str] = Query(None, pattern=r"^-?\d{1,4}_-?\d{1,4}$")) -> Dict[str, Any]:
    """NDVI / NDMI / NDWI dla działek wyników (spełniające i do sprawdzenia) – jedno zapytanie CDSE dla wszystkich."""
    if not _JOB_RE.match(job_id):
        raise HTTPException(status_code=422, detail="Niepoprawny identyfikator zadania.")
    job = parcel_search.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Nie ma takiego wyszukiwania (wygasło po godzinie albo serwer został uruchomiony ponownie).")
    if job.state == "running":
        raise HTTPException(status_code=409, detail="Filtrowanie jeszcze trwa.")
    from src.data import result_index
    if cluster:
        return result_index.cluster_index(parcel_search.result_parcels(job), cluster)
    view = (w, s, e, n) if None not in (w, s, e, n) and w < e and s < n else None
    return result_index.results_index(parcel_search.result_parcels(job), view)

"""Zestawy działek użytkownika („Moje działki”): GET/POST /api/user-sets, PATCH/DELETE /api/user-sets/{id}."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.data import user_sets
from src.security.auth import require_user
from src.security.providers.base import User

router = APIRouter(tags=["zestawy-dzialek"])


class SetParcel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    lat: float = Field(..., ge=49.0, le=55.0)
    lon: float = Field(..., ge=14.0, le=24.2)
    parcel_id: Optional[str] = Field(None, max_length=120)
    id_tag: Optional[str] = Field(None, max_length=200)
    crop: Optional[str] = Field(None, max_length=200)
    soil: Optional[str] = Field(None, max_length=200)


class CreateSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=200)
    parcels: List[SetParcel] = Field(default_factory=list, max_length=user_sets.MAX_PARCELS_PER_SET)


class UpdateSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    add: List[SetParcel] = Field(default_factory=list, max_length=user_sets.MAX_PARCELS_PER_SET)
    remove: List[str] = Field(default_factory=list, max_length=user_sets.MAX_PARCELS_PER_SET)


def _out(sets: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"sets": sets, "limits": {"sets": user_sets.MAX_SETS, "parcels": user_sets.MAX_PARCELS_PER_SET, "name": user_sets.MAX_NAME}}


@router.get("/api/user-sets")
def get_sets(user: User = Depends(require_user)) -> Dict[str, Any]:
    return _out(user_sets.list_sets(user.id))


@router.post("/api/user-sets")
def post_set(req: CreateSet, user: User = Depends(require_user)) -> Dict[str, Any]:
    try:
        s = user_sets.create_set(user.id, req.name, [p.model_dump() for p in req.parcels])
    except user_sets.SetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"set": s}


@router.patch("/api/user-sets/{set_id}")
def patch_set(set_id: str, req: UpdateSet, user: User = Depends(require_user)) -> Dict[str, Any]:
    try:
        s = user_sets.update_set(user.id, set_id, name=req.name, add=[p.model_dump() for p in req.add], remove=req.remove)
    except KeyError:
        raise HTTPException(status_code=404, detail="Nie ma takiego zestawu.") from None
    except user_sets.SetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"set": s}


@router.delete("/api/user-sets/{set_id}")
def del_set(set_id: str, user: User = Depends(require_user)) -> Dict[str, Any]:
    try:
        user_sets.delete_set(user.id, set_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Nie ma takiego zestawu.") from None
    return {"ok": True}

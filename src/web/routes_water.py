"""Cieki i rowy z BDOT10k dla fragmentu mapy (podświetlenie po najechaniu): GET /api/water/features?bbox=…&zoom=…"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query

from src.data import water_features
from src.data.common import SourceError

router = APIRouter(tags=["cieki"])


@router.get("/api/water/features")
def water(bbox: str = Query(..., max_length=120, description="minLon,minLat,maxLon,maxLat (WGS84)"),
          zoom: int = Query(..., ge=0, le=22)) -> Dict[str, Any]:
    try:
        w, s, e, n = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=422, detail="Niepoprawny zasięg mapy (bbox).")
    if not (13.5 <= w < e <= 24.5 and 48.8 <= s < n <= 55.2):
        raise HTTPException(status_code=422, detail="Zasięg mapy poza Polską.")
    try:
        return water_features.features((w, s, e, n), zoom)
    except water_features.TooLarge as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except SourceError as exc:
        raise HTTPException(status_code=502, detail=f"BDOT10k chwilowo niedostępny: {exc}")

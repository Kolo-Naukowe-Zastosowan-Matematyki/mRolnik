"""Endpointy analizy działek, źródeł danych i HydroMelior (właściciel: strumień B – dane).

Uwierzytelnienie dokłada server.py na poziomie include_router; tutaj tylko walidacja wejścia (Pydantic v2).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from src.api_service import analyze_parcels_batch, get_farm_presets_list, sources_status, weather_models_list
from src.data import climate, county, gus, history, rules, sentinel
from src.data.cadastre import fetch_parcel_by_id, fetch_parcel_by_xy, merge_parcels
from src.data.common import SourceError, friendly, missing
from src.hydromelior.advisor import generate_alerts
from src.hydromelior.coordination import get_catchment_coordination_status
from src.hydromelior.ecoscheme import ecoscheme_info, render_html
from src.hydromelior.network import GATE_STATUSES, get_network, update_gate_status
from src.hydromelior.water import get_water_status

log = logging.getLogger(__name__)
router = APIRouter()

PARCEL_ID_PATTERN = r"^\d{6}_\d\.\d{4}(\.[0-9A-Za-z/\-]{1,30})?$"
Lat = Query(..., ge=49.0, le=55.0, description="Szerokość geograficzna (WGS84)")
Lon = Query(..., ge=14.0, le=24.2, description="Długość geograficzna (WGS84)")


class ParcelIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    lat: float = Field(ge=49.0, le=55.0)
    lon: float = Field(ge=14.0, le=24.2)
    crop: Optional[str] = Field(default=None, max_length=120)
    soil: Optional[str] = Field(default=None, max_length=120)
    id_tag: Optional[str] = Field(default=None, max_length=120)
    slope: Optional[float] = Field(default=None, ge=0.0, le=60.0, description="Spadek terenu [%]")
    parcel_id: Optional[str] = Field(default=None, max_length=60, pattern=PARCEL_ID_PATTERN)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parcels: List[ParcelIn] = Field(min_length=1, max_length=50)
    weather_model: Literal["auto", "ecmwf", "icon", "gfs"] = "auto"


class GateToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate_id: str = Field(pattern=r"^Z-\d{1,2}$", max_length=8)
    status: Literal["zamknieta", "przymknieta", "otwarta"]
    retaining_cm: Optional[float] = Field(default=None, ge=0.0, le=150.0)


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    uid = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    return str(uid)[:120] if uid else "anon"


@router.get("/api/presets")
def get_presets() -> Dict[str, Any]:
    return {"farms": get_farm_presets_list(), "weather_models": weather_models_list()}


@router.post("/api/analyze")
def run_analysis(req: AnalyzeRequest) -> Dict[str, Any]:
    return analyze_parcels_batch([p.model_dump() for p in req.parcels], req.weather_model)


@router.get("/api/sources/status")
def api_sources_status(check: bool = Query(False, description="Wykonaj zapytania testowe do źródeł")) -> List[Dict[str, Any]]:
    return sources_status(check=check)


# ------------------------------ Dane referencyjne ------------------------------
@router.get("/api/reference/yields")
def api_reference_yields(crop: str = Query(..., min_length=1, max_length=120, description="Uprawa (nazwa z aplikacji)"),
                         voivodeship: Optional[str] = Query(None, max_length=40, description="Nazwa województwa lub kod TERYT"),
                         lat: Optional[float] = Query(None, ge=49.0, le=55.0), lon: Optional[float] = Query(None, ge=14.0, le=24.2)
                         ) -> Dict[str, Any]:
    """Plony z 1 ha (GUS BDL) dla województwa i Polski, ostatnie ~10 lat. Województwo z parametru albo z ULDK (lat/lon)."""
    if not voivodeship and lat is not None and lon is not None:
        p = fetch_parcel_by_xy(lon, lat)
        voivodeship = p.voivodeship if p else None
    if not voivodeship:
        raise HTTPException(status_code=422, detail="Podaj województwo (nazwa lub TERYT) albo współrzędne działki.")
    try:
        return gus.yields_reference(crop, voivodeship)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SourceError as exc:
        var, note = gus.map_crop(crop)
        return {"crop": crop, "gus_crop": gus.GUS_CROPS.get(var) if var else None, "variable_id": var, "unit": "dt/ha",
                "voivodeship": None, "available": False, "years": [], "note": note,
                "provenance": missing("GUS – Bank Danych Lokalnych", gus.BDL_PAGE, f"GUS BDL nie odpowiedział ({friendly(exc)}).")}


@router.get("/api/reference/prices")
def api_reference_prices(crops: str = Query(..., min_length=1, max_length=1200, description="Uprawy rozdzielone znakiem |"),
                         voivodeship: Optional[str] = Query(None, max_length=40, description="Nazwa województwa lub kod TERYT"),
                         lat: Optional[float] = Query(None, ge=49.0, le=55.0), lon: Optional[float] = Query(None, ge=14.0, le=24.2)
                         ) -> Dict[str, Any]:
    """Roczne ceny skupu (GUS BDL) upraw z działek dla województwa i zmiana do roku poprzedniego."""
    names = [c.strip() for c in crops.split("|") if c.strip()]
    if not names:
        raise HTTPException(status_code=422, detail="Podaj co najmniej jedną uprawę.")
    if not voivodeship and lat is not None and lon is not None:
        p = fetch_parcel_by_xy(lon, lat)
        voivodeship = p.voivodeship if p else None
    if voivodeship and gus.resolve_voivodeship(voivodeship) is None:
        raise HTTPException(status_code=422, detail="Nieznane województwo – podaj nazwę (np. „wielkopolskie”) albo dwucyfrowy kod TERYT.")
    return gus.prices_reference(names, voivodeship)


@router.get("/api/reference/ndvi")
def api_reference_ndvi(parcel_id: Optional[str] = Query(None, max_length=60, pattern=PARCEL_ID_PATTERN),
                       lat: Optional[float] = Query(None, ge=49.0, le=55.0),
                       lon: Optional[float] = Query(None, ge=14.0, le=24.2),
                       parcel_ids: Optional[str] = Query(None, max_length=3100, description="Kilka działek: identyfikatory TERYT rozdzielone przecinkami")) -> Dict[str, Any]:
    """Średnie wieloletnie (2019–2025) NDVI dla każdego miesiąca kalendarzowego dla działki (Sentinel-2, Copernicus)."""
    if parcel_ids:
        p, found, lost = _selection(parcel_ids, None, None, None)
        if p is None:
            return {"parcel_ids": [], "missing_parcel_ids": lost, "period": "2019–2025", "months": [],
                    "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, _NO_PARCEL)}
        return _multi_note({"parcel_id": p.parcel_id, **sentinel.ndvi_climatology(p)}, found, lost)
    p = fetch_parcel_by_id(parcel_id) if parcel_id else None
    if p is None and lat is not None and lon is not None:
        p = fetch_parcel_by_xy(lon, lat)
    if p is None:
        if not parcel_id and (lat is None or lon is None):
            raise HTTPException(status_code=422, detail="Podaj identyfikator działki albo współrzędne.")
        return {"parcel_id": parcel_id, "period": "2019–2025", "months": [],
                "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, "Nie udało się pobrać granic działki z GUGiK ULDK.")}
    return {"parcel_id": p.parcel_id, **sentinel.ndvi_climatology(p)}


@router.get("/api/reference/climate")
def api_reference_climate(lat: float = Lat, lon: float = Lon) -> Dict[str, Any]:
    """Klimat pola od 1991 r. (ERA5): trendy temperatury, opadów, okresu wegetacyjnego, przymrozków, upałów, GDD i bilansu
    wodnego + bieżący sezon na tle średniej 1991–2020."""
    return climate.climate_summary(lat, lon)


@router.get("/api/reference/rules")
def api_reference_rules(lat: float = Lat, lon: float = Lon,
                        parcel_id: Optional[str] = Query(None, max_length=60, pattern=PARCEL_ID_PATTERN),
                        crop: Optional[str] = Query(None, max_length=120)) -> Dict[str, Any]:
    """Progi z przepisów: wiatr przy opryskach (Dz.U. 2014/516 zm. 2026/353), terminy i progi temperatury nawożenia
    azotem (program azotanowy, Dz.U. 2023/244) + prognoza do wykresów."""
    return rules.field_rules(lat, lon, parcel_id, crop)


# ------------------------------ Historia działki ------------------------------
def _parcel_for(parcel_id: Optional[str], lat: Optional[float], lon: Optional[float]):
    p = fetch_parcel_by_id(parcel_id) if parcel_id else None
    if p is None and lat is not None and lon is not None:
        p = fetch_parcel_by_xy(lon, lat)
    if p is None and not parcel_id and (lat is None or lon is None):
        raise HTTPException(status_code=422, detail="Podaj identyfikator działki albo współrzędne.")
    return p


_NO_PARCEL = "Nie udało się pobrać granic działki z GUGiK ULDK."
MAX_SELECTED = 50
_PID_RE = re.compile(PARCEL_ID_PATTERN)


def _selection(parcel_ids: Optional[str], parcel_id: Optional[str], lat: Optional[float], lon: Optional[float]):
    """(działka albo połączone działki | None, znalezione id, brakujące id). parcel_ids ma pierwszeństwo."""
    if not parcel_ids:
        p = _parcel_for(parcel_id, lat, lon)
        return p, ([p.parcel_id] if p else []), []
    ids = sorted({x.strip() for x in parcel_ids.split(",") if x.strip()})
    if not ids or len(ids) > MAX_SELECTED or not all(_PID_RE.match(x) for x in ids):
        raise HTTPException(status_code=422, detail=f"Podaj od 1 do {MAX_SELECTED} poprawnych identyfikatorów działek rozdzielonych przecinkami.")
    found, lost = [], []
    for pid in ids:
        p = fetch_parcel_by_id(pid)
        (found if p is not None else lost).append(p if p is not None else pid)
    if not found:
        return None, [], lost
    return merge_parcels(found), [p.parcel_id for p in found], lost


def _multi_note(res: Dict[str, Any], found: List[str], lost: List[str]) -> Dict[str, Any]:
    """Dla kilku działek: dopisek w opisie źródła (bez zmiany obiektu z cache)."""
    out = dict(res, parcel_ids=found, missing_parcel_ids=lost)
    if len(found) > 1 and isinstance(res.get("provenance"), dict):
        prov = dict(res["provenance"])
        extra = f"Zaznaczone działki: {len(found)} – wartości to średnia ze wszystkich pikseli tych działek (ważona powierzchnią)."
        if lost:
            extra += f" Pominięto {len(lost)} działek bez granic z ULDK."
        prov["note"] = f"{extra} {prov.get('note') or ''}".strip()
        out["provenance"] = prov
    return out


@router.get("/api/history/indices")
def api_history_indices(start: Optional[str] = Query(None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"), end: Optional[str] = Query(None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"),
                        parcel_id: Optional[str] = Query(None, max_length=60, pattern=PARCEL_ID_PATTERN),
                        lat: Optional[float] = Query(None, ge=49.0, le=55.0),
                        lon: Optional[float] = Query(None, ge=14.0, le=24.2),
                        parcel_ids: Optional[str] = Query(None, max_length=3100, description="Kilka działek: identyfikatory TERYT rozdzielone przecinkami")) -> Dict[str, Any]:
    """Miesięczne NDVI, NDMI, NDWI, EVI i udział gleby odsłoniętej z Sentinel-2 (36 miesięcy); dla kilku działek – średnia ze wszystkich pikseli."""
    p, found, lost = _selection(parcel_ids, parcel_id, lat, lon)
    if p is None:
        return {"parcel_id": parcel_id, "parcel_ids": [], "missing_parcel_ids": lost, "months": [], "indices": history.INDEX_INFO_PUBLIC,
                "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, _NO_PARCEL)}
    return _multi_note({"parcel_id": p.parcel_id, **history.monthly_indices(p, start=start, end=end)}, found, lost)


@router.get("/api/history/ndvi")
def api_history_ndvi(parcel_id: Optional[str] = Query(None, max_length=60, pattern=PARCEL_ID_PATTERN),
                     lat: Optional[float] = Query(None, ge=49.0, le=55.0),
                     lon: Optional[float] = Query(None, ge=14.0, le=24.2)) -> Dict[str, Any]:
    """Historia NDVI 24 mies. dla działki (to, co /api/analyze zwraca w polu history) – doczytywana w tle przez przeglądarkę."""
    p = _parcel_for(parcel_id, lat, lon)
    if p is None:
        return {"parcel_id": parcel_id, "history": None}
    return {"parcel_id": p.parcel_id, "history": sentinel.monthly_history(p)}


@router.get("/api/layers/true-color")
def api_layers_true_color(parcel_id: Optional[str] = Query(None, max_length=60, pattern=PARCEL_ID_PATTERN),
                          lat: Optional[float] = Query(None, ge=49.0, le=55.0),
                          lon: Optional[float] = Query(None, ge=14.0, le=24.2)) -> Dict[str, Any]:
    """Zdjęcie Sentinel-2 w barwach naturalnych z tej samej sceny co bieżące NDVI (warstwa layers.true_color)."""
    p = _parcel_for(parcel_id, lat, lon)
    if p is None:
        return {"parcel_id": parcel_id, "layer": None, "warnings": [_NO_PARCEL]}
    return {"parcel_id": p.parcel_id, **sentinel.true_color_layer(p)}


@router.get("/api/history/frame")
def api_history_frame(month: str = Query(..., pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"),
                      index: Literal["ndvi", "ndmi", "ndwi", "evi", "bare", "photo"] = "ndvi",
                      parcel_id: Optional[str] = Query(None, max_length=60, pattern=PARCEL_ID_PATTERN),
                      lat: Optional[float] = Query(None, ge=49.0, le=55.0),
                      lon: Optional[float] = Query(None, ge=14.0, le=24.2),
                      parcel_ids: Optional[str] = Query(None, max_length=3100, description="Kilka działek: identyfikatory TERYT rozdzielone przecinkami")) -> Dict[str, Any]:
    """Klatka mapy do animacji: miesięczny kompozyt wskaźnika dla działki albo kilku działek naraz (jedna nakładka PNG)."""
    p, found, lost = _selection(parcel_ids, parcel_id, lat, lon)
    if p is None:
        return {"month": month, "index": index, "image": None, "bounds": None, "mean": None, "parcel_ids": [], "missing_parcel_ids": lost,
                "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL, _NO_PARCEL)}
    return _multi_note({"parcel_id": p.parcel_id, **history.monthly_frame(p, month, index)}, found, lost)


@router.get("/api/history/climate")
def api_history_climate(lat: float = Lat, lon: float = Lon, start: Optional[str] = Query(None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"),
                        end: Optional[str] = Query(None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")) -> Dict[str, Any]:
    """Miesięczne opady (ERA5) na tle normy IMGW-PIB 1991–2020 i wilgotność gleby (ERA5-Land); domyślnie 36 miesięcy."""
    return history.monthly_climate(lat, lon, start=start, end=end)


@router.get("/api/county/ndvi")
def api_county_ndvi(lat: float = Lat, lon: float = Lon, day: str = Query(..., pattern=r"^20\d{2}-\d{2}-\d{2}$"),
                    ndvi: Optional[float] = Query(None, ge=-1.0, le=1.0)) -> Dict[str, Any]:
    """NDVI działki na tle pól uprawnych powiatu z tego samego dnia (Sentinel-2, granica powiatu z ULDK)."""
    return county.county_ndvi(lat, lon, day, ndvi)


@router.get("/api/county/monthly")
def api_county_monthly(points: str = Query(..., max_length=2000, pattern=r"^-?\d{1,3}(\.\d+)?,-?\d{1,3}(\.\d+)?(,\d+(\.\d+)?)?(;-?\d{1,3}(\.\d+)?,-?\d{1,3}(\.\d+)?(,\d+(\.\d+)?)?){0,49}$"),
                       start: Optional[str] = Query(None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"),
                       end: Optional[str] = Query(None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")) -> Dict[str, Any]:
    """Miesięczna średnia NDVI pól uprawnych w powiecie działek (points = "lat,lon,ha;…"; kilka powiatów – ważone powierzchnią)."""
    pts = []
    for part in points.split(";"):
        xs = [float(x) for x in part.split(",")]
        lat, lon, w = xs[0], xs[1], (xs[2] if len(xs) > 2 else 1.0)
        if not (49.0 <= lat <= 55.0 and 14.0 <= lon <= 24.2):
            raise HTTPException(status_code=422, detail="Punkt poza Polską.")
        pts.append((lat, lon, w))
    return county.county_monthly(pts, start, end)


# ------------------------------ HydroMelior ------------------------------
@router.get("/api/hydromelior/network")
def api_hydromelior_network(request: Request, lat: float = Lat, lon: float = Lon) -> Dict[str, Any]:
    return get_network(lat, lon, _user_id(request))


@router.post("/api/hydromelior/toggle-gate")
def api_hydromelior_toggle(req: GateToggleRequest, request: Request) -> Dict[str, Any]:
    try:
        updated = update_gate_status(req.gate_id, req.status, req.retaining_cm, user_id=_user_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Zastawka nie istnieje (zastawki są dostępne tylko w gospodarstwie demo).")
    return updated


@router.get("/api/hydromelior/water")
def api_hydromelior_water(lat: float = Lat, lon: float = Lon, soil: Optional[str] = Query(None, max_length=120)) -> Dict[str, Any]:
    return get_water_status(lat, lon, soil)


@router.get("/api/hydromelior/alerts")
def api_hydromelior_alerts(request: Request, lat: float = Lat, lon: float = Lon,
                           soil: Optional[str] = Query(None, max_length=120)) -> Dict[str, Any]:
    return generate_alerts(lat, lon, soil, user_id=_user_id(request))


@router.get("/api/hydromelior/coordination")
def api_hydromelior_coordination(lat: float = Lat, lon: float = Lon) -> Dict[str, Any]:
    return get_catchment_coordination_status(lat, lon)


@router.get("/api/hydromelior/ecoscheme")
def api_hydromelior_ecoscheme(lat: float = Lat, lon: float = Lon,
                              parcel_id: Optional[str] = Query(None, max_length=60, pattern=PARCEL_ID_PATTERN)) -> Dict[str, Any]:
    return ecoscheme_info(lat, lon, parcel_id)


@router.get("/api/hydromelior/ecoscheme-download", response_class=HTMLResponse)
def api_hydromelior_download(lat: float = Lat, lon: float = Lon,
                             parcel_id: Optional[str] = Query(None, max_length=60, pattern=PARCEL_ID_PATTERN)) -> HTMLResponse:
    info = ecoscheme_info(lat, lon, parcel_id)
    return HTMLResponse(render_html(info), headers={
        "Content-Disposition": 'attachment; filename="mrolnik_informacja_ekoschemat.html"',
        "Cache-Control": "no-store",
    })


__all__ = ["router", "GATE_STATUSES"]

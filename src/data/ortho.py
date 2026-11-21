"""Data nalotu ortofotomapy GUGiK w punkcie (do podpisu „Ortofotomapa © GUGiK, nalot …” na mapie).

Źródło: usługa WMS „SkorowidzeWgAktualnosci” GUGiK – skorowidze arkuszy ortofotomapy z datą nalotu
(`aktualnosc`), wielkością piksela i kolorem (RGB/CIR). GetFeatureInfo zwraca atrybuty tylko w text/html, jako wywołania
JS `skorDo5cm.push({...})`. Odpytujemy warstwy od najnowszej (bieżący rok, dwa poprzednie, „Starsze”) i bierzemy
najnowszy arkusz RGB – mozaika „ORTO StandardResolution” pokazuje na wierzchu właśnie najnowszą scenę.
Brak odpowiedzi = brak daty (nigdy nie zgadujemy).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional

from src.core.cache import TTLCache
from src.data.common import (
    SOURCE_ORTHO, SourceError, friendly, http_request, iso_utc, missing, provenance, record_error, record_ok, utcnow,
)

SKOROWIDZE_WMS = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/SkorowidzeWgAktualnosci"
SOURCE_URL = "https://www.geoportal.gov.pl/"
_cache = TTLCache("ortho_date", max_entries=5000, default_ttl=7 * 24 * 3600)
_PUSH = re.compile(r"\w+\.push\(\{(.*?)\}\)", re.S)
_FIELD = re.compile(r'(\w+)\s*:\s*"([^"]*)"')
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def layers_newest_first(today: Optional[date] = None) -> List[str]:
    y = (today or utcnow().date()).year
    return [f"SkorowidzeOrtofotomapy{y}", f"SkorowidzeOrtofotomapy{y - 1}", f"SkorowidzeOrtofotomapy{y - 2}",
            "SkorowidzeOrtofotomapyStarsze"]


def parse_sheets(html: str) -> List[Dict[str, str]]:
    """Arkusze z odpowiedzi GetFeatureInfo (text/html): lista słowników pól, np. aktualnosc, wielkoscPiksela, kolor, godlo."""
    out = []
    for body in _PUSH.findall(html or ""):
        d = dict(_FIELD.findall(body))
        if _DATE.match(d.get("aktualnosc", "")):
            out.append(d)
    return out


def _px(s: Dict[str, str]) -> Optional[float]:
    try:
        return float((s.get("wielkoscPiksela") or "").replace(",", "."))
    except ValueError:
        return None


def newest_rgb(sheets: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Najnowszy arkusz w barwach naturalnych (nie CIR); przy tej samej dacie – mniejszy piksel."""
    rgb = [s for s in sheets if (s.get("kolor") or "RGB").upper() == "RGB"]
    if not rgb:
        return None
    best = max(rgb, key=lambda s: (s["aktualnosc"], -(_px(s) or 99.0)))
    return {"date": best["aktualnosc"], "pixel_m": _px(best), "sheet": best.get("godlo") or None}


def _gfi(layer: str, lat: float, lon: float) -> str:
    d = 0.001
    params = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": layer, "QUERY_LAYERS": layer, "STYLES": "",
        "CRS": "EPSG:4326", "BBOX": f"{lat - d:.6f},{lon - d:.6f},{lat + d:.6f},{lon + d:.6f}",
        "WIDTH": 101, "HEIGHT": 101, "I": 50, "J": 50, "FEATURE_COUNT": 50, "INFO_FORMAT": "text/html",
    }
    return http_request("GET", SKOROWIDZE_WMS, params=params, timeout=8.0, retries=1).text


def ortho_date(lat: float, lon: float) -> Dict[str, Any]:
    """{"date": "RRRR-MM-DD" | None, "pixel_m", "sheet", "provenance"}. Klucz cache: punkt zaokrąglony do ok. 1 km."""
    key = f"{lat:.2f}:{lon:.2f}"
    cached = _cache.get(key)
    if cached:
        return cached
    try:
        found = None
        for layer in layers_newest_first():
            found = newest_rgb(parse_sheets(_gfi(layer, lat, lon)))
            if found:
                break
    except SourceError as exc:
        record_error(SOURCE_ORTHO, str(exc))
        return {"date": None, "pixel_m": None, "sheet": None,
                "provenance": missing(SOURCE_ORTHO, SOURCE_URL, f"Skorowidz ortofotomap GUGiK nie odpowiada ({friendly(exc)}).")}
    record_ok(SOURCE_ORTHO)
    if not found:
        res = {"date": None, "pixel_m": None, "sheet": None,
               "provenance": missing(SOURCE_ORTHO, SOURCE_URL, "Skorowidz GUGiK nie ma arkusza ortofotomapy dla tego miejsca.")}
    else:
        res = {**found, "provenance": provenance(
            SOURCE_ORTHO, SOURCE_URL, "pomiar", observed_at=found["date"], fetched_at=iso_utc(),
            resolution=None if found["pixel_m"] is None else f"{found['pixel_m']:g} m",
            note="Data nalotu z skorowidza arkuszy ortofotomapy GUGiK (usługa SkorowidzeWgAktualnosci) – najnowszy arkusz RGB w tym miejscu.")}
    _cache.set(key, res, 7 * 24 * 3600)
    return res

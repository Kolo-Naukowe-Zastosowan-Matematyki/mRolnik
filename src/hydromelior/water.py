"""Bieżąca sytuacja wodna działki dla HydroMelior (GET /api/hydromelior/water).

Łączy automatycznie pobierane dane:
- bilans wodny: opad i ET0 z ostatnich 7 dni (analiza modelu Open-Meteo) + prognoza 14 dni,
- najbliższy wodowskaz IMGW-PIB (stan wody, trend),
- wilgotność gleby (model Open-Meteo / ERA5-Land) na tle pojemności polowej i punktu więdnięcia dla klasy gleby,
- ostrzeżenia meteorologiczne IMGW-PIB,
i wylicza orientacyjne zalecenie dla zastawki (zamknięta / przymknięta / otwarta) z jawnie podanym uzasadnieniem.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.data.common import SourceError, iso_utc, missing, provenance, friendly
from src.data.soil import SOIL_TEXTURES, classify_soil, fetch_soil
from src.data.reference import climate_block, cumulative_normal_precip, gauge_reference, soil_reference_provenance
from src.data.weather import fetch_forecast, nearest_hydro, summarize_daily, warnings_for

log = logging.getLogger(__name__)

HEAVY_RAIN_DAY_MM = 20.0
HEAVY_RAIN_3D_MM = 30.0
FLOOD_EVENT_WORDS = ("opad", "deszcz", "burz", "roztop", "wezbran", "powód", "powod")

GATE_LABELS = {"closed": "zamknięta", "half": "przymknięta", "open": "otwarta"}


def recommend_gate(
    *,
    wetness: Optional[float],
    balance_14d: Optional[float],
    rain_next3: Optional[List[Optional[float]]],
    flood_warning: bool,
    gauge: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Orientacyjne ustawienie zastawki. `wetness` = (θ − θWP)/(θFC − θWP) dla warstwy 0–28 cm.

    Reguły (kolejność ma znaczenie):
    1. otwarta – prognozowany intensywny opad (≥20 mm/dobę lub ≥30 mm w 3 dni), ostrzeżenie IMGW o opadach/
       roztopach, stan wody na wodowskazie ≥ stanu ostrzegawczego, albo gleba nasycona (wetness ≥ 1,0),
    2. zamknięta – gleba poniżej 50% wody dostępnej i ujemny bilans 14 dni (< −10 mm): zatrzymaj wodę w rowie,
    3. przymknięta – pozostałe przypadki (utrzymanie średniego poziomu).
    """
    reasons: List[str] = []
    rain3 = [r for r in (rain_next3 or []) if r is not None]
    heavy = bool(rain3) and (max(rain3) >= HEAVY_RAIN_DAY_MM or sum(rain3) >= HEAVY_RAIN_3D_MM)
    gauge_high = bool(gauge and gauge.get("warning_level_cm") is not None and gauge.get("level_cm") is not None
                      and gauge["level_cm"] >= gauge["warning_level_cm"])
    if wetness is None and balance_14d is None and not rain3:
        return {"position": None, "label": "brak danych", "reasons": ["Brak danych o glebie i prognozie – nie można wyliczyć zalecenia."]}
    if heavy:
        reasons.append(f"Prognozowany intensywny opad (maks. {max(rain3):.0f} mm/dobę, {sum(rain3):.0f} mm w 3 dni) – zrób miejsce w rowie na odpływ.")
    if flood_warning:
        reasons.append("Obowiązuje ostrzeżenie IMGW-PIB dotyczące opadów lub roztopów.")
    if gauge_high:
        reasons.append(f"Stan wody na wodowskazie {gauge.get('name')} jest na poziomie ostrzegawczym lub wyższym.")
    if wetness is not None and wetness >= 1.0:
        reasons.append("Gleba jest nasycona wodą (powyżej pojemności polowej) – ryzyko zastoisk.")
    if reasons:
        return {"position": "open", "label": GATE_LABELS["open"], "reasons": reasons}
    if wetness is not None and wetness < 0.5 and balance_14d is not None and balance_14d < -10:
        return {"position": "closed", "label": GATE_LABELS["closed"], "reasons": [
            f"W glebie zostało mniej niż połowa wody dostępnej dla roślin ({wetness * 100:.0f}%).",
            f"Prognozowany bilans wodny 14 dni jest ujemny ({balance_14d:.0f} mm) – zatrzymaj wodę w rowie.",
        ]}
    why = []
    if wetness is not None:
        why.append(f"Woda dostępna w glebie: {wetness * 100:.0f}% zakresu użytecznego.")
    if balance_14d is not None:
        why.append(f"Bilans wodny 14 dni: {balance_14d:+.0f} mm.")
    why.append("Utrzymuj średni poziom piętrzenia i obserwuj prognozę.")
    return {"position": "half", "label": GATE_LABELS["half"], "reasons": why}


def get_water_status(lat: float, lon: float, soil_name: Optional[str] = None) -> Dict[str, Any]:
    """Odpowiedź GET /api/hydromelior/water. Każdy element ma proweniencję; braki → null + "brak"."""
    out: Dict[str, Any] = {"generated_at": iso_utc(), "location": [lat, lon], "warnings": []}

    # --- bilans wodny: 7 dni wstecz + 14 dni prognozy ---
    forecast = None
    try:
        forecast = fetch_forecast(lat, lon, "auto")
    except SourceError as exc:
        out["warnings"].append(f"Prognoza Open-Meteo niedostępna ({friendly(exc)}).")
    if forecast:
        rows = [{"date": d["date"], "precip_mm": d["rain_mm"], "et0_mm": d["et0_mm"], "kind": "obs"} for d in forecast.get("past", [])[-7:]]
        rows += [{"date": d["date"], "precip_mm": d["rain_mm"], "et0_mm": d["et0_mm"], "kind": "forecast"} for d in forecast["daily"]]
        summ = summarize_daily(forecast["daily"])
        out["water_balance"] = rows
        out["water_balance_summary"] = {
            "past_7d_precip_mm": _sum(r["precip_mm"] for r in rows if r["kind"] == "obs"),
            "past_7d_et0_mm": _sum(r["et0_mm"] for r in rows if r["kind"] == "obs"),
            "forecast_14d_precip_mm": summ["precip_14d_mm"],
            "climatic_water_balance_14d_mm": summ["climatic_water_balance_14d_mm"],
        }
        wb_prov = provenance(
            "Open-Meteo – " + forecast.get("model_label", "model"), "https://open-meteo.com/", "prognoza",
            fetched_at=forecast.get("fetched_at"),
            note=("Dni 'obs' = ostatnie 7 dni z analizy modelu numerycznego (nie pomiar stacyjny); dni 'forecast' = prognoza. "
                  "ET0 – ewapotranspiracja wskaźnikowa FAO-56 Penman-Monteith. Dane: Open-Meteo.com, CC BY 4.0."),
        )
    else:
        out["water_balance"] = []
        out["water_balance_summary"] = None
        wb_prov = missing("Open-Meteo", "https://open-meteo.com/", "Brak danych do bilansu wodnego.")

    # --- linia referencyjna bilansu: norma opadu IMGW-PIB 1991–2020 narastająco dla tych samych dni ---
    climate = climate_block(lat, lon, [r["date"] for r in out["water_balance"]])
    reference: Dict[str, Any] = {"climate_normals": climate,
                                 "water_balance": cumulative_normal_precip(out["water_balance"], climate["daily"])}

    # --- wodowskaz IMGW-PIB (+ stany charakterystyczne i 7 dni z hydro.imgw.pl) ---
    gauge, gauge_prov = nearest_hydro(lat, lon)
    gref = None
    if gauge is not None:
        gref = gauge_reference(gauge)
        gauge = dict(gauge, warning_level_cm=gref["warning_level_cm"], alarm_level_cm=gref["alarm_level_cm"])
    out["river_gauge"] = None if gauge is None else {
        "station": gauge["name"], "station_id": gauge.get("id") or None, "river": gauge["river"] or None,
        "level_cm": gauge["level_cm"], "trend": gauge.get("trend"), "observed_at": gauge.get("measured_at"),
        "distance_km": gauge.get("distance_km"),
        "warning_level_cm": gref["warning_level_cm"], "alarm_level_cm": gref["alarm_level_cm"],
        "low_level_cm": gref["low_level_cm"], "high_level_cm": gref["high_level_cm"],
        "state_label": gref["state_label"], "series_7d": gref["series_7d"],
        "provenance": gauge_prov, "reference_provenance": gref["provenance"],
    }
    if gref is not None:
        reference["river_gauge"] = {k: gref[k] for k in ("warning_level_cm", "alarm_level_cm", "low_level_cm", "high_level_cm")}
        reference["river_gauge"]["provenance"] = gref["provenance"]

    # --- wilgotność gleby na tle FC / WP ---
    cls, recognized = classify_soil(soil_name)
    tex = SOIL_TEXTURES[cls]
    soil_ref_prov = soil_reference_provenance(tex["label"], round(tex["fc"] * 100, 1), round(tex["wp"] * 100, 1))
    reference["soil_moisture"] = {"field_capacity_pct": round(tex["fc"] * 100, 1), "wilting_point_pct": round(tex["wp"] * 100, 1),
                                  "soil_class": tex["label"], "provenance": soil_ref_prov}
    wetness = None
    try:
        s = fetch_soil(lat, lon)
        theta = s["root"] if s["surface"] is None else (s["surface"] * 7 + (s["root"] or s["surface"]) * 21) / 28
        wetness = (theta - tex["wp"]) / (tex["fc"] - tex["wp"]) if theta is not None else None
        sm_prov = provenance(s["source"], "https://open-meteo.com/", s["kind"], observed_at=s.get("valid_at"),
                             fetched_at=s.get("fetched_at"), resolution="2–11 km",
                             note=s["note_src"] + f" FC/WP: typowe wartości FAO-56 dla klasy '{tex['label']}'.")
        out["soil_moisture"] = {
            "surface_pct": None if s["surface"] is None else round(s["surface"] * 100, 1),
            "root_zone_pct": None if s["root"] is None else round(s["root"] * 100, 1),
            "trend_7d": s.get("trend_7d"),
            "field_capacity_pct": round(tex["fc"] * 100, 1), "wilting_point_pct": round(tex["wp"] * 100, 1),
            "soil_class": tex["label"], "soil_recognized": recognized,
            "available_water_pct": None if wetness is None else round(max(0.0, min(1.5, wetness)) * 100, 0),
            "provenance": sm_prov, "reference_provenance": soil_ref_prov,
        }
    except SourceError as exc:
        out["soil_moisture"] = {"surface_pct": None, "root_zone_pct": None, "trend_7d": None,
                                "field_capacity_pct": round(tex["fc"] * 100, 1), "wilting_point_pct": round(tex["wp"] * 100, 1),
                                "soil_class": tex["label"], "soil_recognized": recognized, "available_water_pct": None,
                                "reference_provenance": soil_ref_prov,
                                "provenance": missing("Open-Meteo", "https://open-meteo.com/", f"Brak danych o wilgotności gleby ({friendly(exc)}).")}

    # --- ostrzeżenia IMGW ---
    alerts, warn_prov = warnings_for(lat, lon)
    out["imgw_warnings"] = alerts
    flood = any(any(w in (a.get("event") or "").lower() for w in FLOOD_EVENT_WORDS) and (a.get("level") or 0) >= 2 for a in alerts)

    # --- zalecenie dla zastawki ---
    rec = recommend_gate(
        wetness=wetness,
        balance_14d=(out["water_balance_summary"] or {}).get("climatic_water_balance_14d_mm"),
        rain_next3=[d["rain_mm"] for d in (forecast or {}).get("daily", [])[:3]],
        flood_warning=flood, gauge=gauge,
    )
    rec["provenance"] = provenance(
        "Reguły decyzyjne mRolnik (HydroMelior)", "https://open-meteo.com/", "model" if rec["position"] else "brak",
        fetched_at=iso_utc(),
        note=("Zalecenie orientacyjne wyliczone z prognozy opadu, bilansu wodnego, wilgotności gleby, ostrzeżeń i wodowskazu IMGW-PIB. "
              "Manewrowanie zastawkami prowadź zgodnie z pozwoleniem wodnoprawnym i ustaleniami spółki wodnej."),
    )
    out["gate_recommendation"] = rec
    out["reference"] = reference
    out["provenance"] = {"water_balance": wb_prov, "river_gauge": gauge_prov, "soil_moisture": out["soil_moisture"]["provenance"],
                         "warnings": warn_prov, "gate_recommendation": rec["provenance"],
                         "climate_normals": climate["provenance"], "soil_reference": soil_ref_prov}
    if gref is not None:
        out["provenance"]["gauge_reference"] = gref["provenance"]
    if not recognized:
        out["warnings"].append("Nie rozpoznano rodzaju gleby – przyjęto glebę średnią (glina).")
    return out


def _sum(vals) -> Optional[float]:
    v = [x for x in vals if x is not None]
    return round(sum(v), 1) if v else None

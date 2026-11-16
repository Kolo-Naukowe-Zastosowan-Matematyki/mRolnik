"""Alerty HydroMelior (GET /api/hydromelior/alerts) z realnych danych: prognoza Open-Meteo, ostrzeżenia
IMGW-PIB, najbliższy wodowskaz IMGW-PIB i modelowa wilgotność gleby. Bez danych – brak alertu, nie zgadywanie.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.hydromelior.network import demo_gates, is_demo_area
from src.hydromelior.water import get_water_status


def generate_alerts(lat: float, lon: float, soil: Optional[str] = None, user_id: str = "anon") -> Dict[str, Any]:
    w = get_water_status(lat, lon, soil)
    alerts: List[Dict[str, Any]] = []

    for a in w.get("imgw_warnings", []):
        lvl = a.get("level") or 1
        alerts.append({
            "level": "pilne" if lvl >= 2 else "uwaga",
            "title": f"Ostrzeżenie IMGW-PIB: {a['event']} (stopień {lvl})",
            "text": f"Obowiązuje od {a.get('valid_from') or '?'} do {a.get('valid_to') or '?'}. {a.get('text') or ''}".strip(),
            "source": "IMGW-PIB",
        })

    rows = [r for r in w.get("water_balance", []) if r["kind"] == "forecast"]
    rain3 = [r["precip_mm"] for r in rows[:3] if r["precip_mm"] is not None]
    if rain3 and (max(rain3) >= 20 or sum(rain3) >= 30):
        alerts.append({"level": "pilne", "title": "Intensywny opad w ciągu 3 dni",
                       "text": f"Prognoza: do {max(rain3):.0f} mm na dobę, łącznie {sum(rain3):.0f} mm. Udrożnij odpływ z rowów przed opadem.",
                       "source": "Open-Meteo"})
    summ = w.get("water_balance_summary") or {}
    bal = summ.get("climatic_water_balance_14d_mm")
    if bal is not None and bal < -25:
        alerts.append({"level": "uwaga", "title": "Ujemny bilans wodny",
                       "text": f"Przez 14 dni parowanie przewyższy opad o {abs(bal):.0f} mm. Rozważ zatrzymanie wody w rowach.",
                       "source": "Open-Meteo"})

    g = w.get("river_gauge")
    if g:
        if g.get("alarm_level_cm") is not None and g["level_cm"] >= g["alarm_level_cm"]:
            alerts.append({"level": "pilne", "title": f"Stan alarmowy: {g['river'] or ''} – {g['station']}",
                           "text": f"Stan wody {g['level_cm']:.0f} cm (alarmowy {g['alarm_level_cm']:.0f} cm).", "source": "IMGW-PIB"})
        elif g.get("warning_level_cm") is not None and g["level_cm"] >= g["warning_level_cm"]:
            alerts.append({"level": "uwaga", "title": f"Stan ostrzegawczy: {g['river'] or ''} – {g['station']}",
                           "text": f"Stan wody {g['level_cm']:.0f} cm (ostrzegawczy {g['warning_level_cm']:.0f} cm).", "source": "IMGW-PIB"})
        elif g.get("low_level_cm") is not None and g["level_cm"] < g["low_level_cm"]:
            alerts.append({"level": "info", "title": f"Niski stan wody: {g['river'] or ''} – {g['station']}",
                           "text": (f"Stan wody {g['level_cm']:.0f} cm – poniżej granicy stanów niskich IMGW-PIB "
                                    f"({g['low_level_cm']:.0f} cm). Odpływ z rowów do rzeki nie jest utrudniony; "
                                    "w suszy warto zatrzymywać wodę w rowach."), "source": "IMGW-PIB"})
        elif g.get("trend") == "rośnie":
            alerts.append({"level": "info", "title": f"Rośnie stan wody: {g['station']}",
                           "text": f"Aktualnie {g['level_cm']:.0f} cm, trend wzrostowy.", "source": "IMGW-PIB"})

    sm = w.get("soil_moisture") or {}
    aw = sm.get("available_water_pct")
    if aw is not None:
        if aw >= 100:
            alerts.append({"level": "uwaga", "title": "Gleba nasycona wodą",
                           "text": "Wilgotność powyżej pojemności polowej – unikaj wjazdu ciężkim sprzętem, nie piętrz wody.", "source": "Open-Meteo"})
        elif aw < 30:
            alerts.append({"level": "uwaga", "title": "Mało wody w glebie",
                           "text": f"Woda dostępna dla roślin: ok. {aw:.0f}% zakresu użytecznego (0–28 cm).", "source": "Open-Meteo"})

    rec = w["gate_recommendation"]
    if rec.get("position"):
        alerts.append({"level": "info", "title": f"Zalecane ustawienie zastawki: {rec['label']}", "text": " ".join(rec["reasons"]),
                       "source": "mRolnik"})
    if not alerts:
        if any(w.get(k) for k in ("water_balance", "river_gauge")) or (w.get("soil_moisture") or {}).get("available_water_pct") is not None:
            alerts.append({"level": "info", "title": "Brak alertów", "text": "Na podstawie dostępnych danych nie ma pilnych działań.", "source": "mRolnik"})
        else:
            alerts.append({"level": "info", "title": "Brak danych", "text": "Źródła danych o wodzie nie odpowiedziały – nie można ocenić sytuacji. Spróbuj ponownie później.", "source": "mRolnik"})

    # Kontrakt z UI: pozycja po polsku + jedno zdanie uzasadnienia; dane do wykresów w tej samej odpowiedzi.
    rec_ui = dict(rec)
    if rec.get("position"):
        rec_ui["position"] = {"open": "otwarta", "half": "przymknieta", "closed": "zamknieta"}[rec["position"]]
    rec_ui["reason"] = " ".join(rec.get("reasons") or [])
    out: Dict[str, Any] = {"alerts": alerts, "gate_recommendation": rec_ui, "provenance": w["provenance"], "warnings": w["warnings"]}
    for k in ("water_balance", "river_gauge", "soil_moisture", "reference"):
        if k in w:
            out[k] = w[k]
    if is_demo_area(lat, lon) and rec.get("position"):
        target = {"open": "otwarta", "half": "przymknieta", "closed": "zamknieta"}[rec["position"]]
        out["demo_gates"] = [{"gate_id": gd["gate_id"], "name": gd["name"], "status": gd["status"], "recommended_status": target,
                              "demo": True} for gd in demo_gates(user_id)]
    return out

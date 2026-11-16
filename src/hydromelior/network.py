"""Sieć melioracyjna wokół działki.

Dane podstawowe: prawdziwa hydrografia z usług rządowych (src/hydromelior/hydrography.py: WFS PGW Wody Polskie /
GUGiK BDOT10k + warstwa WMS do podglądu). Rządowe bazy nie zawierają stanu zastawek, dlatego zastawki są wyłącznie
DEMONSTRACJĄ dla gospodarstwa przykładowego w Śnieciskach (pow. średzki) – pokazywane tylko w promieniu ~5 km
od niego i zawsze z flagą demo:true. Stan zastawek demo jest przechowywany osobno dla każdego użytkownika.
"""
from __future__ import annotations

import copy
import logging
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from src.data.common import haversine_km, iso_utc, provenance
from src.hydromelior.hydrography import DEFAULT_WMS_URL, hydrography_near, wms_layers

log = logging.getLogger(__name__)

DEMO_CENTER = (52.2015, 17.2000)  # Śnieciska, gm. Środa Wielkopolska
DEMO_RADIUS_KM = 5.0
GATE_STATUSES = ("zamknieta", "przymknieta", "otwarta")
STATUS_LABELS = {"zamknieta": "zamknięta (piętrzenie)", "przymknieta": "przymknięta", "otwarta": "otwarta (odpływ)"}
RETAINING_MAX_CM = 150.0

DEMO_DITCHES: List[Dict[str, Any]] = [
    {"ditch_id": "R-1", "name": "Rów R-1 (przykład)", "depth_cm": 130,
     "coordinates": [[52.1993, 17.1925], [52.1997, 17.1937], [52.2004, 17.1959], [52.2007, 17.1967], [52.2014, 17.1989], [52.2022, 17.2015]]},
    {"ditch_id": "R-2", "name": "Rów R-2 (przykład)", "depth_cm": 110,
     "coordinates": [[52.2022, 17.2015], [52.2026, 17.2034], [52.2019, 17.2045], [52.2013, 17.2050], [52.2011, 17.2077]]},
]

DEMO_GATES: List[Dict[str, Any]] = [
    {"gate_id": "Z-1", "name": "Zastawka Z-1 (przykład)", "lat": 52.2007, "lon": 17.1967, "ditch_id": "R-1",
     "status": "zamknieta", "retaining_cm": 50.0, "max_retaining_cm": 70.0},
    {"gate_id": "Z-2", "name": "Zastawka Z-2 (przykład)", "lat": 52.2022, "lon": 17.2015, "ditch_id": "R-1",
     "status": "przymknieta", "retaining_cm": 25.0, "max_retaining_cm": 60.0},
    {"gate_id": "Z-3", "name": "Zastawka Z-3 (przykład)", "lat": 52.2011, "lon": 17.2077, "ditch_id": "R-2",
     "status": "otwarta", "retaining_cm": 0.0, "max_retaining_cm": 65.0},
]

_STATE_LOCK = threading.Lock()
_USER_STATE: "OrderedDict[str, Dict[str, Dict[str, Any]]]" = OrderedDict()
_MAX_USERS = 1000


def is_demo_area(lat: float, lon: float) -> bool:
    return haversine_km(lat, lon, *DEMO_CENTER) <= DEMO_RADIUS_KM


def _user_gates(user_id: str) -> Dict[str, Dict[str, Any]]:
    """Stan zastawek demo dla użytkownika (kopiowany z wartości początkowych przy pierwszym użyciu)."""
    uid = (user_id or "anon")[:120]
    with _STATE_LOCK:
        if uid not in _USER_STATE:
            _USER_STATE[uid] = {g["gate_id"]: copy.deepcopy(g) for g in DEMO_GATES}
            while len(_USER_STATE) > _MAX_USERS:
                _USER_STATE.popitem(last=False)
        _USER_STATE.move_to_end(uid)
        return copy.deepcopy(_USER_STATE[uid])


def demo_gates(user_id: str) -> List[Dict[str, Any]]:
    return list(_user_gates(user_id).values())


def update_gate_status(gate_id: str, status: str, retaining_cm: Optional[float] = None, user_id: str = "anon") -> Optional[Dict[str, Any]]:
    """Zmiana stanu zastawki DEMO dla danego użytkownika. Status tylko z enum; spiętrzenie 0–150 cm (i ≤ maks. zastawki)."""
    if status not in GATE_STATUSES:
        raise ValueError("niedozwolony status zastawki")
    if retaining_cm is not None and not (0.0 <= float(retaining_cm) <= RETAINING_MAX_CM):
        raise ValueError("spiętrzenie poza zakresem 0–150 cm")
    _user_gates(user_id)  # inicjalizacja
    uid = (user_id or "anon")[:120]
    with _STATE_LOCK:
        g = _USER_STATE[uid].get(gate_id)
        if g is None:
            return None
        g["status"] = status
        if retaining_cm is not None:
            g["retaining_cm"] = min(float(retaining_cm), g["max_retaining_cm"])
        else:
            g["retaining_cm"] = {"zamknieta": round(g["max_retaining_cm"] * 0.85), "przymknieta": round(g["max_retaining_cm"] * 0.45),
                                 "otwarta": 0.0}[status]
        g["updated_at"] = iso_utc()
        return dict(copy.deepcopy(g), demo=True, status_label=STATUS_LABELS[status])


def _demo_features(user_id: str) -> List[Dict[str, Any]]:
    feats = []
    for d in DEMO_DITCHES:
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in d["coordinates"]]},
                      "properties": {"layer": "rowy_demo", "id": d["ditch_id"], "name": d["name"], "depth_cm": d["depth_cm"], "demo": True}})
    for g in demo_gates(user_id):
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [g["lon"], g["lat"]]},
                      "properties": {"layer": "zastawki_demo", "id": g["gate_id"], "name": g["name"], "ditch_id": g["ditch_id"],
                                     "status": g["status"], "status_label": STATUS_LABELS[g["status"]],
                                     "retaining_cm": g["retaining_cm"], "max_retaining_cm": g["max_retaining_cm"], "demo": True}})
    return feats


def get_network(lat: float, lon: float, user_id: str = "anon") -> Dict[str, Any]:
    """GET /api/hydromelior/network: prawdziwa hydrografia + (tylko w Śnieciskach) zastawki demo."""
    hydro, hydro_prov = hydrography_near(lat, lon)
    demo = is_demo_area(lat, lon)
    features = list(hydro)
    notes = []
    if hydro:
        notes.append(f"Pokazano {len(hydro)} odcinków cieków/rowów z rządowej usługi hydrograficznej (promień ok. 1,5 km).")
    else:
        notes.append("Rowy i cieki są widoczne jako warstwa mapy WMS BDOT10k (GUGiK); obiekty wektorowe z usługi WFS "
                     "nie są dostępne dla tego miejsca.")
    if demo:
        features += _demo_features(user_id)
        notes.append("Zastawki i rowy oznaczone jako DEMO są przykładem dla gospodarstwa w Śnieciskach – nie pochodzą z rejestru.")
    else:
        notes.append("Stan zastawek nie jest publikowany w rejestrach publicznych; docelowo: ewidencja urządzeń melioracyjnych PGW Wody Polskie / spółki wodnej.")
    return {
        "demo": demo,
        "note": " ".join(notes),
        "features": features,
        "provenance": hydro_prov,
        "demo_provenance": provenance("Dane przykładowe mRolnik", "", "demo",
                                      note="Rowy i zastawki przykładowe (Śnieciska) – wyłącznie do demonstracji funkcji.") if demo else None,
        "wms": wms_layers(),
        # źródło warstwy mapy z ciekami i rowami (obraz WMS z państwowej bazy BDOT10k) – osobno od braku obiektów WFS
        "wms_provenance": provenance(
            "GUGiK – BDOT10k: cieki, kanały i rowy (warstwa mapy WMS)", DEFAULT_WMS_URL, "pomiar",
            note=("Baza Danych Obiektów Topograficznych BDOT10k prowadzona przez GUGiK i starostwa. Na mapie jako obraz WMS "
                  "(rowy melioracyjne, rzeki i strumienie, kanały, woda powierzchniowa, przepusty). Aktualność zależy od "
                  "aktualizacji bazy w powiecie; stan techniczny i drożność rowów nie są w niej opisane."),
        ),
    }

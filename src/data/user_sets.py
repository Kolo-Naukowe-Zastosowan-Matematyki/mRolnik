"""Zestawy działek użytkownika („Moje działki” z własnymi nazwami) – zapis po stronie serwera, osobno dla każdego użytkownika.

Plik output/user_sets/<id użytkownika>.json (zapis atomowy, blokada per użytkownik). Limity: MAX_SETS zestawów,
MAX_PARCELS_PER_SET działek w zestawie, nazwa do MAX_NAME znaków. Działka: współrzędne (WGS84, w Polsce), opcjonalnie
identyfikator działki ewidencyjnej (TERYT), etykieta i uprawa – bez danych osobowych.

Pierwszy odczyt dla użytkownika bez pliku zakłada trzy zestawy DEMO zbudowane z prawdziwych działek gospodarstw
przykładowych (presets) – można je zmienić lub usunąć jak każdy inny zestaw.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from typing import Any, Dict, List, Optional

from src.core.config import BASE_DIR
from src.data.common import iso_utc

DIR = BASE_DIR / "output" / "user_sets"
MAX_SETS = 30
MAX_PARCELS_PER_SET = 2000
MAX_NAME = 80
_ID_RE = re.compile(r"^[a-z0-9]{8,16}$")
_PID_RE = re.compile(r"^[0-9A-Za-z_.\-/]{1,120}$")
_locks: Dict[str, threading.Lock] = {}
_guard = threading.Lock()


class SetError(ValueError):
    """Błąd walidacji – komunikat do pokazania użytkownikowi."""


def _path(user_id: str):
    # nazwa pliku z hasha identyfikatora (identyfikator z dostawcy tożsamości może zawierać dowolne znaki)
    return DIR / f"{hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:32]}.json"


def _lock(user_id: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(user_id, threading.Lock())


def clean_name(name: Any) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]", "", str(name or "")).strip()
    s = re.sub(r"\s+", " ", s)
    if not s:
        raise SetError("Podaj nazwę zestawu.")
    if len(s) > MAX_NAME:
        raise SetError(f"Nazwa zestawu może mieć najwyżej {MAX_NAME} znaków.")
    return s


def clean_parcel(p: Dict[str, Any]) -> Dict[str, Any]:
    try:
        lat, lon = float(p["lat"]), float(p["lon"])
    except (KeyError, TypeError, ValueError):
        raise SetError("Działka bez poprawnych współrzędnych.") from None
    if not (49.0 <= lat <= 55.0 and 14.0 <= lon <= 24.2):
        raise SetError("Współrzędne działki poza Polską.")
    out: Dict[str, Any] = {"lat": round(lat, 6), "lon": round(lon, 6)}
    pid = p.get("parcel_id")
    if pid:
        pid = str(pid).strip()
        if not _PID_RE.match(pid):
            raise SetError("Niepoprawny identyfikator działki.")
        out["parcel_id"] = pid
    for k, n in (("id_tag", 60), ("crop", 60), ("soil", 80)):
        v = p.get(k)
        if v:
            v = re.sub(r"[\x00-\x1f\x7f]", "", str(v)).strip()[:n]
            if v:
                out[k] = v
    return out


def _key(p: Dict[str, Any]) -> str:
    return p.get("parcel_id") or f"{p['lat']:.6f},{p['lon']:.6f}"


def _demo_sets() -> List[Dict[str, Any]]:
    from src.data.presets import FARM_PRESETS
    allp = [p for ps in FARM_PRESETS.values() for p in ps]
    picks = [
        ("Pola przy Wiśle", [p for name, ps in FARM_PRESETS.items() if "Żuławy" in name for p in ps]),
        ("Działki pod kukurydzę", [p for p in allp if "Kukurydza" in (p.get("crop") or "")]),
        ("Łąki przy rowach", [p for p in allp if "użytek zielony" in (p.get("crop") or "")]),
    ]
    now = iso_utc()
    return [{"id": secrets.token_hex(5), "name": n, "demo": True, "created_at": now, "updated_at": now,
             "parcels": [clean_parcel(p) for p in ps][:MAX_PARCELS_PER_SET]} for n, ps in picks if ps]


def _read(user_id: str) -> Optional[List[Dict[str, Any]]]:
    try:
        with open(_path(user_id), encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return []    # uszkodzony plik – zaczynamy od pustej listy (bez nadpisywania przykładów DEMO)
    sets = doc.get("sets") if isinstance(doc, dict) else None
    return sets if isinstance(sets, list) else []


def _write(user_id: str, sets: List[Dict[str, Any]]) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    p = _path(user_id)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "sets": sets}, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, p)


def _load(user_id: str) -> List[Dict[str, Any]]:
    sets = _read(user_id)
    if sets is None:
        sets = _demo_sets()
        _write(user_id, sets)
    return sets


def list_sets(user_id: str) -> List[Dict[str, Any]]:
    with _lock(user_id):
        return _load(user_id)


def _find(sets: List[Dict[str, Any]], set_id: str) -> Dict[str, Any]:
    if not _ID_RE.match(set_id or ""):
        raise KeyError(set_id)
    for s in sets:
        if s.get("id") == set_id:
            return s
    raise KeyError(set_id)


def create_set(user_id: str, name: str, parcels: List[Dict[str, Any]]) -> Dict[str, Any]:
    name = clean_name(name)
    ps = _dedup([clean_parcel(p) for p in parcels])
    if len(ps) > MAX_PARCELS_PER_SET:
        raise SetError(f"Zestaw może mieć najwyżej {MAX_PARCELS_PER_SET} działek.")
    with _lock(user_id):
        sets = _load(user_id)
        if len(sets) >= MAX_SETS:
            raise SetError(f"Można mieć najwyżej {MAX_SETS} zestawów – usuń któryś, aby dodać nowy.")
        if any(s.get("name", "").casefold() == name.casefold() for s in sets):
            raise SetError("Zestaw o tej nazwie już istnieje – wybierz inną nazwę albo dodaj działki do istniejącego zestawu.")
        now = iso_utc()
        s = {"id": secrets.token_hex(5), "name": name, "demo": False, "created_at": now, "updated_at": now, "parcels": ps}
        sets.append(s)
        _write(user_id, sets)
        return s


def _dedup(ps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for p in ps:
        k = _key(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def update_set(user_id: str, set_id: str, name: Optional[str] = None, add: Optional[List[Dict[str, Any]]] = None,
               remove: Optional[List[str]] = None) -> Dict[str, Any]:
    new_name = clean_name(name) if name is not None else None
    add_ps = [clean_parcel(p) for p in (add or [])]
    with _lock(user_id):
        sets = _load(user_id)
        s = _find(sets, set_id)
        if new_name is not None:
            if any(x is not s and x.get("name", "").casefold() == new_name.casefold() for x in sets):
                raise SetError("Zestaw o tej nazwie już istnieje.")
            s["name"] = new_name
        ps = list(s.get("parcels") or [])
        if remove:
            drop = set(remove)
            ps = [p for p in ps if _key(p) not in drop]
        ps = _dedup(ps + add_ps)
        if len(ps) > MAX_PARCELS_PER_SET:
            raise SetError(f"Zestaw może mieć najwyżej {MAX_PARCELS_PER_SET} działek.")
        s["parcels"] = ps
        s["updated_at"] = iso_utc()
        _write(user_id, sets)
        return s


def delete_set(user_id: str, set_id: str) -> None:
    with _lock(user_id):
        sets = _load(user_id)
        s = _find(sets, set_id)
        sets.remove(s)
        _write(user_id, sets)

"""Gospodarstwa demonstracyjne: punkty startowe na mapie (po jednym rejonie na województwo).

To są WYŁĄCZNIE współrzędne punktów i przykładowe deklaracje użytkownika (uprawa, gleba).
Granice, powierzchnia, NDVI i pogoda są zawsze pobierane z prawdziwych źródeł podczas analizy;
nazwy upraw nie opisują stanu łanu (stan wynika z danych Sentinel-2).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

log = logging.getLogger("mrolnik.presets")

# Działki zweryfikowane w ULDK skryptem scripts/build_presets.py (różna liczba działek w gospodarstwach).
# Gdy pliku nie ma albo jest uszkodzony, używamy punktów startowych z FALLBACK_PRESETS poniżej.
PRESETS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets_dzialki.json")

DEFAULT_FARM = "Wielkopolskie • Śnieciska (pow. średzki)"

FALLBACK_PRESETS: Dict[str, List[Dict[str, object]]] = {
    "Wielkopolskie • Śnieciska (pow. średzki)": [
        {"id_tag": "Dz. 114/1", "lat": 52.2020, "lon": 17.2012, "crop": "Pszenica ozima", "soil": "Średnia gliniasta (klasa IIIb)"},
        {"id_tag": "Dz. 111/5", "lat": 52.2025, "lon": 17.1990, "crop": "Jęczmień jary", "soil": "Lekka piaszczysto-gliniasta (klasa IVa)"},
        {"id_tag": "Dz. 2/4", "lat": 52.2015, "lon": 17.2035, "crop": "Kukurydza", "soil": "Średnia gliniasta"},
    ],
    "Kujawsko-Pomorskie • Kruszwica (pow. inowrocławski)": [
        {"id_tag": "Grodztwo", "lat": 52.6650, "lon": 18.3600, "crop": "Buraki cukrowe", "soil": "Czarnoziem kujawski (klasa II)"},
        {"id_tag": "Bachorce", "lat": 52.6600, "lon": 18.3700, "crop": "Rzepak ozimy", "soil": "Czarnoziem kujawski"},
    ],
    "Lubelskie • Krasnystaw (pow. krasnostawski)": [
        {"id_tag": "Siennica Nadolna", "lat": 50.9950, "lon": 23.2350, "crop": "Pszenica ozima", "soil": "Gleba lessowa (klasa IIIa)"},
        {"id_tag": "Dolina Wieprza", "lat": 50.9820, "lon": 23.1750, "crop": "Trwały użytek zielony (łąka)", "soil": "Mada rzeczna"},
    ],
    "Dolnośląskie • Strzelin (pow. strzeliński)": [
        {"id_tag": "Pęcz", "lat": 50.7950, "lon": 17.0750, "crop": "Pszenica ozima", "soil": "Czarnoziem (klasa I-II)"},
        {"id_tag": "Strzelin wschód", "lat": 50.7720, "lon": 17.1050, "crop": "Buraki cukrowe", "soil": "Czarnoziem (klasa II)"},
    ],
    "Lubuskie • Sulechów (pow. zielonogórski)": [
        {"id_tag": "Kije", "lat": 52.0850, "lon": 15.5850, "crop": "Kukurydza na ziarno", "soil": "Piaszczysto-gliniasta (klasa IVa)"},
        {"id_tag": "Cigacice", "lat": 52.0450, "lon": 15.5950, "crop": "Żyto", "soil": "Lekka piaszczysta (klasa IV-V)"},
    ],
    "Łódzkie • Kutno (pow. kutnowski)": [
        {"id_tag": "Kutno Wieś", "lat": 52.2650, "lon": 19.3850, "crop": "Pszenica ozima", "soil": "Czarne ziemie (klasa II)"},
        {"id_tag": "Krośniewice", "lat": 52.2850, "lon": 19.1950, "crop": "Warzywa gruntowe", "soil": "Czarne ziemie (klasa I-II)"},
    ],
    "Małopolskie • Proszowice (pow. proszowicki)": [
        {"id_tag": "Klimontów", "lat": 50.1850, "lon": 20.3150, "crop": "Pszenica ozima", "soil": "Rędzina (klasa II)"},
        {"id_tag": "Proszowice wschód", "lat": 50.1920, "lon": 20.3250, "crop": "Warzywa gruntowe", "soil": "Less (klasa IIIa)"},
    ],
    "Mazowieckie • Płońsk (pow. płoński)": [
        {"id_tag": "Arcelin", "lat": 52.6450, "lon": 20.4050, "crop": "Pszenica ozima", "soil": "Gleba brunatna (klasa IIIb)"},
        {"id_tag": "Baboszewo", "lat": 52.6950, "lon": 20.3150, "crop": "Rzepak ozimy", "soil": "Gleba brunatna (klasa IIIb)"},
    ],
    "Opolskie • Głubczyce (pow. głubczycki)": [
        {"id_tag": "Gołuszowice", "lat": 50.2150, "lon": 17.8450, "crop": "Pszenica ozima", "soil": "Czarnoziem (klasa I)"},
        {"id_tag": "Nowa Cerekwia", "lat": 50.1450, "lon": 17.8850, "crop": "Kukurydza na ziarno", "soil": "Czarnoziem (klasa I-II)"},
    ],
    "Podkarpackie • Jarosław (pow. jarosławski)": [
        {"id_tag": "Wiązownica", "lat": 50.0550, "lon": 22.7550, "crop": "Pszenica ozima", "soil": "Mada pyłowa (klasa IIIa)"},
        {"id_tag": "Makowisko", "lat": 50.0350, "lon": 22.7150, "crop": "Rzepak ozimy", "soil": "Gleba brunatna (klasa IIIb)"},
    ],
    "Podlaskie • Wysokie Mazowieckie (pow. wysokomazowiecki)": [
        {"id_tag": "Szepietowo", "lat": 52.8850, "lon": 22.5350, "crop": "Kukurydza na kiszonkę", "soil": "Piaszczysto-gliniasta (klasa IVa)"},
        {"id_tag": "Dolina Broku", "lat": 52.8920, "lon": 22.5450, "crop": "Trwały użytek zielony (łąka kośna)", "soil": "Gleba torfowa"},
    ],
    "Pomorskie • Nowy Dwór Gdański (Żuławy Wiślane)": [
        {"id_tag": "Żuławy", "lat": 54.2250, "lon": 19.1250, "crop": "Pszenica ozima", "soil": "Mada ciężka (klasa I)"},
        {"id_tag": "Kępki", "lat": 54.2320, "lon": 19.1350, "crop": "Rzepak ozimy", "soil": "Mada próchniczna (klasa II)"},
    ],
    "Śląskie • Racibórz (pow. raciborski)": [
        {"id_tag": "Pietrowice Wielkie", "lat": 50.0720, "lon": 18.0550, "crop": "Pszenica ozima", "soil": "Czarnoziem (klasa II)"},
        {"id_tag": "Samborowice", "lat": 50.0400, "lon": 18.0850, "crop": "Buraki cukrowe", "soil": "Gleba brunatna pyłowa (klasa II-IIIa)"},
    ],
    "Świętokrzyskie • Opatów (pow. opatowski)": [
        {"id_tag": "Lipnik", "lat": 50.7850, "lon": 21.4450, "crop": "Pszenica ozima", "soil": "Less (klasa II-IIIa)"},
        {"id_tag": "Opatów wschód", "lat": 50.7920, "lon": 21.4550, "crop": "Rzepak ozimy", "soil": "Gleba lessowa (klasa II)"},
    ],
    "Warmińsko-Mazurskie • Kętrzyn (pow. kętrzyński)": [
        {"id_tag": "Reszel", "lat": 54.0650, "lon": 21.3550, "crop": "Rzepak ozimy", "soil": "Gleba brunatna (klasa IIIb)"},
        {"id_tag": "Barciany", "lat": 54.2150, "lon": 21.3450, "crop": "Pszenica ozima", "soil": "Glina morenowa (klasa IIIa)"},
    ],
    "Zachodniopomorskie • Pyrzyce (pow. pyrzycki)": [
        {"id_tag": "Pyrzyce Wieś", "lat": 53.1480, "lon": 14.8650, "crop": "Pszenica ozima", "soil": "Czarne ziemie (klasa I-II)"},
        {"id_tag": "Kozielice", "lat": 53.1150, "lon": 14.7750, "crop": "Buraki cukrowe", "soil": "Czarne ziemie (klasa I-II)"},
    ],
}


def _load_verified(path: str = PRESETS_JSON) -> Dict[str, List[Dict[str, Any]]]:
    """Gospodarstwa z pliku JSON (tylko znane nazwy i poprawne punkty); pusty słownik, gdy pliku nie ma."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            farms = json.load(f).get("farms", {})
    except (OSError, ValueError, AttributeError) as exc:
        log.warning("Nie wczytano %s (%s) – używam punktów startowych", path, exc)
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for name, items in farms.items():
        if name not in FALLBACK_PRESETS or not isinstance(items, list):
            continue
        ok = [it for it in items if isinstance(it, dict) and isinstance(it.get("lat"), (int, float))
              and isinstance(it.get("lon"), (int, float)) and 49.0 <= it["lat"] <= 55.0 and 14.0 <= it["lon"] <= 24.2]
        if ok:
            out[name] = [{k: it[k] for k in ("id_tag", "lat", "lon", "crop", "soil", "parcel_id") if it.get(k) is not None}
                         for it in ok]
    return out


def build_presets(path: str = PRESETS_JSON) -> Dict[str, List[Dict[str, Any]]]:
    verified = _load_verified(path)
    return {name: verified.get(name) or [dict(it) for it in items] for name, items in FALLBACK_PRESETS.items()}


FARM_PRESETS: Dict[str, List[Dict[str, Any]]] = build_presets()

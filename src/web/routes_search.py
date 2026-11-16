"""Wyszukiwarka miejscowości i działek ewidencyjnych. Właściciel: bezpieczeństwo/warstwa web.

Kolejność: identyfikator działki (GUGiK ULDK) → lokalny indeks miejscowości → Photon (OSM) → Nominatim (OSM).
Wyniki zewnętrzne trafiają do TTLCache (SQLite/JSON, 7 dni). Każda nazwa jest czyszczona po stronie serwera.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Query

from src.core.cache import TTLCache
from src.core.config import settings

log = logging.getLogger(__name__)
router = APIRouter(tags=["wyszukiwarka"])

APP_UA_VERSION = "2.1"
_CACHE = TTLCache("geo_search", max_entries=5000, default_ttl=7 * 24 * 3600)
_MAX_RESULTS = 7
_NAME_MAX = 200
# granice z kontraktu (Polska z marginesem)
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 49.0, 55.0, 14.0, 24.2
PHOTON_URL = "https://photon.komoot.io/api/"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# identyfikator działki TERYT, np. 302505_5.0014.114/1 lub 141201_1.0001.AR_1.123/4
_PARCEL_ID_RE = re.compile(r"^\d{6}_\d\.\d{4}(\.[A-Za-z0-9_-]{1,12})?\.[0-9A-Za-z/-]{1,20}$")

_nominatim_lock = threading.Lock()
_nominatim_last = 0.0
_ua_warned = False

_LOCAL_PLACES = [
    {"name": "Śnieciska", "display_name": "Śnieciska, gm. Zaniemyśl, pow. średzki, wielkopolskie", "lat": 52.2020, "lon": 17.2012},
    {"name": "Kruszwica", "display_name": "Kruszwica, pow. inowrocławski, kujawsko-pomorskie", "lat": 52.6650, "lon": 18.3600},
    {"name": "Krasnystaw", "display_name": "Krasnystaw, pow. krasnostawski, lubelskie", "lat": 50.9950, "lon": 23.2350},
    {"name": "Strzelin", "display_name": "Strzelin, pow. strzeliński, dolnośląskie", "lat": 50.7950, "lon": 17.0750},
    {"name": "Sulechów", "display_name": "Sulechów, pow. zielonogórski, lubuskie", "lat": 52.0850, "lon": 15.5850},
    {"name": "Kutno", "display_name": "Kutno, pow. kutnowski, łódzkie", "lat": 52.2650, "lon": 19.3850},
    {"name": "Proszowice", "display_name": "Proszowice, pow. proszowicki, małopolskie", "lat": 50.1850, "lon": 20.3150},
    {"name": "Płońsk", "display_name": "Płońsk, pow. płoński, mazowieckie", "lat": 52.6450, "lon": 20.4050},
    {"name": "Głubczyce", "display_name": "Głubczyce, pow. głubczycki, opolskie", "lat": 50.2150, "lon": 17.8450},
    {"name": "Jarosław", "display_name": "Jarosław, pow. jarosławski, podkarpackie", "lat": 50.0550, "lon": 22.7550},
    {"name": "Wysokie Mazowieckie", "display_name": "Wysokie Mazowieckie, pow. wysokomazowiecki, podlaskie", "lat": 52.8850, "lon": 22.5350},
    {"name": "Nowy Dwór Gdański", "display_name": "Nowy Dwór Gdański, Żuławy Wiślane, pomorskie", "lat": 54.2250, "lon": 19.1250},
    {"name": "Racibórz", "display_name": "Racibórz, pow. raciborski, śląskie", "lat": 50.0720, "lon": 18.0550},
    {"name": "Opatów", "display_name": "Opatów, pow. opatowski, świętokrzyskie", "lat": 50.7850, "lon": 21.4450},
    {"name": "Kętrzyn", "display_name": "Kętrzyn, pow. kętrzyński, warmińsko-mazurskie", "lat": 54.0650, "lon": 21.3550},
    {"name": "Pyrzyce", "display_name": "Pyrzyce, pow. pyrzycki, zachodniopomorskie", "lat": 53.1480, "lon": 14.8650},
    {"name": "Warszawa", "display_name": "Warszawa, woj. mazowieckie", "lat": 52.2297, "lon": 21.0122},
    {"name": "Kraków", "display_name": "Kraków, woj. małopolskie", "lat": 50.0647, "lon": 19.9450},
    {"name": "Poznań", "display_name": "Poznań, woj. wielkopolskie", "lat": 52.4064, "lon": 16.9252},
    {"name": "Wrocław", "display_name": "Wrocław, woj. dolnośląskie", "lat": 51.1079, "lon": 17.0385},
    {"name": "Łódź", "display_name": "Łódź, woj. łódzkie", "lat": 51.7592, "lon": 19.4560},
    {"name": "Gdańsk", "display_name": "Gdańsk, woj. pomorskie", "lat": 54.3520, "lon": 18.6466},
    {"name": "Szczecin", "display_name": "Szczecin, woj. zachodniopomorskie", "lat": 53.4285, "lon": 14.5528},
    {"name": "Bydgoszcz", "display_name": "Bydgoszcz, woj. kujawsko-pomorskie", "lat": 53.1235, "lon": 18.0084},
    {"name": "Lublin", "display_name": "Lublin, woj. lubelskie", "lat": 51.2465, "lon": 22.5684},
    {"name": "Białystok", "display_name": "Białystok, woj. podlaskie", "lat": 53.1325, "lon": 23.1688},
    {"name": "Katowice", "display_name": "Katowice, woj. śląskie", "lat": 50.2649, "lon": 19.0238},
    {"name": "Rzeszów", "display_name": "Rzeszów, woj. podkarpackie", "lat": 50.0412, "lon": 21.9991},
    {"name": "Kielce", "display_name": "Kielce, woj. świętokrzyskie", "lat": 50.8661, "lon": 20.6286},
    {"name": "Opole", "display_name": "Opole, woj. opolskie", "lat": 50.6751, "lon": 17.9213},
    {"name": "Olsztyn", "display_name": "Olsztyn, woj. warmińsko-mazurskie", "lat": 53.7784, "lon": 20.4801},
    {"name": "Gorzów Wielkopolski", "display_name": "Gorzów Wielkopolski, woj. lubuskie", "lat": 52.7368, "lon": 15.2288},
    {"name": "Zielona Góra", "display_name": "Zielona Góra, woj. lubuskie", "lat": 51.9356, "lon": 15.5062},
    {"name": "Toruń", "display_name": "Toruń, woj. kujawsko-pomorskie", "lat": 53.0138, "lon": 18.5984},
    {"name": "Gdynia", "display_name": "Gdynia, woj. pomorskie", "lat": 54.5189, "lon": 18.5305},
    {"name": "Częstochowa", "display_name": "Częstochowa, woj. śląskie", "lat": 50.8118, "lon": 19.1203},
    {"name": "Radom", "display_name": "Radom, woj. mazowieckie", "lat": 51.4027, "lon": 21.1471},
    {"name": "Sosnowiec", "display_name": "Sosnowiec, woj. śląskie", "lat": 50.2863, "lon": 19.1041},
    {"name": "Kalisz", "display_name": "Kalisz, woj. wielkopolskie", "lat": 51.7611, "lon": 18.0910},
    {"name": "Gniezno", "display_name": "Gniezno, woj. wielkopolskie", "lat": 52.5348, "lon": 17.5826},
    {"name": "Środa Wielkopolska", "display_name": "Środa Wielkopolska, woj. wielkopolskie", "lat": 52.2272, "lon": 17.2747},
    {"name": "Inowrocław", "display_name": "Inowrocław, woj. kujawsko-pomorskie", "lat": 52.7936, "lon": 18.2588},
    {"name": "Sandomierz", "display_name": "Sandomierz, woj. świętokrzyskie", "lat": 50.6823, "lon": 21.7489},
    {"name": "Zamość", "display_name": "Zamość, woj. lubelskie", "lat": 50.7231, "lon": 23.2519},
    {"name": "Chełm", "display_name": "Chełm, woj. lubelskie", "lat": 51.1396, "lon": 23.4735},
    {"name": "Łomża", "display_name": "Łomża, woj. podlaskie", "lat": 53.1781, "lon": 22.0594},
    {"name": "Suwałki", "display_name": "Suwałki, woj. podlaskie", "lat": 54.0991, "lon": 22.9249},
    {"name": "Słupsk", "display_name": "Słupsk, woj. pomorskie", "lat": 54.4641, "lon": 17.0287},
    {"name": "Koszalin", "display_name": "Koszalin, woj. zachodniopomorskie", "lat": 54.1943, "lon": 16.1722},
    {"name": "Stargard", "display_name": "Stargard, woj. zachodniopomorskie", "lat": 53.3386, "lon": 15.0450},
    {"name": "Elbląg", "display_name": "Elbląg, woj. warmińsko-mazurskie", "lat": 54.1522, "lon": 19.4088},
    {"name": "Ełk", "display_name": "Ełk, woj. warmińsko-mazurskie", "lat": 53.8282, "lon": 22.3615},
    {"name": "Nysa", "display_name": "Nysa, woj. opolskie", "lat": 50.4738, "lon": 17.3344},
    {"name": "Brzeg", "display_name": "Brzeg, woj. opolskie", "lat": 50.8606, "lon": 17.4674},
    {"name": "Przemyśl", "display_name": "Przemyśl, woj. podkarpackie", "lat": 49.7839, "lon": 22.7678},
    {"name": "Mielec", "display_name": "Mielec, woj. podkarpackie", "lat": 50.2871, "lon": 21.4239},
    {"name": "Tarnów", "display_name": "Tarnów, woj. małopolskie", "lat": 50.0121, "lon": 20.9858},
    {"name": "Nowy Sącz", "display_name": "Nowy Sącz, woj. małopolskie", "lat": 49.6218, "lon": 20.6971},
    {"name": "Piotrków Trybunalski", "display_name": "Piotrków Trybunalski, woj. łódzkie", "lat": 51.4052, "lon": 19.7030},
    {"name": "Sieradz", "display_name": "Sieradz, woj. łódzkie", "lat": 51.5962, "lon": 18.7303},
    {"name": "Ostrowiec Świętokrzyski", "display_name": "Ostrowiec Świętokrzyski, woj. świętokrzyskie", "lat": 50.9294, "lon": 21.3853},
    {"name": "Starachowice", "display_name": "Starachowice, woj. świętokrzyskie", "lat": 51.0526, "lon": 21.0694},
    {"name": "Legnica", "display_name": "Legnica, woj. dolnośląskie", "lat": 51.2070, "lon": 16.1553},
    {"name": "Wałbrzych", "display_name": "Wałbrzych, woj. dolnośląskie", "lat": 50.7818, "lon": 16.2842},
    {"name": "Świdnica", "display_name": "Świdnica, woj. dolnośląskie", "lat": 50.8438, "lon": 16.4886}
]


def _normalize_pl(text: str) -> str:
    pl_map = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
    return text.translate(pl_map).lower().strip()


def strip_control(text: str) -> str:
    """Znaki sterujące (Cc) zamienia na spację, formatujące (Cf, np. U+202E) usuwa."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Cc":
            out.append(" ")
        elif cat != "Cf":
            out.append(ch)
    return "".join(out)


def clean_name(value: Any, limit: int = _NAME_MAX) -> str:
    """Nazwa z zewnętrznego źródła (OSM jest edytowalny przez każdego): bez < >, znaków sterujących,
    z jedną spacją między słowami i ograniczoną długością. Frontend dodatkowo wstawia ją przez textContent."""
    text = strip_control(str(value or "")).replace("<", "").replace(">", "")
    return " ".join(text.split())[:limit]


def _coords(lat: Any, lon: Any) -> Optional[tuple]:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(la) and math.isfinite(lo)):
        return None
    if not (LAT_MIN <= la <= LAT_MAX and LON_MIN <= lo <= LON_MAX):
        return None
    return round(la, 6), round(lo, 6)


def _result(name: Any, lat: Any, lon: Any, **extra: Any) -> Optional[Dict[str, Any]]:
    c = _coords(lat, lon)
    disp = clean_name(name)
    if not c or not disp:
        return None
    return {"display_name": disp, "lat": c[0], "lon": c[1], "is_parcel": False, **extra}


def _user_agent() -> str:
    global _ua_warned
    contact = settings.contact_email
    if not contact and not _ua_warned:
        _ua_warned = True
        log.warning("Brak MROLNIK_CONTACT_EMAIL – polityka Nominatim wymaga kontaktu w User-Agent.")
    return f"mRolnik/{APP_UA_VERSION} (+{contact or 'prototyp HackNation, kontakt nieustawiony'})"


def _http_get_json(url: str, params: Dict[str, Any], timeout: float) -> Any:
    """Jedno miejsce wywołań HTTP (łatwe do podmiany w testach)."""
    headers = {"User-Agent": _user_agent(), "Accept": "application/json", "Accept-Language": "pl"}
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _search_parcel(parcel_id: str) -> List[Dict[str, Any]]:
    # identyfikator przeszedł walidację regexem (bez &, =, ?, spacji) – nie da się nim doklejać parametrów do ULDK
    from src.data.cadastre import fetch_parcel_by_id

    try:
        parcel = fetch_parcel_by_id(parcel_id)
    except Exception:
        log.exception("ULDK: błąd pobierania działki")
        return []
    if not parcel:
        return []
    get = parcel.get if isinstance(parcel, dict) else (lambda k, d=None: getattr(parcel, k, d))
    pid = clean_name(get("parcel_id") or parcel_id, 60)
    commune, county, area = clean_name(get("commune"), 60), clean_name(get("county"), 60), get("area_ha")
    if county and county.lower().startswith("powiat "):  # ULDK zwraca "powiat średzki" → "pow. średzki"
        county = county[len("powiat "):].strip()
    label = f"Działka {pid}"
    place = ", ".join(x for x in (commune, f"pow. {county}" if county else "") if x)
    if place:
        label += f" ({place})"
    if isinstance(area, (int, float)) and math.isfinite(area):
        label += f" • {area:.2f} ha"
    r = _result(label, get("centroid_lat"), get("centroid_lon"), parcel_id=pid, is_parcel=True,
                area_ha=area if isinstance(area, (int, float)) else None)
    return [r] if r else []


def _search_local(query: str) -> List[Dict[str, Any]]:
    q_norm = _normalize_pl(query)
    if len(q_norm) < 2:
        return []
    out = []
    for p in _LOCAL_PLACES:
        if _normalize_pl(p["name"]).startswith(q_norm) or q_norm in _normalize_pl(p["display_name"]):
            out.append({"display_name": p["display_name"], "lat": p["lat"], "lon": p["lon"], "is_parcel": False})
            if len(out) >= 6:
                break
    return out


def _search_photon(query: str) -> List[Dict[str, Any]]:
    try:
        data = _http_get_json(PHOTON_URL, {"q": query, "bbox": f"{LON_MIN},{LAT_MIN},{LON_MAX},{LAT_MAX}",
                                           "limit": _MAX_RESULTS}, timeout=2.5)
    except (requests.RequestException, ValueError) as exc:
        log.warning("Photon niedostępny: %s", type(exc).__name__)
        return []
    out = []
    for f in (data or {}).get("features", [])[: _MAX_RESULTS * 2]:
        p = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or []
        name = p.get("name")
        if len(coords) < 2 or not name:
            continue
        details = []
        if p.get("city") and p.get("city") != name:
            details.append(f"gm. {p['city']}")
        details += [p[k] for k in ("county", "state") if p.get(k)]
        r = _result(", ".join([str(name)] + [str(d) for d in details]), coords[1], coords[0])
        if r:
            out.append(r)
    return out[:_MAX_RESULTS]


def _search_nominatim(query: str) -> List[Dict[str, Any]]:
    """Polityka Nominatim: maks. 1 żądanie/s (globalnie), identyfikowalny User-Agent, wyniki cache'owane."""
    global _nominatim_last
    params = {"q": query, "format": "jsonv2", "countrycodes": "pl", "limit": 5}
    if settings.contact_email:
        params["email"] = settings.contact_email
    with _nominatim_lock:
        wait = 1.0 - (time.monotonic() - _nominatim_last)
        if wait > 0:
            time.sleep(wait)
        try:
            items = _http_get_json(NOMINATIM_URL, params, timeout=3.0)
        except (requests.RequestException, ValueError) as exc:
            log.warning("Nominatim niedostępny: %s", type(exc).__name__)
            items = []
        finally:
            _nominatim_last = time.monotonic()
    out = []
    for it in items if isinstance(items, list) else []:
        r = _result(it.get("display_name"), it.get("lat"), it.get("lon"))
        if r:
            out.append(r)
    return out[:_MAX_RESULTS]


@router.get("/api/search-location")
def api_search_location(q: str = Query(..., min_length=1, max_length=100, description="Miejscowość lub nr działki")):
    """Wyszukiwarka miejscowości, wsi, gmin i działek ewidencyjnych."""
    query = " ".join(strip_control(q).split())
    if not query:
        raise HTTPException(status_code=422, detail="Podaj nazwę miejscowości lub numer działki.")

    if _PARCEL_ID_RE.match(query):
        key = "p:" + query
        cached = _CACHE.get(key)
        if cached is not None:
            return {"results": cached, "cached": True, "source": "uldk"}
        results = _search_parcel(query)
        if results:
            _CACHE.set(key, results)
        return {"results": results, "cached": False, "source": "uldk"}

    local = _search_local(query)
    if local:
        return {"results": local, "cached": False, "source": "lokalny"}

    # klucz to skrót zapytania – w cache nie przechowujemy wpisanych fraz jawnym tekstem
    key = "q:" + hashlib.sha256(_normalize_pl(query).encode("utf-8")).hexdigest()
    cached = _CACHE.get(key)
    if cached is not None:
        return {"results": cached, "cached": True, "source": "osm"}
    results = _search_photon(query) or _search_nominatim(query)
    if results:
        _CACHE.set(key, results)
    return {"results": results, "cached": False, "source": "osm"}

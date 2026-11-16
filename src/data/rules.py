"""Progi i terminy z przepisów (MRiRW / Rada Ministrów) do zaznaczania na wykresach i w kalendarzu zabiegów.

Tylko wartości sprawdzone w tekstach aktów prawnych (ISAP / Dziennik Ustaw, 15.11.2026):

1. Środki ochrony roślin – Rozporządzenie MRiRW z 31.03.2014 r. w sprawie warunków stosowania środków ochrony roślin
   (Dz.U. 2014 poz. 516) w brzmieniu nadanym rozporządzeniem MRiRW z 12.03.2026 r. (Dz.U. 2026 poz. 353, w mocy od 1.04.2026):
   § 3: sprzęt naziemny – prędkość wiatru do 5 m/s przy technice ograniczającej znoszenie cieczy o co najmniej 75 %,
   do 4 m/s w pozostałych przypadkach; sprzęt agrolotniczy – do 4 m/s. (Akt NIE określa progu temperatury.)

2. Nawożenie azotem – „Program azotanowy”, Rozporządzenie Rady Ministrów z 31.01.2023 r. (Dz.U. 2023 poz. 244), rozdz. 1.3:
   - tabela 2: nawozy azotowe mineralne i naturalne płynne na gruntach ornych 1 marca – 20 października
     (15 października w gminach z tabeli 4, 25 października w gminach z tabeli 5); nawozy naturalne stałe 1 marca – 31 października;
     uprawy trwałe, wieloletnie i TUZ: 1 marca – 31 października (stałe naturalne do 30 listopada),
   - pkt 4–6: wcześniej, od 1 lutego, jeżeli średnia dobowa temperatura powietrza przez 5 kolejnych dni przekracza 3°C
     (rośliny ozime, uprawy trwałe i wieloletnie, TUZ) lub 5°C (pozostałe uprawy); datę przejścia progu dla powiatu podaje IMGW-PIB,
   - pkt 1.2.1: zakaz stosowania nawozów na glebach zamarzniętych, zalanych wodą, nasyconych wodą lub przykrytych śniegiem
     (art. 20 ustawy o nawozach i nawożeniu).
   Wykazy gmin (tabele 4 i 5) – src/data/nitrates_gminy.json (skrypt scripts/build_nitrates_gminy.py).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.cache import TTLCache
from src.data.common import (
    SOURCE_OM_FORECAST,
    SourceError,
    friendly,
    http_get_json,
    iso_utc,
    missing,
    provenance,
    record_error,
    record_ok,
    to_float,
)
from src.data.weather import OPEN_METEO_FORECAST_URL, cluster_key, poland_now

log = logging.getLogger(__name__)

NITRATES_PATH = Path(__file__).with_name("nitrates_gminy.json")
SPRAY_ACT_URL = "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20260000353"
SPRAY_BASE_URL = "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20140000516"
NITRATES_ACT_URL = "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20230000244"

SPRAY_WIND_MAX_MS = 4.0
SPRAY_WIND_MAX_DRIFT_MS = 5.0
NITRATE_TEMP_WINTER_C = 3.0
NITRATE_TEMP_OTHER_C = 5.0
NITRATE_CONSECUTIVE_DAYS = 5

# Progi siewu – ZALECENIA DORADZTWA ROLNICZEGO (ODR), nie przepisy. Każdy próg sprawdzony w tekście dokumentu źródłowego.
SOWING_THRESHOLDS: List[Dict[str, Any]] = [
    {"crop": "kukurydza", "keys": ("kukurydz",), "min_c": 8.0, "max_c": 10.0,
     "depth": "na głębokości siewu (ok. 5–6 cm)",
     "text": "temperatura gleby powinna wynosić minimalnie 8–10°C na głębokości siewu; najlepszy termin ok. 15 kwietnia",
     "source": "Pomorski Ośrodek Doradztwa Rolniczego w Lubaniu, Oddział Stare Pole – materiały konferencji „Nowe wyzwania w produkcji kukurydzy”, 27.01.2022",
     "url": "https://new.podr.pl/wp-content/uploads/2022/02/PODR-Stare-Pole-2022.pdf"},
    {"crop": "soja", "keys": ("soja", "soi"), "min_c": 10.0, "max_c": None,
     "depth": "górna warstwa gleby",
     "text": "optymalny termin siewu – ostatnia dekada kwietnia i pierwsza dekada maja, kiedy górna warstwa gleby osiągnie 10°C",
     "source": "Wielkopolski Ośrodek Doradztwa Rolniczego w Poznaniu (J. Kałużny), „Uprawa soi jako źródło krajowego dobrego białka” (gov.pl)",
     "url": "https://www.gov.pl/attachment/f79cbf5d-172b-4674-9b7c-7e3ba5f1ad47"},
]
SOWING_NOT_FOUND = ("Dla buraka cukrowego, zbóż jarych i ziemniaków nie znaleźliśmy progu temperatury gleby w dokumentach ODR "
                    "(tylko w poradnikach komercyjnych i prasie), więc ich nie pokazujemy. Progów zbioru (np. wilgotności ziarna) "
                    "nie ma w prognozie – nie mierzymy wilgotności ziarna.")

FIELD_TTL_S = 30 * 60
_field_cache = TTLCache("rules_field", max_entries=5000, default_ttl=FIELD_TTL_S)

AUTUMN_SOWN = ("ozim", "rzepak", "tuz", "trwały użytek", "trwaly uzytek", "łąk", "lak", "pastwisk", "sad", "wieloletn")


@lru_cache(maxsize=1)
def _nitrate_lists() -> Dict[str, List[str]]:
    try:
        d = json.loads(NITRATES_PATH.read_text(encoding="utf-8"))
        return {"oct15": d.get("oct15", []), "oct25": d.get("oct25", [])}
    except (OSError, ValueError) as exc:
        log.warning("Brak wykazu gmin programu azotanowego: %s", exc)
        return {"oct15": [], "oct25": []}


def gmina_code(parcel_id: Optional[str]) -> Optional[str]:
    """Identyfikator działki ULDK „302505_5.0014.114/1” → 6 cyfr gminy (woj., powiat, nr gminy) „302505”.

    Rodzaj gminy (ostatnia cyfra TERYT) pomijamy: zmienia się np. po nadaniu praw miejskich, a numer gminy w powiecie nie."""
    s = (parcel_id or "").strip()
    return s[:6] if len(s) >= 6 and s[:6].isdigit() else None


def nitrate_deadline_day(gmina6: Optional[str]) -> Dict[str, Any]:
    """Termin końcowy dla nawozów azotowych mineralnych i naturalnych płynnych na gruntach ornych (dzień października)."""
    lists = _nitrate_lists()
    if gmina6:
        if any(c[:6] == gmina6 for c in lists["oct15"]):
            return {"day": 15, "basis": "gmina z załącznika nr 2 (tabela 4) – termin 15 października"}
        if any(c[:6] == gmina6 for c in lists["oct25"]):
            return {"day": 25, "basis": "gmina z załącznika nr 3 (tabela 5) – termin 25 października"}
        return {"day": 20, "basis": "gmina spoza załączników nr 2 i 3 – termin podstawowy 20 października"}
    return {"day": 20, "basis": "nie ustalono gminy działki – podano termin podstawowy 20 października (sprawdź załączniki nr 2 i 3)"}


def is_autumn_or_perennial(crop: Optional[str]) -> bool:
    c = (crop or "").lower()
    return any(k in c for k in AUTUMN_SOWN)


def nitrates_status(today: date, gmina6: Optional[str], crop: Optional[str] = None) -> Dict[str, Any]:
    """Okna stosowania nawozów azotowych w bieżącym roku i stan „dziś” wg programu azotanowego."""
    dl = nitrate_deadline_day(gmina6)
    y = today.year
    mineral_end = date(y, 10, dl["day"])
    solid_end = date(y, 10, 31)
    start = date(y, 3, 1)
    feb_start = date(y, 2, 1)
    perennial = is_autumn_or_perennial(crop)
    threshold = NITRATE_TEMP_WINTER_C if perennial else NITRATE_TEMP_OTHER_C
    if start <= today <= mineral_end:
        status, text = "dozwolone", (f"Termin trwa: nawozy azotowe mineralne i naturalne płynne na gruntach ornych można stosować do "
                                     f"{dl['day']} października – pod warunkiem, że gleba nie jest zamarznięta, zalana, nasycona wodą ani przykryta śniegiem.")
    elif mineral_end < today <= solid_end:
        status, text = "tylko_stale", (f"Termin dla nawozów azotowych mineralnych i naturalnych płynnych minął {dl['day']} października; "
                                        "nawozy naturalne stałe (obornik) – do 31 października.")
    elif feb_start <= today < start:
        status, text = "luty_warunkowo", (f"Luty: nawożenie azotem możliwe wcześniej tylko po przejściu średniej dobowej temperatury przez "
                                          f"{threshold:.0f}°C przez {NITRATE_CONSECUTIVE_DAYS} kolejnych dni (datę dla powiatu ogłasza IMGW-PIB).")
    else:
        nxt = date(y + (today > solid_end), 3, 1)
        status, text = "zakaz", (f"Poza terminem: nawozów azotowych na gruntach ornych nie stosuje się do 1 marca {nxt.year} r. "
                                 f"(od 1 lutego – po przejściu temperatury przez próg {threshold:.0f}°C przez {NITRATE_CONSECUTIVE_DAYS} dni).")
    return {
        "today": today.isoformat(),
        "status": status,
        "text": text,
        "gmina_teryt6": gmina6,
        "deadline_basis": dl["basis"],
        "windows": [
            {"label": "Nawozy azotowe mineralne i naturalne płynne (grunty orne)", "from": f"{y}-03-01", "to": mineral_end.isoformat()},
            {"label": "Nawozy naturalne stałe (grunty orne)", "from": f"{y}-03-01", "to": solid_end.isoformat()},
            {"label": f"Luty – warunkowo (próg {threshold:.0f}°C przez {NITRATE_CONSECUTIVE_DAYS} dni)", "from": f"{y}-02-01",
             "to": (start - timedelta(days=1)).isoformat(), "conditional": True},
        ],
        "temp_threshold_c": threshold,
        "temp_threshold_basis": ("rośliny ozime / uprawy trwałe i wieloletnie / TUZ – próg 3°C" if perennial
                                 else "pozostałe uprawy (np. jare) – próg 5°C"),
        "provenance": provenance(
            "Program azotanowy – Rozporządzenie Rady Ministrów z 31.01.2023 r. (Dz.U. 2023 poz. 244)", NITRATES_ACT_URL, "przepis",
            note=("Tabela 2 (terminy stosowania nawozów), pkt 4–6 rozdz. 1.3 (wcześniejsze stosowanie od 1 lutego po przejściu "
                  "średniej dobowej temperatury przez 3°C lub 5°C przez 5 kolejnych dni; datę dla powiatu określa IMGW-PIB), "
                  "pkt 1.2.1 (zakaz na glebach zamarzniętych, zalanych, nasyconych wodą lub przykrytych śniegiem). "
                  f"Gmina działki: {dl['basis']}. To informacja pomocnicza – wiążący jest tekst rozporządzenia."),
        ),
    }


def spraying_rules() -> Dict[str, Any]:
    return {
        "wind_max_ms": SPRAY_WIND_MAX_MS,
        "wind_max_drift_reduction_ms": SPRAY_WIND_MAX_DRIFT_MS,
        "provenance": provenance(
            "Rozporządzenie MRiRW w sprawie warunków stosowania środków ochrony roślin (Dz.U. 2014 poz. 516, zm. Dz.U. 2026 poz. 353)",
            SPRAY_ACT_URL, "przepis",
            note=("§ 3: sprzętem naziemnym na terenie otwartym – przy prędkości wiatru do 4 m/s, a do 5 m/s przy technice "
                  "ograniczającej znoszenie cieczy użytkowej o co najmniej 75 % (np. opryskiwacze z pomocniczym strumieniem "
                  "powietrza); sprzętem agrolotniczym – do 4 m/s. Brzmienie od 1.04.2026 r. Rozporządzenie nie określa progu "
                  "temperatury – wymagania co do temperatury podaje etykieta środka. Prognoza wiatru dotyczy wysokości 10 m, "
                  "przy belce opryskiwacza wiatr jest zwykle słabszy – decyduje pomiar w chwili zabiegu."),
        ),
    }


# --------------------------------------------------------------------------------------
# Prognoza godzinowa wiatru + średnia dobowa temperatura (do wykresów progów)
# --------------------------------------------------------------------------------------
def _fetch_field_weather(lat: float, lon: float) -> Dict[str, Any]:
    clat, clon = cluster_key(lat, lon)
    key = f"{clat:.3f},{clon:.3f}"
    cached = _field_cache.get(key)
    if cached:
        return cached
    try:
        data = http_get_json(OPEN_METEO_FORECAST_URL, params={
            "latitude": f"{clat:.3f}", "longitude": f"{clon:.3f}",
            "hourly": "wind_speed_10m,wind_gusts_10m,soil_temperature_6cm",
            "daily": "temperature_2m_mean,temperature_2m_min",
            "past_days": 7, "forecast_days": 14, "wind_speed_unit": "ms", "timezone": "Europe/Warsaw",
        }, timeout=8.0, retries=1)
        if not isinstance(data, dict) or not isinstance((data.get("hourly") or {}).get("time"), list):
            raise SourceError("Open-Meteo: niepoprawna odpowiedź")
    except SourceError as exc:
        record_error(SOURCE_OM_FORECAST, str(exc))
        raise
    record_ok(SOURCE_OM_FORECAST)
    hourly, daily = data["hourly"], data.get("daily") or {}
    soil: Dict[str, List[float]] = {}
    for t, v in zip(hourly["time"], hourly.get("soil_temperature_6cm") or []):
        f = to_float(v)
        if f is not None:
            soil.setdefault(str(t)[:10], []).append(f)
    res = {
        "soil_daily": [{"date": d, "soil_t_c": round(sum(v) / len(v), 1), "hours": len(v)} for d, v in sorted(soil.items()) if len(v) >= 12],
        "hourly": [{"time": t, "wind_ms": to_float(w), "gust_ms": to_float(g)}
                   for t, w, g in zip(hourly["time"], hourly.get("wind_speed_10m") or [], hourly.get("wind_gusts_10m") or [])],
        "daily": [{"date": d, "t_mean_c": to_float(m), "t_min_c": to_float(n)}
                  for d, m, n in zip(daily.get("time") or [], daily.get("temperature_2m_mean") or [],
                                     daily.get("temperature_2m_min") or [])],
        "fetched_at": iso_utc(),
    }
    _field_cache.set(key, res, FIELD_TTL_S)
    return res


def spray_windows(hourly: List[Dict[str, Any]], now_local: datetime, hours: int = 120) -> List[Dict[str, Any]]:
    """Godziny dzienne (6–20) w najbliższych `hours` godzinach z oceną wiatru względem § 3 rozporządzenia."""
    out = []
    for h in hourly:
        try:
            t = datetime.strptime(h["time"], "%Y-%m-%dT%H:%M")
        except (ValueError, TypeError, KeyError):
            continue
        if t < now_local.replace(minute=0, second=0, microsecond=0) or t > now_local + timedelta(hours=hours):
            continue
        w = h.get("wind_ms")
        ok = None if w is None else ("tak" if w <= SPRAY_WIND_MAX_MS else ("technika_75" if w <= SPRAY_WIND_MAX_DRIFT_MS else "nie"))
        out.append({"time": h["time"], "wind_ms": w, "gust_ms": h.get("gust_ms"), "daylight": 6 <= t.hour <= 20, "allowed": ok})
    return out


def temperature_threshold_series(daily: List[Dict[str, Any]], threshold: float) -> Dict[str, Any]:
    """Średnie dobowe temperatury i pierwszy dzień, w którym zaczyna się 5-dniowa seria powyżej progu (program azotanowy, pkt 5)."""
    run, first = 0, None
    for i, d in enumerate(daily):
        t = d.get("t_mean_c")
        run = run + 1 if (t is not None and t > threshold) else 0
        if run >= NITRATE_CONSECUTIVE_DAYS and first is None:
            first = daily[i - NITRATE_CONSECUTIVE_DAYS + 1]["date"]
    return {"threshold_c": threshold, "first_5day_run_start": first}


def sowing_threshold_for(crop: Optional[str]) -> Optional[Dict[str, Any]]:
    c = (crop or "").lower()
    return next((t for t in SOWING_THRESHOLDS if any(k in c for k in t["keys"])), None)


def sowing_block(soil_daily: List[Dict[str, Any]], crop: Optional[str], today: str, fetched_at: Optional[str]) -> Dict[str, Any]:
    """Temperatura gleby (6 cm, model) na tle progów siewu ODR; pierwszy dzień ≥ progu dla uprawy działki."""
    thr = sowing_threshold_for(crop)
    first = None
    if thr:
        first = next((d["date"] for d in soil_daily if d["date"] >= today and d["soil_t_c"] >= thr["min_c"]), None)
    return {
        "daily": [dict(d, forecast=d["date"] > today) for d in soil_daily],
        "thresholds": [{k: v for k, v in t.items() if k != "keys"} for t in SOWING_THRESHOLDS],
        "crop_threshold": None if not thr else {k: v for k, v in thr.items() if k != "keys"},
        "first_day_at_threshold": first,
        "not_found_note": SOWING_NOT_FOUND,
        "provenance": provenance(
            "Zalecenia doradztwa rolniczego (ODR) – progi temperatury gleby do siewu", SOWING_THRESHOLDS[0]["url"], "zalecenie",
            note=("Progi to zalecenia ośrodków doradztwa rolniczego, nie przepisy ani zalecenia ministerstwa: kukurydza 8–10°C na "
                  "głębokości siewu (PODR Stare Pole, 2022), soja 10°C w górnej warstwie gleby (WODR Poznań). " + SOWING_NOT_FOUND),
        ),
        "soil_provenance": provenance(
            "Open-Meteo – temperatura gleby 6 cm (model numeryczny)", "https://open-meteo.com/", "model", fetched_at=fetched_at,
            resolution="2–11 km",
            note=("Średnia dobowa temperatura gleby na głębokości 6 cm z modelu numerycznego (7 dni wstecz – analiza, dalej "
                  "prognoza do 14 dni, automatyczny dobór modelu Open-Meteo: DWD ICON / ECMWF / GFS) dla komórki siatki, nie pomiar na polu. Przed siewem zmierz temperaturę gleby termometrem "
                  "glebowym rano na głębokości siewu. Dane: Open-Meteo.com, CC BY 4.0."),
        ),
    }


def field_rules(lat: float, lon: float, parcel_id: Optional[str] = None, crop: Optional[str] = None) -> Dict[str, Any]:
    """Odpowiedź GET /api/reference/rules: przepisy + prognoza do wykresów progów. Nigdy nie rzuca."""
    now = poland_now()
    gm = gmina_code(parcel_id)
    nitr = nitrates_status(now.date(), gm, crop)
    out: Dict[str, Any] = {"generated_at": iso_utc(), "parcel_id": parcel_id, "crop": crop,
                           "nitrates": nitr, "spraying": spraying_rules()}
    try:
        fw = _fetch_field_weather(lat, lon)
        fc_prov = provenance("Open-Meteo – prognoza (wiatr 10 m, temperatura 2 m)", "https://open-meteo.com/", "prognoza",
                             fetched_at=fw.get("fetched_at"),
                             note="Prognoza godzinowa wiatru na wysokości 10 m i średnia dobowa temperatura powietrza "
                                  "(7 dni wstecz – analiza modelu, dalej prognoza). Dane: Open-Meteo.com, CC BY 4.0.")
        out["spraying"]["hourly"] = spray_windows(fw["hourly"], now)
        out["spraying"]["forecast_provenance"] = fc_prov
        out["temperature"] = {"daily": fw["daily"], **temperature_threshold_series(fw["daily"], nitr["temp_threshold_c"]),
                              "provenance": fc_prov}
        out["sowing"] = sowing_block(fw.get("soil_daily") or [], crop, now.date().isoformat(), fw.get("fetched_at"))
    except SourceError as exc:
        note = f"Prognoza niedostępna ({friendly(exc)})."
        out["spraying"]["hourly"] = []
        out["spraying"]["forecast_provenance"] = missing("Open-Meteo", "https://open-meteo.com/", note)
        out["temperature"] = {"daily": [], "threshold_c": nitr["temp_threshold_c"], "first_5day_run_start": None,
                              "provenance": missing("Open-Meteo", "https://open-meteo.com/", note)}
        out["sowing"] = sowing_block([], crop, now.date().isoformat(), None)
        out["sowing"]["soil_provenance"] = missing("Open-Meteo", "https://open-meteo.com/", note)
    return out

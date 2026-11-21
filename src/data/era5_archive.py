"""Archiwum dobowe reanalizy ERA5 / ERA5-Land (Copernicus C3S, ECMWF) z zapasowym źródłem NASA POWER.

Dlaczego osobny moduł:
- Darmowe archiwum Open-Meteo (mirror ERA5 / ERA5-Land) ma limit 10 000 „ważonych” zapytań na dobę na adres IP,
  a waga zapytania rośnie z liczbą dni × zmiennych (≈ 1 zapytanie na 14 dni). Pobieranie za każdym razem całych
  10 lub 35 lat (Krok 2, zakładka Historia, Klimat pola) szybko wyczerpywało limit → HTTP 429 i „brak danych”.
- Tu dane są cache'owane w porcjach ROCZNYCH na komórkę siatki 0,1° (SQLite/JSON, `TTLCache`). Zakończone lata się
  nie zmieniają, więc z sieci pobieramy tylko brakujące dni (zwykle kilka ostatnich), a różne zakresy (1/3/5/10 lat)
  i sąsiednie działki dzielą te same porcje.
- Po HTTP 429 włącza się „bezpiecznik”: do czasu odnowienia limitu (nagłówek Retry-After) nie odpytujemy archiwum
  Open-Meteo, tylko od razu korzystamy ze źródła zapasowego – bez zbędnych sekund oczekiwania.
- Źródło zapasowe: NASA POWER (reanaliza MERRA-2 / GEOS-IT, NASA GMAO; bez klucza, dane publiczne). Jest to INNA
  reanaliza (ok. 0,5° × 0,625°), więc proweniencja zawsze mówi wprost, które źródło policzyło wynik.

Brak przecieku danych z przyszłości: zwracane są wyłącznie dni z przedziału [start, end] podanego przez wywołującego
(`end` = data analizy lub koniec dostępnego archiwum). Bieżący czas służy tylko do metadanych `fetched_at`
i do decyzji, kiedy ponownie sprawdzić ogon archiwum (opóźnienie ERA5 ok. 5 dni).

Opcjonalnie: `OPEN_METEO_API_KEY` (plan komercyjny Open-Meteo) → endpoint customer-archive-api bez limitu darmowego.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.core.cache import TTLCache
from src.core.config import settings
from src.data.common import (
    SOURCE_NASA_POWER,
    SOURCE_OM_ARCHIVE,
    SourceError,
    friendly,
    http_get_json,
    iso_utc,
    record_error,
    record_ok,
    to_float,
)

log = logging.getLogger(__name__)

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_CUSTOMER_ARCHIVE_URL = "https://customer-archive-api.open-meteo.com/v1/archive"
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

ERA5_SOURCE = "ERA5 / ERA5-Land (Copernicus C3S, ECMWF) przez archiwum Open-Meteo"
ERA5_URL = "https://open-meteo.com/en/docs/historical-weather-api"
POWER_SOURCE = "NASA POWER (reanaliza MERRA-2 / GEOS-IT, NASA GMAO) – źródło zapasowe"
POWER_URL = "https://power.larc.nasa.gov/"

PROVIDER_ERA5 = "era5"
PROVIDER_POWER = "nasa_power"

ARCHIVE_LAG_DAYS = 6                 # ERA5 (Open-Meteo) – opóźnienie ok. 5 dni
POWER_EARLIEST = date(1981, 1, 1)    # początek dziennych danych NASA POWER
ERA5_EARLIEST = date(1940, 1, 1)
CONNECT_TIMEOUT_S = 10.0             # na konektor (wymóg ≤ 30 s na całą analizę działki)
TAIL_RECHECK_S = 6 * 3600            # jak często ponownie sprawdzać „ogon” archiwum bez nowych danych
CHUNK_TTL_RECENT_S = 30 * 24 * 3600  # porcje obejmujące ostatnie ~4 miesiące (ERA5T → ERA5 final)
CHUNK_TTL_CLOSED_S = 365 * 24 * 3600
QUOTA_DEFAULT_BLOCK_S = 3600.0
QUOTA_MAX_BLOCK_S = 26 * 3600.0


@dataclass(frozen=True)
class Dataset:
    """Zestaw zmiennych dobowych: pola wyjściowe + mapowanie na zmienne Open-Meteo (ERA5) i NASA POWER."""
    name: str
    fields: Tuple[str, ...]
    om_daily: Dict[str, str] = field(default_factory=dict)
    om_hourly: Dict[str, str] = field(default_factory=dict)     # średnia dobowa z wartości godzinowych
    power: Dict[str, str] = field(default_factory=dict)
    primary: str = "precip"                                      # pole decydujące, czy dzień „ma dane”


# Krok 2 / Historia: opad dobowy (ERA5) + wilgotność gleby 7–28 cm (ERA5-Land, m³/m³);
# zapasowo: opad PRECTOTCORR + względne nasycenie strefy korzeniowej GWETROOT (0–1, NIE % objętości).
WATER = Dataset(
    "water", ("precip", "soil_moisture", "soil_wetness"),
    om_daily={"precip": "precipitation_sum"},
    om_hourly={"soil_moisture": "soil_moisture_7_to_28cm"},
    power={"precip": "PRECTOTCORR", "soil_wetness": "GWETROOT"},
)
# Klimat pola od 1991 r.: Tmax, Tmin, Tśr, opad, ET0 (FAO-56 Penman-Monteith w ERA5 / Open-Meteo);
# zapasowo z NASA POWER: temperatury + opad, ET0 liczone wzorem Hargreavesa (FAO-56, równ. 52).
CLIMATE = Dataset(
    "climate", ("tmax", "tmin", "tmean", "precip", "et0"),
    om_daily={"tmax": "temperature_2m_max", "tmin": "temperature_2m_min", "tmean": "temperature_2m_mean",
              "precip": "precipitation_sum", "et0": "et0_fao_evapotranspiration"},
    power={"tmax": "T2M_MAX", "tmin": "T2M_MIN", "tmean": "T2M", "precip": "PRECTOTCORR"},
)
DATASETS = {d.name: d for d in (WATER, CLIMATE)}

_caches: Dict[Tuple[str, str], TTLCache] = {
    (prov, ds.name): TTLCache(f"arch_{prov}_{ds.name}", max_entries=60000, default_ttl=CHUNK_TTL_CLOSED_S)
    for prov in (PROVIDER_ERA5, PROVIDER_POWER) for ds in DATASETS.values()
}
_quota_cache = TTLCache("arch_om_quota", max_entries=10, default_ttl=QUOTA_DEFAULT_BLOCK_S)

_LOCKS_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.Lock] = {}
_quota_mem: Dict[str, Any] = {}


def _lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = _LOCKS[key] = threading.Lock()
        return lk


def reset() -> None:
    """Tylko dla testów: czyści porcje archiwum i bezpiecznik limitu."""
    for c in _caches.values():
        c.clear()
    _quota_cache.clear()
    _quota_mem.clear()


def grid_key(lat: float, lon: float) -> Tuple[float, float]:
    """Siatka 0,1° (ERA5-Land ma 0,1°, ERA5 0,25°) – sąsiednie działki dzielą pobranie."""
    return round(lat, 1), round(lon, 1)


# --------------------------------------------------------------------------------------
# Bezpiecznik limitu Open-Meteo (HTTP 429)
# --------------------------------------------------------------------------------------
def om_quota_block() -> Optional[Dict[str, Any]]:
    """{"until": epoch, "reason": str} gdy archiwum Open-Meteo jest wstrzymane po HTTP 429, inaczej None."""
    now = time.time()
    st = _quota_mem.get("block")
    if not st:
        st = _quota_cache.get("block")
        if st:
            _quota_mem["block"] = st
    if st and float(st.get("until", 0)) > now:
        return st
    _quota_mem.pop("block", None)
    return None


def note_om_error(exc: BaseException) -> None:
    """Po HTTP 429 z archiwum Open-Meteo wstrzymuje kolejne zapytania do czasu odnowienia limitu."""
    if not isinstance(exc, SourceError) or exc.status_code != 429:
        return
    wait = exc.retry_after if exc.retry_after is not None else QUOTA_DEFAULT_BLOCK_S
    wait = min(max(60.0, float(wait)), QUOTA_MAX_BLOCK_S)
    until = time.time() + wait
    daily = wait > 3600 or "daily" in (exc.reason or "").lower()
    st = {"until": until, "daily": daily, "reason": exc.reason or "HTTP 429"}
    _quota_mem["block"] = st
    _quota_cache.set("block", st, wait)
    log.warning("Archiwum Open-Meteo: limit zapytań wyczerpany (%s) – wstrzymane na %.0f s", st["reason"], wait)


def blocked_error(st: Dict[str, Any]) -> SourceError:
    local = datetime.fromtimestamp(float(st["until"]), tz=timezone.utc).astimezone()
    what = "dzienny limit zapytań" if st.get("daily") else "limit zapytań"
    return SourceError("Open-Meteo archive: limit zapytań (wstrzymane)", status_code=429,
                       user_message=f"wyczerpany {what} darmowego archiwum Open-Meteo (HTTP 429), "
                                    f"ponowna próba po {local:%d.%m %H:%M}")


# --------------------------------------------------------------------------------------
# Pobieranie segmentów z sieci
# --------------------------------------------------------------------------------------
def _days(a: date, b: date) -> List[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def _fetch_om(ds: Dataset, glat: float, glon: float, a: date, b: date) -> Tuple[Dict[str, List[Optional[float]]], List[float]]:
    st = om_quota_block()
    if st:
        raise blocked_error(st)
    key = settings.open_meteo_api_key
    params: Dict[str, Any] = {
        "latitude": f"{glat:.2f}", "longitude": f"{glon:.2f}", "start_date": a.isoformat(), "end_date": b.isoformat(),
        "timezone": "Europe/Warsaw",
    }
    if ds.om_daily:
        params["daily"] = ",".join(ds.om_daily.values())
    if ds.om_hourly:
        params["hourly"] = ",".join(ds.om_hourly.values())
    if key:
        params["apikey"] = key
    url = OPEN_METEO_CUSTOMER_ARCHIVE_URL if key else OPEN_METEO_ARCHIVE_URL
    try:
        data = http_get_json(url, params=params, timeout=CONNECT_TIMEOUT_S, retries=0)
    except SourceError as exc:
        note_om_error(exc)
        raise
    if not isinstance(data, dict):
        raise SourceError("Open-Meteo archive: niepoprawna odpowiedź")
    if data.get("error"):
        raise SourceError("Open-Meteo archive: odpowiedź z błędem",
                          user_message=f"archiwum Open-Meteo odrzuciło zapytanie ({str(data.get('reason') or '')[:120]})")
    daily = data.get("daily") or {}
    hourly = data.get("hourly") or {}
    if ds.om_daily and not isinstance(daily.get("time"), list):
        raise SourceError("Open-Meteo archive: brak danych dziennych")
    by_day: Dict[str, Dict[str, Optional[float]]] = {}
    for f, var in ds.om_daily.items():
        for t, v in zip(daily.get("time") or [], daily.get(var) or []):
            by_day.setdefault(str(t)[:10], {})[f] = to_float(v)
    for f, var in ds.om_hourly.items():
        acc: Dict[str, List[float]] = {}
        for t, v in zip(hourly.get("time") or [], hourly.get(var) or []):
            fv = to_float(v)
            if fv is not None:
                acc.setdefault(str(t)[:10], []).append(fv)
        for d, vals in acc.items():
            by_day.setdefault(d, {})[f] = sum(vals) / len(vals)
    out = {f: [(by_day.get(d.isoformat()) or {}).get(f) for d in _days(a, b)] for f in ds.fields}
    cell = [to_float(data.get("latitude")), to_float(data.get("longitude"))]
    return out, cell


def _ra_mm(lat: float, d: date) -> float:
    """Promieniowanie pozaziemskie Ra [mm/d] (FAO-56, równ. 21, × 0,408)."""
    j = d.timetuple().tm_yday
    phi = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi / 365 * j)
    delta = 0.409 * math.sin(2 * math.pi / 365 * j - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    ra = 24 * 60 / math.pi * 0.0820 * dr * (ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))
    return 0.408 * ra


def hargreaves_et0(tmax: Optional[float], tmin: Optional[float], tmean: Optional[float], lat: float, d: date) -> Optional[float]:
    """ET0 wg Hargreavesa (FAO-56, równ. 52) – gdy brak promieniowania/wiatru/wilgotności do Penmana-Monteitha."""
    if tmax is None or tmin is None:
        return None
    tm = tmean if tmean is not None else (tmax + tmin) / 2
    return max(0.0, 0.0023 * (tm + 17.8) * math.sqrt(max(0.0, tmax - tmin)) * _ra_mm(lat, d))


def _fetch_power(ds: Dataset, glat: float, glon: float, a: date, b: date) -> Tuple[Dict[str, List[Optional[float]]], List[float]]:
    params = {
        "parameters": ",".join(ds.power.values()), "community": "AG", "format": "JSON",
        "latitude": f"{glat:.2f}", "longitude": f"{glon:.2f}", "start": a.strftime("%Y%m%d"), "end": b.strftime("%Y%m%d"),
    }
    data = http_get_json(NASA_POWER_URL, params=params, timeout=CONNECT_TIMEOUT_S, retries=0)
    try:
        par = data["properties"]["parameter"]
        fill = to_float((data.get("header") or {}).get("fill_value"))
    except (KeyError, TypeError, AttributeError) as exc:
        raise SourceError("NASA POWER: niepoprawna odpowiedź") from exc
    if not isinstance(par, dict) or not par:
        raise SourceError("NASA POWER: brak parametrów w odpowiedzi")
    fill = -999.0 if fill is None else fill
    days = _days(a, b)

    def col(var: str) -> List[Optional[float]]:
        series = par.get(var) or {}
        out: List[Optional[float]] = []
        for d in days:
            v = to_float(series.get(d.strftime("%Y%m%d")))
            out.append(None if v is None or v <= fill + 1e-6 else v)
        return out

    out: Dict[str, List[Optional[float]]] = {f: [None] * len(days) for f in ds.fields}
    for f, var in ds.power.items():
        out[f] = col(var)
    if "et0" in ds.fields and "et0" not in ds.power:
        out["et0"] = [None if v is None else round(v, 2) for v in
                      (hargreaves_et0(out["tmax"][i], out["tmin"][i], out["tmean"][i], glat, d) for i, d in enumerate(days))]
    coords = ((data.get("geometry") or {}).get("coordinates") or [None, None]) if isinstance(data, dict) else [None, None]
    return out, [to_float(coords[1]) if len(coords) > 1 else None, to_float(coords[0])]


_FETCHERS = {PROVIDER_ERA5: _fetch_om, PROVIDER_POWER: _fetch_power}
_SOURCE_REG = {PROVIDER_ERA5: SOURCE_OM_ARCHIVE, PROVIDER_POWER: SOURCE_NASA_POWER}


# --------------------------------------------------------------------------------------
# Porcje roczne w cache
# --------------------------------------------------------------------------------------
def _chunk_key(glat: float, glon: float, year: int) -> str:
    return f"{glat:.1f},{glon:.1f}:{year}"


def _complete_to(chunk: Dict[str, Any], primary_idx: int) -> Optional[date]:
    last = None
    for k, vals in chunk["d"].items():
        if vals[primary_idx] is not None and (last is None or k > last):
            last = k
    return date.fromisoformat(last) if last else None


def _missing_segments(chunk: Optional[Dict[str, Any]], need_a: date, need_b: date, primary_idx: int, now_ts: float) -> List[Tuple[date, date]]:
    """Brakujące dni porcji (tak, by porcja pozostała ciągła): lewy i/lub prawy segment."""
    if not chunk or not chunk.get("d"):
        return [(need_a, need_b)]
    keys = sorted(chunk["d"])
    ca, cb = date.fromisoformat(keys[0]), date.fromisoformat(keys[-1])
    segs: List[Tuple[date, date]] = []
    if need_a < ca:
        segs.append((need_a, ca - timedelta(days=1)))
    done = _complete_to(chunk, primary_idx) or (ca - timedelta(days=1))
    checked_to = date.fromisoformat(chunk.get("checked_to") or keys[-1])
    recently = now_ts - float(chunk.get("checked_at") or 0) < TAIL_RECHECK_S
    covered_b = max(done, checked_to) if recently else done
    covered_b = min(covered_b, cb)
    if need_b > covered_b:
        segs.append((covered_b + timedelta(days=1), need_b))
    return segs


def _merge_segments(segs: List[Tuple[date, date]]) -> List[Tuple[date, date]]:
    out: List[Tuple[date, date]] = []
    for a, b in sorted(segs):
        if out and a <= out[-1][1] + timedelta(days=1):
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _store(cache: TTLCache, ds: Dataset, glat: float, glon: float, chunks: Dict[int, Optional[Dict[str, Any]]],
           a: date, b: date, values: Dict[str, List[Optional[float]]], cell: List[Optional[float]], today: date) -> None:
    days = _days(a, b)
    for year in range(a.year, b.year + 1):
        ch = chunks.get(year) or {"d": {}, "cell": cell}
        for i, d in enumerate(days):
            if d.year != year:
                continue
            k = d.isoformat()
            new = [values[f][i] for f in ds.fields]
            old = ch["d"].get(k)
            if old:   # nie nadpisujemy istniejącej wartości pustą (chwilowy brak w źródle)
                new = [n if n is not None else o for n, o in zip(new, old)]
            ch["d"][k] = new
        ch["checked_to"] = max(ch.get("checked_to") or "", min(b, date(year, 12, 31)).isoformat())
        ch["checked_at"] = time.time()
        ch["fetched_at"] = iso_utc()
        ch["cell"] = cell if cell and cell[0] is not None else ch.get("cell")
        chunks[year] = ch
        recent = date(year, 12, 31) >= today - timedelta(days=120)
        cache.set(_chunk_key(glat, glon, year), ch, CHUNK_TTL_RECENT_S if recent else CHUNK_TTL_CLOSED_S)


def _collect(provider: str, ds: Dataset, glat: float, glon: float, start: date, end: date, today: date) -> Dict[str, Any]:
    """Seria dobowa [start, end] z porcji rocznych; dociąga z sieci tylko brakujące dni. Rzuca SourceError."""
    cache = _caches[(provider, ds.name)]
    pidx = ds.fields.index(ds.primary)
    now_ts = time.time()
    chunks: Dict[int, Optional[Dict[str, Any]]] = {y: cache.get(_chunk_key(glat, glon, y)) for y in range(start.year, end.year + 1)}
    segs: List[Tuple[date, date]] = []
    for y, ch in chunks.items():
        segs += _missing_segments(ch, max(start, date(y, 1, 1)), min(end, date(y, 12, 31)), pidx, now_ts)
    fetched = 0
    for a, b in _merge_segments(segs):
        try:
            values, cell = _FETCHERS[provider](ds, glat, glon, a, b)
        except SourceError as exc:
            record_error(_SOURCE_REG[provider], friendly(exc))
            raise
        fetched += 1
        _store(cache, ds, glat, glon, chunks, a, b, values, cell, today)
    if fetched:
        record_ok(_SOURCE_REG[provider], f"OK ({ds.name}: pobrano {fetched} zakres(y) dni)")
    series: Dict[str, List[Optional[float]]] = {f: [] for f in ds.fields}
    days = _days(start, end)
    for d in days:
        vals = ((chunks.get(d.year) or {}).get("d") or {}).get(d.isoformat())
        for i, f in enumerate(ds.fields):
            series[f].append(vals[i] if vals else None)
    if not any(v is not None for v in series[ds.primary]):
        raise SourceError(f"{provider}: brak danych w zakresie {start}–{end}",
                          user_message=f"źródło nie zwróciło danych dla okresu {start:%d.%m.%Y}–{end:%d.%m.%Y}")
    stamps = [c.get("fetched_at") for c in chunks.values() if c and c.get("fetched_at")]
    cells = [c.get("cell") for c in chunks.values() if c and c.get("cell")]
    last = max((d for d, v in zip(days, series[ds.primary]) if v is not None), default=None)
    return {
        "provider": provider, "start": start.isoformat(), "end": end.isoformat(),
        "days": [d.isoformat() for d in days], "series": series,
        "data_end": last.isoformat() if last else None,
        "fetched_at": min(stamps) if stamps else iso_utc(), "cell": cells[-1] if cells else [glat, glon],
        "network_calls": fetched,
    }


def daily_series(lat: float, lon: float, start: date, end: date, ds: Dataset = WATER, today: Optional[date] = None) -> Dict[str, Any]:
    """Dobowa seria [start, end] dla komórki z działką: najpierw ERA5 (Open-Meteo), przy błędzie NASA POWER.

    Zwraca {"provider", "days", "series": {pole: [...]}, "data_end", "fetched_at", "cell", "fallback_reason"}.
    Rzuca SourceError (z czytelnym `user_message`), gdy oba źródła zawiodą.
    """
    if end < start:
        raise SourceError("zakres dat odwrócony", user_message="niepoprawny zakres dat")
    glat, glon = grid_key(lat, lon)
    today = today or datetime.now(timezone.utc).date()   # tylko do TTL porcji (nie do okna analizy)
    with _lock(f"{ds.name}:{glat:.1f},{glon:.1f}"):
        try:
            res = _collect(PROVIDER_ERA5, ds, glat, glon, max(start, ERA5_EARLIEST), end, today)
            res["fallback_reason"] = None
            return res
        except SourceError as e1:
            reason1 = friendly(e1)
        p_start = max(start, POWER_EARLIEST)
        try:
            if p_start > end:
                raise SourceError("NASA POWER: zakres sprzed 1981 r.", user_message="NASA POWER obejmuje dane od 1981 r.")
            res = _collect(PROVIDER_POWER, ds, glat, glon, p_start, end, today)
            res["fallback_reason"] = reason1
            log.info("Archiwum %s (%.1f, %.1f): ERA5 niedostępne (%s) – użyto NASA POWER", ds.name, glat, glon, reason1)
            return res
        except SourceError as e2:
            reason2 = friendly(e2)
    raise SourceError(f"ERA5: {reason1}; NASA POWER: {reason2}",
                      user_message=f"ERA5 (Open-Meteo): {reason1}; zapasowo NASA POWER: {reason2}")


def archive_end(now: datetime) -> date:
    """Ostatni dzień, którego można się spodziewać w archiwum ERA5 (opóźnienie ok. 5 dni)."""
    return (now - timedelta(days=ARCHIVE_LAG_DAYS)).date()


def provider_label(provider: str) -> Tuple[str, str]:
    return (ERA5_SOURCE, ERA5_URL) if provider == PROVIDER_ERA5 else (POWER_SOURCE, POWER_URL)

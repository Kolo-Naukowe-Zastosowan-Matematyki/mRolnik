"""Wspólne narzędzia warstwy danych: HTTP z timeoutem i ponowieniem, proweniencja, rejestr stanu źródeł.

Zasada: każda funkcja pobierająca dane zewnętrzne zgłasza wynik do rejestru (`record_ok` / `record_error`),
a błąd sieci NIGDY nie jest zamieniany na wartość zmyśloną. Wywołujący dostaje wyjątek `SourceError`
i sam buduje proweniencję `kind="brak"` z komunikatem.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests

from src.core.config import settings

log = logging.getLogger(__name__)

# "statystyka" – statystyka urzędowa lub średnia wieloletnia z danych państwowych (GUS BDL, normy IMGW-PIB);
# "tabelaryczna" – wartość z tabel literaturowych (np. FAO-56), nie pomiar; "przepis" – próg/termin z aktu prawnego;
# "zalecenie" – zalecenie państwowego doradztwa rolniczego (ODR), nie przepis.
PROVENANCE_KINDS = ("pomiar", "prognoza", "model", "reanaliza", "szacunek", "statystyka", "tabelaryczna", "przepis", "zalecenie",
                    "demo", "brak")


class SourceError(RuntimeError):
    """Źródło danych nie odpowiedziało lub zwróciło niepoprawną odpowiedź."""


def friendly(exc: BaseException) -> str:
    """Krótki, zrozumiały dla użytkownika opis błędu źródła (pełny opis trafia do logu, nie do UI)."""
    log.warning("Błąd źródła danych: %s", exc)
    chain, e = [], exc
    while e is not None and len(chain) < 6:
        chain.append(e)
        e = e.__cause__ or e.__context__
    for e in chain:
        if isinstance(e, requests.Timeout):
            return "serwis nie odpowiedział w wyznaczonym czasie"
        if isinstance(e, requests.ConnectionError):
            return "brak połączenia z serwisem"
        if isinstance(e, requests.HTTPError):
            code = getattr(getattr(e, "response", None), "status_code", None)
            return f"serwis zwrócił błąd HTTP {code}" if code else "serwis zwrócił błąd"
    if isinstance(exc, SourceError) and exc.__cause__ is None:
        msg = str(exc)
        return msg if len(msg) <= 160 and "http" not in msg.lower() else "nieprawidłowa odpowiedź serwisu"
    return "nieprawidłowa odpowiedź serwisu"


# --------------------------------------------------------------------------------------
# Czas
# --------------------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: Optional[datetime] = None) -> str:
    dt = dt or utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------------------
# Proweniencja (kształt wg Kontraktu 2)
# --------------------------------------------------------------------------------------
def provenance(
    source: str,
    source_url: str,
    kind: str,
    note: str = "",
    observed_at: Optional[str] = None,
    fetched_at: Optional[str] = None,
    cloud_cover_pct: Optional[float] = None,
    resolution: Optional[str] = None,
) -> Dict[str, Any]:
    if kind not in PROVENANCE_KINDS:
        raise ValueError(f"nieznany rodzaj proweniencji: {kind}")
    return {
        "source": source,
        "source_url": source_url,
        "kind": kind,
        "observed_at": observed_at,
        "fetched_at": fetched_at or iso_utc(),
        "cloud_cover_pct": None if cloud_cover_pct is None else round(float(cloud_cover_pct), 1),
        "resolution": resolution,
        "note": note,
    }


def missing(source: str, source_url: str, note: str) -> Dict[str, Any]:
    """Proweniencja dla braku danych."""
    return provenance(source, source_url, "brak", note=note)


# --------------------------------------------------------------------------------------
# Rejestr stanu źródeł (do GET /api/sources/status)
# --------------------------------------------------------------------------------------
SOURCE_CDSE = "Copernicus Sentinel-2 (CDSE Sentinel Hub)"
SOURCE_IMGW_SYNOP = "IMGW-PIB – dane synoptyczne"
SOURCE_IMGW_WARN = "IMGW-PIB – ostrzeżenia meteorologiczne"
SOURCE_IMGW_HYDRO = "IMGW-PIB – dane hydrologiczne"
SOURCE_OM_FORECAST = "Open-Meteo – prognoza pogody"
SOURCE_OM_SOIL = "Open-Meteo – wilgotność i temperatura gleby"
SOURCE_ULDK = "GUGiK ULDK – działki ewidencyjne"
SOURCE_HYDRO_WFS = "Hydrografia – BDOT10k GUGiK (WMS; WFS opcjonalnie)"
SOURCE_IMGW_HYDRO_BACK = "IMGW-PIB – hydro.imgw.pl (stany charakterystyczne, 7 dni)"
SOURCE_GUS_BDL = "GUS – Bank Danych Lokalnych (plony)"
SOURCE_ORTHO = "GUGiK – skorowidz ortofotomap (data nalotu)"   # tylko podpis mapy, poza listą statusu źródeł
# wyszukiwarka działek (src/data/parcel_search.py)
SOURCE_BDOT10K = "GUGiK BDOT10k – obiekty topograficzne (paczki powiatowe)"
SOURCE_PRNG = "GUGiK PRNG – nazwy miejscowości (WFS)"
SOURCE_NMT = "GUGiK NMT – numeryczny model terenu 1 m (WCS)"
SOURCE_ISOK = "PGW Wody Polskie ISOK – obszary zagrożenia powodziowego (WFS)"
SOURCE_EGIB = "GUGiK – EGiB WFS (usługa zbiorcza; pokrycie zależy od starostw)"

ALL_SOURCES = (
    SOURCE_CDSE,
    SOURCE_IMGW_SYNOP,
    SOURCE_IMGW_WARN,
    SOURCE_IMGW_HYDRO,
    SOURCE_OM_FORECAST,
    SOURCE_OM_SOIL,
    SOURCE_ULDK,
    SOURCE_HYDRO_WFS,
    SOURCE_IMGW_HYDRO_BACK,
    SOURCE_GUS_BDL,
    SOURCE_BDOT10K,
    SOURCE_PRNG,
    SOURCE_NMT,
    SOURCE_ISOK,
    SOURCE_EGIB,
)

_STATUS_LOCK = threading.Lock()
_STATUS: Dict[str, Dict[str, Any]] = {}


def record_ok(source: str, message: str = "OK") -> None:
    with _STATUS_LOCK:
        st = _STATUS.setdefault(source, {})
        st.update({"ok": True, "last_success": iso_utc(), "last_attempt": iso_utc(), "message": message})


def record_error(source: str, message: str) -> None:
    with _STATUS_LOCK:
        st = _STATUS.setdefault(source, {})
        st.update({"ok": False, "last_attempt": iso_utc(), "message": message})


def source_status(source: str, configured: bool = True) -> Dict[str, Any]:
    with _STATUS_LOCK:
        st = dict(_STATUS.get(source, {}))
    checked = "last_attempt" in st
    return {
        "name": source,
        "ok": bool(st.get("ok")) if checked else False,
        "configured": configured,
        "checked": checked,
        "last_success": st.get("last_success"),
        "message": st.get("message") or ("Jeszcze nie odpytywano w tej sesji serwera" if configured else "Nieskonfigurowane"),
    }


def reset_status() -> None:
    """Tylko dla testów."""
    with _STATUS_LOCK:
        _STATUS.clear()


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------
def user_agent() -> str:
    ua = "mRolnik/0.3 (prototyp; modul eRolnik)"
    if settings.contact_email:
        ua += f" kontakt: {settings.contact_email}"
    return ua


_RETRY_STATUS = {429, 500, 502, 503, 504}

# Trwałe połączenia (keep-alive): osobna sesja requests na wątek, więc kolejne zapytania do tego samego serwisu
# (CDSE, ULDK, IMGW, Open-Meteo) nie zestawiają za każdym razem od nowa połączenia TCP i TLS. Dane są te same.
_TLS = threading.local()


def http_session() -> requests.Session:
    s = getattr(_TLS, "session", None)
    if s is None:
        s = requests.Session()
        _TLS.session = s
    return s


def http_request(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Any = None,
    data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 5.0,
    retries: int = 1,
) -> requests.Response:
    """Wywołanie HTTP z timeoutem i `retries` ponowieniami (błędy sieci, 429, 5xx).

    Zwraca odpowiedź z kodem 2xx albo rzuca SourceError. W trybie MROLNIK_OFFLINE_DEMO nie łączy się z siecią.
    """
    if settings.offline_demo:
        raise SourceError("Tryb offline (MROLNIK_OFFLINE_DEMO=1) – źródła zewnętrzne wyłączone")
    hdrs = {"User-Agent": user_agent(), "Accept-Encoding": "gzip"}
    if headers:
        hdrs.update(headers)
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            if method == "GET":
                resp = http_session().get(url, params=params, headers=hdrs, timeout=timeout)
            else:
                resp = http_session().post(url, params=params, json=json_body, data=data, headers=hdrs, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            log.warning("HTTP %s %s – błąd połączenia (próba %d): %s", method, url, attempt + 1, exc)
        else:
            code = getattr(resp, "status_code", 0)
            if 200 <= code < 300:
                return resp
            last_exc = SourceError(f"HTTP {code}")
            log.warning("HTTP %s %s – kod %s (próba %d)", method, url, code, attempt + 1)
            if code not in _RETRY_STATUS:
                break
        if attempt < retries:
            time.sleep(0.4 * (attempt + 1))
    raise SourceError(f"{url}: {last_exc}")


def http_get_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: float = 5.0, retries: int = 1) -> Any:
    resp = http_request("GET", url, params=params, timeout=timeout, retries=retries)
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceError(f"{url}: niepoprawny JSON") from exc


# --------------------------------------------------------------------------------------
# Drobne narzędzia liczbowe
# --------------------------------------------------------------------------------------
def to_float(v: Any) -> Optional[float]:
    """Konwersja wartości z API (liczba / string / None / 'NaN') na float lub None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(str(v).replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def safe_sum(values: Iterable[Optional[float]]) -> Optional[float]:
    vals: List[float] = [v for v in values if v is not None]
    return round(sum(vals), 1) if vals else None


def normalize_name(s: str) -> str:
    """Nazwa bez polskich znaków, małe litery, tylko litery/cyfry (do dopasowań nazw stacji)."""
    tr = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
    return "".join(ch for ch in s.translate(tr).lower() if ch.isalnum())

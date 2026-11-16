"""Serwis analizy działek (POST /api/analyze) wg Kontraktu 2 (docs/PLAN_I_KONTRAKT.md).

Łączy: GUGiK ULDK (granice), Copernicus Sentinel-2 przez CDSE (NDVI, historia), IMGW-PIB (obserwacje,
ostrzeżenia), Open-Meteo (prognoza, wilgotność i temperatura gleby). Każda liczba ma proweniencję; awaria źródła
daje null + kind "brak" + ostrzeżenie, nigdy wartość zmyśloną ani błąd 500.

Wydajność: wspólna, ograniczona pula wątków na I/O, cache per źródło (src.core.cache.TTLCache), pogoda i gleba
pobierane raz na rejon (~5 km), limit czasu całej analizy. Odpowiedź nie jest cache'owana jako całość.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.config import settings
from src.data import sentinel, soil, weather
from src.data.cadastre import ParcelData, fetch_parcel_by_id, fetch_parcel_by_xy, parcel_provenance
from src.data.common import (
    SOURCE_CDSE,
    SOURCE_GUS_BDL,
    SOURCE_HYDRO_WFS,
    SOURCE_IMGW_HYDRO_BACK,
    SOURCE_IMGW_HYDRO,
    SOURCE_IMGW_SYNOP,
    SOURCE_IMGW_WARN,
    SOURCE_OM_FORECAST,
    SOURCE_OM_SOIL,
    SOURCE_ULDK,
    SourceError,
    http_get_json,
    http_request,
    iso_utc,
    missing,
    record_error,
    record_ok,
    source_status,
    utcnow,
)
from src.data.imgw_stations import VOIVODESHIP_TERYT
from src.data.presets import DEFAULT_FARM, FARM_PRESETS
from src.hydromelior import hydrography
from src.data.soil import fetch_soil, soil_block_from
from src.data.weather import WEATHER_MODELS, cluster_key, get_weather

log = logging.getLogger(__name__)

MAX_PARCELS = 50
PARCEL_STAGE_TIMEOUT_S = 15.0
TOTAL_TIMEOUT_S = 60.0

_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="mrolnik-io")
# zadania w tle (historia NDVI po odpowiedzi, rozgrzewanie cache) – mała pula, żeby nie zająć limitów CDSE
_BG_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mrolnik-bg")


def _safe(fn: Callable[..., Any], *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 – zadanie w tle nie może przerwać serwera
        log.exception("Błąd zadania w tle %s", getattr(fn, "__name__", fn))
        return None

SOURCES: List[Dict[str, str]] = [
    {"name": "Copernicus Sentinel-2 L2A (Copernicus Data Space Ecosystem)", "url": "https://dataspace.copernicus.eu/", "kind": "pomiar",
     "licence": "Dane Copernicus: bezpłatne, pełne i otwarte (rozporządzenie delegowane UE nr 1159/2013). "
                "Atrybucja: „Zawiera zmodyfikowane dane Copernicus Sentinel [rok]”."},
    {"name": "IMGW-PIB – dane publiczne (synop, ostrzeżenia, hydro)", "url": "https://danepubliczne.imgw.pl/", "kind": "pomiar",
     "licence": "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy. "
                "Dane IMGW-PIB zostały przetworzone (wybór najbliższej stacji, filtrowanie ostrzeżeń)."},
    {"name": "GUGiK – ULDK (działki ewidencyjne), BDOT10k", "url": "https://uldk.gugik.gov.pl/", "kind": "pomiar",
     "licence": "Dane otwarte państwowego zasobu geodezyjnego i kartograficznego, udostępniane bezpłatnie (Prawo geodezyjne i kartograficzne)."},
    {"name": "PGW Wody Polskie / KZGW – Mapa Podziału Hydrograficznego Polski", "url": "https://wody.isok.gov.pl/", "kind": "pomiar",
     "licence": "Dane publiczne PGW Wody Polskie; podaj źródło."},
    {"name": "Open-Meteo (modele ECMWF, DWD ICON, NOAA GFS; ERA5-Land – Copernicus C3S)", "url": "https://open-meteo.com/", "kind": "prognoza",
     "licence": "CC BY 4.0 – „Weather data by Open-Meteo.com”. Darmowe API tylko do użytku niekomercyjnego; "
                "wdrożenie produkcyjne wymaga planu komercyjnego lub własnej instancji."},
    {"name": "IMGW-PIB – archiwum danych miesięcznych (normy klimatyczne 1991–2020) i hydro.imgw.pl (stany charakterystyczne)",
     "url": "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/", "kind": "statystyka",
     "licence": "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy. "
                "Normy policzone przez mRolnik z danych IMGW-PIB (przetworzenie)."},
    {"name": "GUS – Bank Danych Lokalnych (plony z 1 ha)", "url": "https://bdl.stat.gov.pl/", "kind": "statystyka",
     "licence": "Dane statystyki publicznej GUS – ponowne wykorzystanie z podaniem źródła („Źródło: GUS, Bank Danych Lokalnych”)."},
    {"name": "Przepisy: program azotanowy (Dz.U. 2023 poz. 244) i warunki stosowania środków ochrony roślin (Dz.U. 2014 poz. 516, zm. 2026 poz. 353)",
     "url": "https://isap.sejm.gov.pl/", "kind": "przepis",
     "licence": "Teksty aktów prawnych (ISAP, Dziennik Ustaw) – terminy nawożenia azotem, progi temperatury i dopuszczalna prędkość wiatru."},
    {"name": "FAO-56 (tab. 19) – typowa pojemność polowa i punkt więdnięcia", "url": "https://www.fao.org/3/x0490e/x0490e0a.htm",
     "kind": "tabelaryczna", "licence": "Wartości literaturowe (Allen i in., FAO Irrigation and Drainage Paper 56, 1998) – nie pomiar."},
    {"name": "IUNG-PIB – System Monitoringu Suszy Rolniczej (odnośnik)", "url": "https://susza.iung.pulawy.pl/", "kind": "odnośnik",
     "licence": "Serwis referencyjny IUNG-PIB; mRolnik nie pobiera z niego danych automatycznie."},
]


# --------------------------------------------------------------------------------------
# Presety / źródła
# --------------------------------------------------------------------------------------
def get_farm_presets_list() -> List[Dict[str, Any]]:
    return [{"name": name, "parcels": plist, "demo": True} for name, plist in FARM_PRESETS.items()]


def weather_models_list() -> Dict[str, Dict[str, Any]]:
    return {k: {"label": v["label"], "days": v["days"]} for k, v in WEATHER_MODELS.items()}


def _probe(source: str, fn: Callable[[], Any]) -> None:
    """Wywołuje test źródła; jeśli fn zwraca tekst, trafia on do komunikatu statusu."""
    try:
        msg = fn()
        record_ok(source, msg if isinstance(msg, str) and msg else "OK")
    except (SourceError, ValueError, TypeError, KeyError) as exc:
        record_error(source, str(exc))


def _probe_imgw_warnings() -> str:
    data = http_get_json(weather.IMGW_WARNINGS_URL, timeout=6.0, retries=0)
    if not isinstance(data, list):
        raise SourceError("IMGW ostrzeżenia: nieoczekiwany format")
    return f"{len(data)} aktywnych ostrzeżeń w kraju"


def _probe_imgw_hydro() -> str:
    recs = weather.parse_hydro_records(http_get_json(weather.IMGW_HYDRO_URLS[0], timeout=8.0, retries=0))
    if not recs:
        raise SourceError("IMGW hydro: brak posterunków z lokalizacją")
    return f"{len(recs)} posterunków"


def _probe_om_soil() -> str:
    data = http_get_json(soil.OPEN_METEO_FORECAST_URL, params={
        "latitude": 52.2, "longitude": 19.0, "hourly": ",".join(soil.ICON_VARS), "forecast_days": 1, "timezone": "GMT"},
        timeout=6.0, retries=0)
    vals = ((data or {}).get("hourly") or {}).get(soil.ICON_VARS[-1]) if isinstance(data, dict) else None
    if not vals or all(v is None for v in vals):
        raise SourceError("Open-Meteo: brak wilgotności gleby w odpowiedzi")
    return "OK (model DWD ICON)"


def _probe_hydro_back() -> str:
    from src.data.reference import fetch_hydro_back
    r = fetch_hydro_back("152170010")   # Śrem (Warta) – wodowskaz referencyjny do testu dostępności
    return f"OK (Śrem: ostrzegawczy {r['warning_level_cm']:.0f} cm)" if r.get("warning_level_cm") is not None else "OK"


def _probe_gus() -> str:
    from src.data.gus import fetch_variable
    d = fetch_variable(4332)
    years = sorted(int(y) for y in d["poland"])
    return f"OK (plony pszenicy do {years[-1]} r.)" if years else "OK"


def sources_status(check: bool = False) -> List[Dict[str, Any]]:
    """Stan źródeł do panelu "Źródła danych". check=True wykonuje lekkie zapytania testowe (równolegle, ≤10 s)."""
    if check:
        probes = [
            _POOL.submit(sentinel.probe),
            _POOL.submit(_probe, SOURCE_IMGW_SYNOP, lambda: http_request("GET", "https://danepubliczne.imgw.pl/api/data/synop", timeout=5, retries=0)),
            _POOL.submit(_probe, SOURCE_OM_FORECAST, lambda: http_request(
                "GET", "https://api.open-meteo.com/v1/forecast", params={"latitude": 52.2, "longitude": 19.0, "current": "temperature_2m"},
                timeout=5, retries=0)),
            _POOL.submit(_probe, SOURCE_ULDK, lambda: http_request(
                "GET", "https://uldk.gugik.gov.pl/", params={"request": "GetParcelByXY", "xy": "17.2012,52.2020,4326", "result": "id"},
                timeout=5, retries=0)),
            _POOL.submit(_probe, SOURCE_IMGW_WARN, _probe_imgw_warnings),
            _POOL.submit(_probe, SOURCE_IMGW_HYDRO, _probe_imgw_hydro),
            _POOL.submit(_probe, SOURCE_OM_SOIL, _probe_om_soil),
            _POOL.submit(hydrography.probe),
            _POOL.submit(_probe, SOURCE_IMGW_HYDRO_BACK, _probe_hydro_back),
            _POOL.submit(_probe, SOURCE_GUS_BDL, _probe_gus),
        ]
        wait(probes, timeout=12)
    out = [source_status(SOURCE_CDSE, configured=settings.cdse_configured)]
    if not settings.cdse_configured:
        out[0]["message"] = "Nieskonfigurowane: ustaw CDSE_CLIENT_ID i CDSE_CLIENT_SECRET (instrukcja: docs/ZRODLA_DANYCH.md)"
    for s in (SOURCE_IMGW_SYNOP, SOURCE_IMGW_WARN, SOURCE_IMGW_HYDRO, SOURCE_IMGW_HYDRO_BACK, SOURCE_OM_FORECAST, SOURCE_OM_SOIL,
              SOURCE_ULDK, SOURCE_HYDRO_WFS, SOURCE_GUS_BDL):
        out.append(source_status(s, configured=not settings.offline_demo))
    return out


# --------------------------------------------------------------------------------------
# Ocena działki (tylko z dostępnych danych)
# --------------------------------------------------------------------------------------
def health_assessment(ndvi: Optional[Dict[str, Any]], water_days: Optional[int]) -> Tuple[Optional[float], str, str]:
    """(health_score 0–100 | None, status_label, risk). Bez NDVI → brak oceny kondycji."""
    water_risk = None
    if water_days is not None:
        water_risk = "Wysokie" if water_days == 0 else ("Podwyższone" if water_days <= 3 else ("Średnie" if water_days <= 7 else "Niskie"))
    if not ndvi or ndvi.get("mean") is None:
        return None, "Brak danych satelitarnych", water_risk or "Brak danych"
    mean = ndvi["mean"]
    score = max(0.0, min(1.0, (mean - 0.1) / 0.7)) * 70.0
    healthy = ndvi.get("healthy_pct")
    score += (healthy if healthy is not None else max(0.0, min(100.0, (mean - 0.25) / 0.5 * 100))) * 0.30
    score = round(max(0.0, min(100.0, score)), 1)
    if score >= 75:
        label, risk = "Bardzo dobra kondycja łanu", "Niskie"
    elif score >= 60:
        label, risk = "Dobra kondycja łanu", "Niskie"
    elif score >= 45:
        label, risk = "Umiarkowana – widoczny stres lub rzadki łan", "Średnie"
    elif score >= 30:
        label, risk = "Słaba – duży udział gleby odsłoniętej lub stresu", "Podwyższone"
    else:
        label, risk = "Bardzo słaba – głównie gleba odsłonięta", "Wysokie"
    order = ["Niskie", "Średnie", "Podwyższone", "Wysokie"]
    if water_risk and order.index(water_risk) > order.index(risk):
        risk = water_risk
    return score, label, risk


def advice_for(parcel_out: Dict[str, Any], weather: Dict[str, Any], water: Dict[str, Any]) -> List[str]:
    """Proste zalecenia "co zrobić dziś" – każde wynika z konkretnej, dostępnej wartości."""
    tips: List[str] = []
    cur = weather.get("current") or {}
    daily = weather.get("daily") or []
    wind = cur.get("wind_ms")
    if wind is not None:
        if wind > 4:
            tips.append(f"Wiatr {wind:.0f} m/s – odłóż oprysk: przepisy dopuszczają oprysk sprzętem naziemnym przy wietrze do 4 m/s "
                        "(do 5 m/s przy technice ograniczającej znoszenie cieczy o co najmniej 75%; rozp. MRiRW, Dz.U. 2014 poz. 516 ze zm.).")
    rain2 = [d.get("rain_mm") for d in daily[:2] if d.get("rain_mm") is not None]
    if rain2 and sum(rain2) >= 10:
        tips.append(f"Do jutra prognozowane {sum(rain2):.0f} mm deszczu – nie wysiewaj teraz nawozów azotowych (ryzyko wymycia i spływu).")
    tmins = [d.get("t_min_c") for d in daily[:3] if d.get("t_min_c") is not None]
    if tmins and min(tmins) <= -2:
        tips.append(f"W ciągu 3 dni możliwy przymrozek do {min(tmins):.0f}°C – nie planuj zabiegów dolistnych na te dni.")
    for w in weather.get("warnings") or []:
        tips.append(f"Obowiązuje ostrzeżenie IMGW-PIB: {w['event']} (stopień {w.get('level')}), do {w.get('valid_to') or '?'}.")
        break
    sm = parcel_out.get("soil_moisture")
    if sm and sm.get("surface_pct") is not None and sm.get("field_capacity_pct") is not None:
        if sm["surface_pct"] >= sm["field_capacity_pct"] + 3:
            tips.append("Wierzchnia warstwa gleby jest bardzo mokra – unikaj wjazdu ciężkim sprzętem (ugniatanie gleby).")
    days = parcel_out.get("water_reserve_days")
    if days is not None and days <= 3:
        # "dni do stresu wodnego" liczone do progu łatwo dostępnej wody (FAO-56, p = 0,5) – podajemy też % wody
        # użytecznej, ten sam wskaźnik co w HydroMelior, żeby oba widoki mówiły to samo
        awp = (sm or {}).get("available_water_pct")
        left = f" (zostało ok. {awp:.0f}% wody użytecznej)" if awp is not None else ""
        if days == 0:
            tips.append(f"Łatwo dostępna woda w glebie wyczerpana{left} – rośliny mogą wchodzić w stres wodny; "
                        "ogranicz uprawki przesuszające, jeśli możesz, nawadniaj.")
        else:
            tips.append(f"Za ok. {days} dni wyczerpie się łatwo dostępna woda w glebie{left} – ogranicz uprawki "
                        "przesuszające; jeśli możesz, nawadniaj.")
    ndvi = parcel_out.get("ndvi")
    if ndvi:
        if ndvi.get("stress_pct") is not None and ndvi["stress_pct"] >= 30:
            tips.append(f"Około {ndvi['stress_pct']:.0f}% działki ma słabszy łan – obejrzyj te miejsca na mapie NDVI i w terenie.")
        month = utcnow().month
        if month in (11, 12, 1, 2, 3) and ndvi.get("mean") is not None and ndvi["mean"] < 0.5:
            tips.append("Poza sezonem wegetacji niskie NDVI jest naturalne (rośliny w spoczynku, krótki dzień).")
    if not tips:
        has_data = bool(ndvi) or bool(parcel_out.get("soil_moisture")) or bool(daily) or cur.get("temp_c") is not None
        tips.append("Na podstawie dostępnych danych nie ma pilnych działań na dziś." if has_data else
                    "Brak danych do przygotowania zaleceń – źródła nie odpowiedziały. Spróbuj ponownie za kilka minut.")
    return tips


# --------------------------------------------------------------------------------------
# Analiza
# --------------------------------------------------------------------------------------
def _resolve_parcel(item: Dict[str, Any]) -> Optional[ParcelData]:
    pid = item.get("parcel_id")
    if pid:
        p = fetch_parcel_by_id(pid)
        if p is not None:
            return p
    return fetch_parcel_by_xy(float(item["lon"]), float(item["lat"]))


def _result(fut: Optional[Future], default: Any = None) -> Any:
    if fut is None or not fut.done():
        return default
    try:
        return fut.result()
    except Exception:  # noqa: BLE001 – błąd w wątku nie może przerwać całej analizy
        log.exception("Błąd zadania analizy")
        return default


def _wait_all(futs: List[Future], deadline: float) -> None:
    pending = [f for f in futs if f is not None]
    while pending:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        _, not_done = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
        pending = list(not_done)


def _teryt_codes(p: Optional[ParcelData]) -> Tuple[Optional[str], Optional[str]]:
    if p is None:
        return None, None
    powiat = p.parcel_id[:4] if p.parcel_id[:4].isdigit() else None
    voiv = VOIVODESHIP_TERYT.get((p.voivodeship or "").strip().lower())
    return powiat, voiv or (powiat[:2] if powiat else None)


def analyze_parcels_batch(parcels_request: List[Dict[str, Any]], weather_model_key: str = "auto") -> Dict[str, Any]:
    started = time.monotonic()
    deadline = started + TOTAL_TIMEOUT_S
    warnings: List[str] = []
    items = list(parcels_request)[:MAX_PARCELS]
    if weather_model_key not in WEATHER_MODELS:
        weather_model_key = "auto"

    # 1. Granice działek (ULDK) – równolegle
    pf = [_POOL.submit(_resolve_parcel, it) for it in items]
    _wait_all(pf, min(deadline, started + PARCEL_STAGE_TIMEOUT_S))
    parcels: List[Optional[ParcelData]] = [_result(f) for f in pf]

    # 2. Rejony pogodowe (≈5 km): pogoda + gleba raz na rejon
    locs = [(p.centroid_lat, p.centroid_lon) if p else (float(it["lat"]), float(it["lon"])) for p, it in zip(parcels, items)]
    clusters: Dict[Tuple[float, float], Dict[str, Any]] = {}
    for idx, (loc, p) in enumerate(zip(locs, parcels)):
        ck = cluster_key(*loc)
        c = clusters.setdefault(ck, {"members": [], "loc": loc, "teryt": (None, None)})
        c["members"].append(idx)
        if c["teryt"] == (None, None):
            c["teryt"] = _teryt_codes(p)
    for ck, c in clusters.items():
        powiat, voiv = c["teryt"]
        c["wf"] = _POOL.submit(get_weather, c["loc"][0], c["loc"][1], weather_model_key, powiat, voiv)
        c["sf"] = _POOL.submit(fetch_soil, c["loc"][0], c["loc"][1])

    # 3. Sentinel-2 per działka: bieżące NDVI/NDMI czekamy; historię 24 mies. (najwolniejsza, ~6 s/działkę) bierzemy tylko z cache,
    #    a gdy jej nie ma – pobieramy w tle (przeglądarka doczyta ją przez /api/history/ndvi, status „pending”).
    s2f = [_POOL.submit(sentinel.analyze_current, p) if p else None for p in parcels]
    hist_cached = [sentinel.cached_history(p) if p else None for p in parcels]
    for p, hc in zip(parcels, hist_cached):
        if p is not None and hc is None and settings.cdse_configured:
            _BG_POOL.submit(_safe, sentinel.monthly_history, p)

    all_futs = [c["wf"] for c in clusters.values()] + [c["sf"] for c in clusters.values()] + s2f
    _wait_all(all_futs, deadline)
    if any(f is not None and not f.done() for f in all_futs):
        warnings.append("Część źródeł nie odpowiedziała w limicie czasu – brakujące dane oznaczono jako „brak”.")

    cluster_weather: Dict[Tuple[float, float], Dict[str, Any]] = {}
    cluster_soil: Dict[Tuple[float, float], Tuple[Optional[Dict[str, Any]], Optional[str]]] = {}
    for ck, c in clusters.items():
        w = _result(c["wf"])
        if w is None:
            w = _empty_weather("Pogoda nie została pobrana w limicie czasu.")
        cluster_weather[ck] = w
        sf: Future = c["sf"]
        if sf.done() and sf.exception() is None:
            cluster_soil[ck] = (sf.result(), None)
        else:
            err = str(sf.exception()) if sf.done() else "przekroczono limit czasu"
            cluster_soil[ck] = (None, err)

    # 4. Złożenie wyników działek
    out_parcels = []
    for i, (it, p) in enumerate(zip(items, parcels)):
        ck = cluster_key(*locs[i])
        out_parcels.append(_build_parcel(it, p, cluster_weather[ck], cluster_soil[ck], _result(s2f[i]), hist_cached[i]))

    # 5. Pogoda główna: rejon z największą liczbą działek
    main_ck = max(clusters, key=lambda k: len(clusters[k]["members"]))
    weather = dict(cluster_weather[main_ck])
    weather["soil_temp_c"] = (cluster_soil[main_ck][0] or {}).get("soil_temp_c")
    warnings += weather.pop("notes", [])
    if len(clusters) > 1:
        warnings.append(f"Działki leżą w {len(clusters)} rejonach pogodowych; panel pogody dotyczy rejonu z największą liczbą działek, "
                        "a zapas wody i zalecenia liczone są dla rejonu każdej działki.")
    if not settings.cdse_configured:
        warnings.append(sentinel.NOT_CONFIGURED_NOTE)

    coords = [c for po in out_parcels for c in po["geojson_coords"]]
    lats = [c[1] for c in coords] or [lat for lat, _ in locs]
    lons = [c[0] for c in coords] or [lon for _, lon in locs]
    area = [po["area_ha"] for po in out_parcels if po["area_ha"] is not None]
    seen = set()
    warnings = [w for w in warnings if not (w in seen or seen.add(w))]
    return {
        "generated_at": iso_utc(),
        "weather": weather,
        "parcels": out_parcels,
        "total_area_ha": round(sum(area), 2) if area else None,
        "map_center": [round(sum(lats) / len(lats), 6), round(sum(lons) / len(lons), 6)],
        "map_bounds": [[min(lats), min(lons)], [max(lats), max(lons)]],
        "sources": SOURCES,
        "warnings": warnings,
    }


def _empty_weather(note: str) -> Dict[str, Any]:
    return {
        "current": {"temp_c": None, "humidity_pct": None, "wind_ms": None, "precip_mm": None, "station": None},
        "daily": [], "precip_7d_mm": None, "precip_14d_mm": None, "climatic_water_balance_14d_mm": None, "forecast_days": 0,
        "precip_past_7d_mm": None, "soil_temp_c": None, "warnings": [], "model": None,
        "provenance": {k: missing("IMGW-PIB / Open-Meteo", "https://open-meteo.com/", note) for k in ("current", "forecast", "warnings")},
        "notes": [note],
    }


def _build_parcel(it: Dict[str, Any], p: Optional[ParcelData], weather: Dict[str, Any],
                  soil: Tuple[Optional[Dict[str, Any]], Optional[str]], s2: Optional[Dict[str, Any]],
                  hist: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    pw: List[str] = []
    crop, soil_name = it.get("crop") or None, it.get("soil") or None
    if p is None:
        pw.append("Nie udało się pobrać granic działki z GUGiK ULDK (brak działki w tym miejscu lub usługa nie odpowiada). "
                  "Działka nie jest zmyślana – analiza satelitarna niedostępna.")
    sm, water_days, prov_sm, prov_water, _, sw = soil_block_from(soil[0], soil[1], soil_name, weather.get("daily") or [])
    pw += sw
    if s2 is None:
        s2 = {"ndvi": None, "layer": None, "warnings": [],
              "provenance": missing(sentinel.SOURCE_NAME, sentinel.SOURCE_URL,
                                    "Brak granic działki – NDVI nie zostało policzone." if p is None else "Sentinel-2: przekroczono limit czasu.")}
    pw += s2.get("warnings", [])
    out: Dict[str, Any] = {
        "parcel_id": p.parcel_id if p else (it.get("parcel_id") or None),
        "id_tag": it.get("id_tag") or (p.parcel_id if p else "Działka"),
        "crop": crop, "soil": soil_name, "slope": it.get("slope"),
        "area_ha": round(p.area_ha, 2) if p else None,
        "commune": p.commune if p else None, "county": p.county if p else None, "voivodeship": p.voivodeship if p else None,
        "region": p.region if p else None,
        "centroid": [round(p.centroid_lat, 6), round(p.centroid_lon, 6)] if p else [float(it["lat"]), float(it["lon"])],
        "geojson_coords": p.exterior_coords_lonlat() if p else [],
        "ndvi": s2.get("ndvi"),
        "soil_moisture": sm,
        "water_reserve_days": water_days,
        "layers": {"ndvi": s2.get("layer"), "ndmi": s2.get("layer_ndmi"), "true_color": s2.get("layer_true_color")},
        "history": hist,
        # "pending" = historia NDVI pobiera się w tle (GET /api/history/ndvi); "ready" = jest; "none" = brak (np. bez CDSE)
        "history_status": "ready" if hist else ("pending" if (p is not None and settings.cdse_configured) else "none"),
        # zdjęcie w barwach naturalnych wczytywane osobno (GET /api/layers/true-color), gdy jest zobrazowanie
        "layers_pending": ["true_color"] if (p is not None and s2.get("ndvi")) else [],
        "weather_local": {
            "precip_14d_mm": weather.get("precip_14d_mm"),
            "climatic_water_balance_14d_mm": weather.get("climatic_water_balance_14d_mm"),
            "station": (weather.get("current") or {}).get("station"),
        },
        "provenance": {"parcel": parcel_provenance(p), "ndvi": s2["provenance"], "soil_moisture": prov_sm, "water_reserve": prov_water},
    }
    score, label, risk = health_assessment(out["ndvi"], water_days)
    out.update(health_score=score, status_label=label, risk=risk)
    out["advice_bullets"] = advice_for(out, weather, {"days": water_days})
    seen = set()
    out["warnings"] = [w for w in pw if not (w in seen or seen.add(w))]
    return out


def warm_up_cache(force: bool = False, farms: Optional[List[str]] = None) -> Dict[str, Any]:
    """Rozgrzewa cache dla wszystkich przykładowych gospodarstw (w tle przy starcie serwera).

    Dla każdego gospodarstwa: analiza (ULDK, pogoda, gleba, NDVI/NDMI), historia NDVI 24 mies., zdjęcie w barwach naturalnych,
    historia wskaźników 36 mies. (zakładka Historia) i klimat pola (ERA5). Gospodarstwa po kolei, a zapytania CDSE ograniczone
    semaforem w sentinel.py (≤4 równoległe) – nie przekraczamy limitów API. Wyłączenie: MROLNIK_WARMUP=0.
    """
    from src.data import climate, history  # import tu: unikamy cykli przy imporcie modułu

    if not force and os.environ.get("MROLNIK_WARMUP", "1").strip().lower() in ("0", "false", "no", "nie", "off"):
        log.info("Rozgrzewanie cache wyłączone (MROLNIK_WARMUP=0)")
        return {"skipped": True}
    if settings.offline_demo:
        return {"skipped": True}
    names = farms or ([DEFAULT_FARM] + [n for n in FARM_PRESETS if n != DEFAULT_FARM])
    t0 = time.monotonic()
    done = 0
    for name in names:
        t = time.monotonic()
        try:
            res = analyze_parcels_batch(FARM_PRESETS.get(name, []), "auto")
            for po in res.get("parcels", []):
                if not po.get("parcel_id"):
                    continue
                p = fetch_parcel_by_id(po["parcel_id"])
                if p is None:
                    continue
                if settings.cdse_configured:
                    sentinel.monthly_history(p)
                    sentinel.true_color_layer(p)
                    history.monthly_indices(p)
                lat, lon = po["centroid"]
                climate.climate_summary(lat, lon)
            done += 1
            log.info("Rozgrzano cache: %s (%.1f s)", name, time.monotonic() - t)
        except Exception:  # noqa: BLE001 – rozgrzewanie nie może zatrzymać serwera
            log.exception("Błąd rozgrzewania cache dla %s", name)
    log.info("Rozgrzewanie cache zakończone: %d/%d gospodarstw w %.0f s", done, len(names), time.monotonic() - t0)
    return {"farms": done, "seconds": round(time.monotonic() - t0, 1)}

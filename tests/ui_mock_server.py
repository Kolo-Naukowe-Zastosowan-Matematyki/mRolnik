"""Serwer makietowy do testów UI (tylko dev/test, NIE jest dostarczany w produkcji).

Serwuje static/ tak jak docelowy server.py (index.html pod "/" i "/web", zasoby pod /static),
ustawia nagłówek CSP z Kontraktu 1 i zwraca dane przykładowe zgodne z Kontraktem 2
(w tym przypadki null / kind "brak" / demo). Kalkulatory korzystają z prawdziwego routera.

Uruchomienie:  python tests/ui_mock_server.py [port]     (domyślnie 8765)
Tryby awarii (do testów stanów błędu): nagłówek/parametr ?mock_fail=analyze → 503 dla /api/analyze.
"""
from __future__ import annotations

import base64
import os
import struct
import sys
import math
import zlib
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from src.web import routes_calc  # noqa: E402

STATIC = os.path.join(ROOT, "static")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
       "img-src 'self' data: blob: https://mapy.geoportal.gov.pl https://*.tile.openstreetmap.org https://tile.openstreetmap.org; "
       "connect-src 'self'; font-src 'self'; frame-ancestors 'self' https://erolnik.gov.pl https://*.gov.pl; "
       "base-uri 'self'; form-action 'self'; object-src 'none'")


def _png(width: int, height: int, pixel) -> bytes:
    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _ndvi_pixel(x, y):
    v = (x + y) / 62.0
    if (x - 20) ** 2 + (y - 12) ** 2 < 25:
        return (215, 120, 40, 170)
    return (int(200 - 170 * v), int(150 + 80 * v), int(60 - 20 * v), 160)


NDVI_URI = "data:image/png;base64," + base64.b64encode(_png(32, 32, _ndvi_pixel)).decode()
NOW = datetime(2026, 11, 15, 11, 20, tzinfo=timezone.utc)
ISO = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731


def prov(source, kind, url="", observed=None, cloud=None, res=None, note=""):
    return {"source": source, "source_url": url, "kind": kind, "observed_at": observed,
            "fetched_at": ISO(NOW), "cloud_cover_pct": cloud, "resolution": res, "note": note}


S2 = "Copernicus Sentinel-2 L2A (Copernicus Data Space Ecosystem)"
ULDK = "GUGiK ULDK (Usługa Lokalizacji Działek Katastralnych)"
OM = "Model ECMWF IFS (Open-Meteo)"

PRESETS = {
    "farms": [
        {"name": "Wielkopolskie • Śnieciska (pow. średzki)", "parcels": [
            {"id_tag": "Dz. 114/1", "lat": 52.2020, "lon": 17.2012, "crop": "Pszenica ozima", "soil": "Średnia gliniasta (klasa IIIb)"},
            {"id_tag": "Dz. 111/5", "lat": 52.2025, "lon": 17.1990, "crop": "Jęczmień jary", "soil": "Lekka (klasa IVa)"},
        ]},
        {"name": "Lubelskie • Krasnystaw (pow. krasnostawski)", "parcels": [
            {"id_tag": "Dz. 983", "lat": 50.9950, "lon": 23.2350, "crop": "Pszenica", "soil": "Less (klasa IIIa)"},
        ]},
        {"name": "Zachodniopomorskie • Pyrzyce (pow. pyrzycki)", "parcels": [
            {"id_tag": f"Dz. {200 + i}", "lat": round(53.1480 + (i // 4) * 0.004, 4), "lon": round(14.8650 + (i % 4) * 0.007, 4),
             "crop": ["Pszenica ozima", "Buraki cukrowe", "Rzepak ozimy", "Kukurydza na ziarno"][i % 4], "soil": "Czarne ziemie"}
            for i in range(16)]},
    ],
    "weather_models": {"auto": "Automatyczny"},
}


def _daily():
    out = []
    rain = [0.0, 0.4, 3.2, 6.8, 1.1, 0.0, 0.0, 0.2, 2.5, 4.0, 0.0, 0.0, 1.4, 0.6]
    for i in range(14):
        d = date(2026, 11, 15) + timedelta(days=i)
        out.append({"date": d.isoformat(), "rain_mm": rain[i], "t_max_c": round(9.5 - i * 0.35 + (i % 3), 1),
                    "t_min_c": round(3.0 - i * 0.4, 1), "et0_mm": round(0.8 - i * 0.02, 2)})
    return out


def _water_balance():
    out = []
    obs = [(0.0, 0.9), (2.1, 0.8), (0.0, 1.0), (5.4, 0.6), (0.3, 0.7), (0.0, 0.9), (1.2, 0.8)]
    for i, (p, e) in enumerate(obs):
        out.append({"date": (date(2026, 11, 8) + timedelta(days=i)).isoformat(), "precip_mm": p, "et0_mm": e, "kind": "obs"})
    for d in _daily():
        out.append({"date": d["date"], "precip_mm": d["rain_mm"], "et0_mm": d["et0_mm"], "kind": "forecast"})
    return out


PIDS: dict = {}


def analysis(parcels_req):
    parcels = []
    for i, p in enumerate(parcels_req):
        lat, lon = float(p["lat"]), float(p["lon"])
        tag = p.get("id_tag") or f"Działka {i + 1}"
        if i % 2 == 0:
            d = 0.0022
            coords = [[lon - d, lat - d * 0.6], [lon + d, lat - d * 0.7], [lon + d * 1.1, lat + d * 0.6], [lon - d * 0.9, lat + d * 0.7], [lon - d, lat - d * 0.6]]
            PIDS["302501_2.0012.114/1" if i == 0 else f"302501_2.0012.{100 + i}"] = coords
            parcels.append({
                "parcel_id": "302501_2.0012.114/1" if i == 0 else f"302501_2.0012.{100 + i}",
                "id_tag": tag, "crop": p.get("crop", "Pszenica ozima"), "soil": p.get("soil"),
                "area_ha": 4.87, "commune": "Środa Wielkopolska", "county": "średzki", "voivodeship": "wielkopolskie",
                "centroid": [lat, lon], "geojson_coords": coords,
                "ndvi": {"acquired_on": "2026-11-12", "mean": 0.68, "min": 0.21, "max": 0.84, "std": 0.09, "healthy_pct": 81.0, "stress_pct": 14.0, "bare_soil_pct": 5.0},
                "soil_moisture": {"surface_pct": 27.4, "root_zone_pct": 31.2, "trend_7d": "rośnie", "field_capacity_pct": 34.0, "wilting_point_pct": 14.0},
                "water_reserve_days": 18, "health_score": 0.78, "status_label": "Dobra kondycja roślin", "risk": "Niskie",
                "advice_bullets": [
                    "Łan w dobrej kondycji – nie trzeba nic robić przed zimą.",
                    "Sprawdź zachodni skraj pola: plama słabszych roślin (ok. 5% powierzchni).",
                ],
                "layers": {"ndvi": {"image": NDVI_URI, "bounds": [[lat - d, lon - d * 1.1], [lat + d * 0.8, lon + d * 1.2]]},
                           "ndmi": {"image": NDVI_URI, "bounds": [[lat - d, lon - d * 1.1], [lat + d * 0.8, lon + d * 1.2]], "mean": 0.12,
                                    "legend": [{"from": -1, "to": 0, "color": "#f46d43", "label": "niedobór wody w roślinach"}, {"from": 0, "to": 1, "color": "#313695", "label": "dobre uwodnienie"}]},
                           "ndwi": {"image": NDVI_URI, "bounds": [[lat - d, lon - d * 1.1], [lat + d * 0.8, lon + d * 1.2]], "mean": -0.31, "water_pct": 4.0, "wet_pct": 9.0,
                                    "legend": [{"from": -1, "to": -0.3, "color": "#8c6d31", "label": "sucha powierzchnia / gęsty łan"}, {"from": -0.3, "to": 0, "color": "#e7d9a8", "label": "brak wody na powierzchni"},
                                               {"from": 0, "to": 0.2, "color": "#6baed6", "label": "podmokło – możliwe zastoiska"}, {"from": 0.2, "to": 1, "color": "#08306b", "label": "woda stojąca"}]},
                           "true_color": None},
                "history": {"ndvi_monthly": [{"month": f"2026-{m:02d}", "mean": v} for m, v in zip(range(4, 12), [0.42, 0.66, 0.78, 0.71, 0.35, 0.28, 0.52, 0.68])],
                            "provenance": prov(S2, "pomiar", "https://dataspace.copernicus.eu/", None, None, "10 m", "Średnie miesięczne z bezchmurnych zobrazowań")},
                "provenance": {
                    "parcel": prov(ULDK, "pomiar", "https://uldk.gugik.gov.pl/", None, None, None, "Granice działki ewidencyjnej z ULDK"),
                    "ndvi": prov(S2, "pomiar", "https://dataspace.copernicus.eu/", "2026-11-12T10:03:00Z", 8.0, "10 m",
                                 "Najnowsze bezchmurne zobrazowanie z ostatnich 30 dni"),
                    "soil_moisture": prov("ERA5-Land / ICON (Open-Meteo)", "model", "https://open-meteo.com/", "2026-11-15T10:00:00Z", None, "9 km",
                                          "Wilgotność gleby z modelu, nie pomiar na polu"),
                },
                "warnings": [],
            })
        else:
            parcels.append({
                "parcel_id": None, "id_tag": tag, "crop": p.get("crop"), "soil": p.get("soil"), "area_ha": None,
                "commune": None, "county": None, "voivodeship": None, "centroid": [lat, lon], "geojson_coords": [],
                "ndvi": None, "soil_moisture": None, "water_reserve_days": None, "health_score": None,
                "status_label": "Brak danych", "risk": "Brak danych",
                "advice_bullets": ["Nie udało się ustalić granic działki – sprawdź numer działki w ewidencji."],
                "layers": {"ndvi": None, "true_color": None}, "history": None,
                "provenance": {
                    "parcel": prov(ULDK, "brak", "https://uldk.gugik.gov.pl/", note="ULDK nie zwróciło działki dla tego punktu"),
                    "ndvi": prov(S2, "brak", note="Brak bezchmurnego zobrazowania w ostatnich 30 dniach"),
                    "soil_moisture": prov("Open-Meteo", "brak", note="Źródło chwilowo niedostępne"),
                },
                "warnings": ["Nie znaleziono działki ewidencyjnej w ULDK – działka nie jest zmyślana, analiza pominięta."],
            })
    return {
        "generated_at": ISO(NOW),
        "weather": {
            "current": {"temp_c": 7.4, "humidity_pct": 86, "wind_ms": 4.2, "precip_mm": None, "station": "Poznań (IMGW-PIB)"},
            "daily": _daily(),
            "precip_7d_mm": 11.5, "precip_14d_mm": 20.2, "climatic_water_balance_14d_mm": 9.8, "soil_temp_c": None,
            "warnings": [{"level": 1, "event": "Silny wiatr", "valid_from": "2026-11-16T06:00:00Z", "valid_to": "2026-11-16T20:00:00Z", "source": "IMGW-PIB"}],
            "provenance": {
                "current": prov("IMGW-PIB dane synoptyczne", "pomiar", "https://danepubliczne.imgw.pl/", "2026-11-15T11:00:00Z", note="Stacja synoptyczna Poznań, 38 km od działki"),
                "forecast": prov(OM, "prognoza", "https://open-meteo.com/", note="Prognoza 14-dniowa"),
                "warnings": prov("IMGW-PIB ostrzeżenia meteorologiczne", "pomiar", "https://meteo.imgw.pl/"),
            },
        },
        "parcels": parcels,
        "total_area_ha": 4.87,
        "map_center": [parcels_req[0]["lat"], parcels_req[0]["lon"]] if parcels_req else [52.0, 19.0],
        "map_bounds": None,
        "sources": [
            {"name": S2, "url": "https://dataspace.copernicus.eu/", "kind": "pomiar", "licence": "Copernicus – licencja otwarta"},
            {"name": "IMGW-PIB", "url": "https://danepubliczne.imgw.pl/", "kind": "pomiar", "licence": "Dane publiczne IMGW-PIB"},
            {"name": "Open-Meteo", "url": "https://open-meteo.com/", "kind": "prognoza", "licence": "CC BY 4.0"},
            {"name": ULDK, "url": "https://uldk.gugik.gov.pl/", "kind": "pomiar", "licence": "Dane otwarte GUGiK"},
        ],
        "warnings": ["Dla 1 działki nie udało się pobrać granic z ULDK – pokazano tylko punkt."],
    }


def create_app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def csp(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    @app.get("/web")
    def index():
        resp = FileResponse(os.path.join(STATIC, "index.html"))
        resp.set_cookie("mrolnik_csrf", "test-csrf-token", samesite="lax")
        return resp

    @app.get("/mobile")
    def mobile():
        return RedirectResponse("/web")

    @app.get("/auth/me")
    def me():
        return {"authenticated": True, "user": {"id": "dev", "name": "Użytkownik lokalny", "role": "rolnik"},
                "auth_mode": "dev", "login_url": "/auth/login"}

    @app.post("/auth/logout")
    def logout():
        return {"ok": True}

    def _mock_catalog(services: bool = True):
        from src.data import profit_data
        costs = profit_data.load_costs()["crops"]
        Y = {"pszenica_ozima": 68, "pszenica_jara": 50, "jeczmien_ozimy": 60, "jeczmien_jary": 48, "pszenzyto": 52, "zyto": 38,
             "owies": 38, "kukurydza": 82, "rzepak_ozimy": 33, "burak_cukrowy": 640, "ziemniak": 290, "groch": 30, "bobik": 32, "lubin": 22}
        P = {"pszenica_ozima": 92, "pszenica_jara": 92, "jeczmien_ozimy": 80, "jeczmien_jary": 80, "pszenzyto": 76, "zyto": 68, "owies": 72,
             "kukurydza": 82, "rzepak_ozimy": 195, "burak_cukrowy": 21, "ziemniak": 62}
        out = []
        for k, (name, _, pname, group, legume) in profit_data.CATALOG.items():
            c = costs[k]
            price = P.get(k) or round(c["odr_price_zl_t"] / 10, 2)
            cost = round(c["direct_cost"] + (c["services"] if services else 0), 2)
            out.append({"key": k, "name": name, "group": group, "legume": legume, "yield_dt_ha": Y[k], "yield_source": "makieta",
                        "price_zl_dt": price, "price_source": "makieta", "direct_cost_zl_ha": c["direct_cost"], "services_zl_ha": c["services"],
                        "cost_zl_ha": cost, "cost_source": "ODR 2025", "contract": profit_data.NEEDS_CONTRACT.get(k),
                        "margin_zl_ha": round(Y[k] * price - cost)})
        return out

    def _mock_prov():
        from src.data import profit_data
        return {"costs": profit_data.costs_provenance(), "prices": prov("GUS BDL – ceny skupu", "statystyka"), "yields": prov("GUS BDL – plony", "statystyka")}

    @app.get("/api/calculators/profit/catalog")
    def profit_catalog(voivodeship: str = "wielkopolskie", include_services: bool = True):
        return {"voivodeship": voivodeship, "crops": _mock_catalog(include_services), "include_services": include_services, "provenance": _mock_prov()}

    @app.post("/api/calculators/profit")
    async def profit_calc(request: Request):
        from src.data import profit_data
        body = await request.json()
        cat = {c["key"]: c for c in _mock_catalog(body.get("include_services", True))}
        rows, total, miss = [], 0, 0
        for p in body["parcels"]:
            k = p.get("crop_key") or profit_data.crop_key(p.get("crop"))
            ref = cat.get(k or "", {})
            y = p.get("yield_dt_ha") or ref.get("yield_dt_ha"); pr = p.get("price_zl_dt") or ref.get("price_zl_dt"); c = p.get("cost_zl_ha") if p.get("cost_zl_ha") is not None else ref.get("cost_zl_ha")
            row = {"id": p["id"], "area_ha": p["area_ha"], "crop": p.get("crop"), "crop_key": k}
            if None in (y, pr, c):
                miss += 1; row["note"] = "Brak danych dla tej uprawy – wpisz plon, cenę i koszty."
            else:
                row.update(revenue_zl=round(p["area_ha"] * y * pr), direct_cost_zl=round(p["area_ha"] * c), margin_zl=round(p["area_ha"] * (y * pr - c)))
                total += row["margin_zl"]
            rows.append(row)
        return {"voivodeship": body["voivodeship"], "rows": rows, "total_margin_zl": total, "incomplete": miss}

    @app.post("/api/calculators/optimal-plan")
    async def optimal_plan_mock(request: Request):
        from src.data import profit_data
        from src.calculators.profit import CropEcon, ParcelIn, optimal_plan
        body = await request.json()
        cat = _mock_catalog(body.get("include_services", True))
        allow = set(body.get("allowed") or [k for k in profit_data.CATALOG if k not in profit_data.NEEDS_CONTRACT])
        ins = [ParcelIn(str(p["id"]), p["area_ha"], profit_data.crop_key(p.get("crop")), arable=profit_data.crop_key(p.get("crop")) != "tuz") for p in body["parcels"]]
        res = optimal_plan(ins, [CropEcon(c["key"], c["name"], c["yield_dt_ha"], c["price_zl_dt"], c["cost_zl_ha"], c["group"], c["legume"]) for c in cat if c["key"] in allow])
        names = {c["key"]: c["name"] for c in cat}
        rows = [{**r, "id": p["id"], "crop": p.get("crop"), "plan_name": names.get(r["plan"]) if not r["kept"] else p.get("crop")} for r, p in zip(res.rows, body["parcels"])]
        return {"voivodeship": body["voivodeship"], "rows": rows, "total_margin_zl": res.total_margin_zl, "current_margin_zl": res.current_margin_zl,
                "allowed": sorted(allow), "checks": res.checks, "feasible": res.feasible, "notes": res.notes, "catalog": cat, "provenance": _mock_prov()}

    @app.get("/api/ortho/date")
    def ortho_date(lat: float = 52.2, lon: float = 17.2):
        return {"date": "2025-05-25", "pixel_m": 0.25, "sheet": "N-33-143-A-d-4-1",
                "provenance": prov("GUGiK – skorowidz ortofotomap (data nalotu)", "pomiar", "https://www.geoportal.gov.pl/")}

    @app.get("/api/presets")
    def presets():
        return PRESETS

    @app.post("/api/analyze")
    async def analyze(request: Request):
        if request.query_params.get("mock_fail") or request.headers.get("referer", "").find("mock_fail=analyze") >= 0:
            return JSONResponse({"detail": "Usługa chwilowo niedostępna"}, status_code=503)
        body = await request.json()
        if request.headers.get("x-csrf-token") != "test-csrf-token":
            return JSONResponse({"detail": "Brak tokenu CSRF"}, status_code=403)
        return analysis(body.get("parcels", []))

    @app.get("/api/search-location")
    def search(q: str = ""):
        return {"results": [
            {"display_name": "Środa Wielkopolska, powiat średzki, wielkopolskie", "lat": 52.2286, "lon": 17.2770},
            {"display_name": "<img src=x onerror=alert(1)> Śnieciska", "lat": 52.2020, "lon": 17.2012},
        ] if q else []}

    @app.get("/api/sources/status")
    def sources():
        return [
            {"name": "Copernicus Data Space (Sentinel-2)", "ok": True, "configured": True, "last_success": ISO(NOW), "message": "OK"},
            {"name": "IMGW-PIB", "ok": True, "configured": True, "last_success": "2026-11-15T11:05:00Z", "message": "OK"},
            {"name": "GUGiK ULDK", "ok": False, "configured": True, "last_success": "2026-11-14T18:00:00Z", "message": "Przekroczony czas odpowiedzi"},
            {"name": "Open-Meteo", "ok": True, "configured": True, "last_success": ISO(NOW), "message": "OK"},
        ]

    @app.get("/api/hydromelior/alerts")
    def alerts(lat: float = 52.2, lon: float = 17.2):
        return {"alerts": [
            {"level": "info", "title": "Zastawki można otworzyć", "text": "W ciągu 7 dni prognozowane 11 mm opadu, bilans wodny dodatni – nie trzeba zatrzymywać wody."},
            {"level": "ostrzezenie", "title": "Wysoki stan rowu po opadach", "text": "Po 18.11 sprawdź drożność przepustów."},
        ],
            "water_balance": _water_balance(),
            "river_gauge": {"station": "Śrem", "river": "Warta", "level_cm": 182, "trend": "rośnie", "observed_at": "2026-11-15T10:00:00Z",
                            "warning_level_cm": 230, "alarm_level_cm": 250, "high_level_cm": 186, "low_level_cm": 137,
                            "series_7d": [{"t": (datetime(2026, 11, 9, 2, tzinfo=timezone.utc) + timedelta(hours=3 * i)).isoformat(), "level_cm": 135 + 25 * math.sin(i / 9) ** 2} for i in range(56)],
                            "provenance": prov("IMGW-PIB dane hydrologiczne", "pomiar", "https://danepubliczne.imgw.pl/", "2026-11-15T10:00:00Z", note="Wodowskaz 21 km od działki")},
            "provenance": {"weather": prov(OM, "prognoza", "https://open-meteo.com/"), "hydro": prov("IMGW-PIB hydro", "pomiar", "https://danepubliczne.imgw.pl/", "2026-11-15T10:00:00Z")}}

    @app.get("/api/hydromelior/network")
    def network(lat: float = 52.2, lon: float = 17.2):
        return {"demo": True, "note": "Dane demonstracyjne sieci melioracyjnej (Śnieciska) – nie są to dane z ewidencji urządzeń melioracyjnych.",
                "features": [
                    {"type": "Feature", "properties": {"name": "Rów R-1", "kind": "rów"},
                     "geometry": {"type": "LineString", "coordinates": [[17.196, 52.200], [17.200, 52.2005], [17.205, 52.2012]]}},
                    {"type": "Feature", "properties": {"name": "Zastawka Z-1", "kind": "zastawka", "status": "otwarta"},
                     "geometry": {"type": "Point", "coordinates": [17.200, 52.2005]}},
                ], "provenance": prov("Dane demonstracyjne mRolnik", "demo")}

    def _bounds(parcel_ids: str, parcel_id: str, lat: float, lon: float):
        ids = [x for x in (parcel_ids or parcel_id).split(",") if x]
        pts = [c for pid in ids for c in PIDS.get(pid, [])] or [[lon - 0.002, lat - 0.002], [lon + 0.002, lat + 0.002]]
        return [[min(c[1] for c in pts), min(c[0] for c in pts)], [max(c[1] for c in pts), max(c[0] for c in pts)]], len(ids)

    @app.get("/api/history/indices")
    def hist_indices(parcel_ids: str = "", parcel_id: str = "", lat: float = 52.2, lon: float = 17.2, start: str = "2023-12", end: str = "2026-11"):
        _, n = _bounds(parcel_ids, parcel_id, lat, lon)
        months = []
        t0 = int(start[:4]) * 12 + int(start[5:7]) - 1
        t1 = int(end[:4]) * 12 + int(end[5:7]) - 1
        for t in range(t0, t1 + 1):
            y, m = t // 12, t % 12 + 1
            base = 0.35 + 0.3 * math.sin((m - 3) / 12 * math.pi) + (0.02 * n if n > 1 else 0)
            months.append({"month": f"{y}-{m:02d}", "ndvi": round(base, 3), "ndmi": round(base - 0.4, 3), "ndwi": round(-base + 0.1 * (m in (3, 4)), 3), "evi": round(base * 0.8, 3),
                           "bare_pct": round(max(0, 60 - base * 100), 1), "valid_pct": 90.0})
        note = (f"Zaznaczone działki: {n} – średnia ze wszystkich pikseli. " if n > 1 else "") + "Makieta."
        return {"months": months, "parcel_ids": (parcel_ids or parcel_id).split(","),
                "provenance": prov(S2, "pomiar", "https://dataspace.copernicus.eu/", note=note)}

    @app.get("/api/history/frame")
    def hist_frame(month: str, index: str = "ndvi", parcel_ids: str = "", parcel_id: str = "", lat: float = 52.2, lon: float = 17.2):
        b, _ = _bounds(parcel_ids, parcel_id, lat, lon)
        photo = index == "photo"
        return {"month": month, "index": index, "image": NDVI_URI, "bounds": b, "mean": None if photo else 0.61,
                "legend": [] if photo else [{"color": "#1a9850", "label": "gęsty łan"}, {"color": "#d73027", "label": "gleba odsłonięta"}],
                "provenance": prov(S2, "pomiar", "https://dataspace.copernicus.eu/", note="Makieta klatki.")}

    @app.get("/api/history/climate")
    def hist_climate(lat: float = 52.2, lon: float = 17.2, start: str = "", end: str = ""):
        rows = [{"month": f"2026-{m:02d}", "precip_mm": 30 + m * 3, "precip_normal_mm": 45.0, "soil_moisture_pct": [12.0, 16.0, 22.0, 26.0, 30.0, 33.0, 36.0, 31.0, 24.0, 18.0, 15.0, 20.0][m - 1], "complete": m < 11}
                for m in range(1, 12)]
        return {"months": rows, "normals_station": "Szczecin",
                "provenance": {"precip": prov("ERA5", "reanaliza", "https://open-meteo.com/"), "normals": prov("IMGW-PIB", "statystyka", "https://imgw.pl/")}}

    @app.get("/api/reference/rules")
    def ref_rules(lat: float = 52.2, lon: float = 17.2, parcel_id: str = "", crop: str = ""):
        from datetime import datetime as _dt, timedelta as _td
        now = _dt(2026, 11, 16, 20, 0)
        hourly = [{"time": (now + _td(hours=h)).strftime("%Y-%m-%dT%H:00"), "wind_ms": 5.5 if h < 30 else 3.2, "gust_ms": 9.0,
                   "daylight": 6 <= (now + _td(hours=h)).hour <= 20, "allowed": "nie" if h < 30 else "tak"} for h in range(0, 120)]
        soil = [{"date": (now + _td(days=d)).strftime("%Y-%m-%d"), "soil_t_c": round(4.5 - d * 0.2, 1), "hours": 24, "forecast": d > 0} for d in range(-7, 14)]
        daily = [{"date": (now + _td(days=d)).strftime("%Y-%m-%d"), "t_mean_c": 3.0, "t_min_c": -1.5} for d in range(-7, 14)]
        P = lambda n, k: prov(n, k, "https://isap.sejm.gov.pl/", note="Atrapa.")
        return {"nitrates": {"status": "zakaz", "text": "Poza terminem: nawozów azotowych na gruntach ornych nie stosuje się do 1 marca 2027 r. (od 1 lutego – po przejściu temperatury przez próg 3°C przez 5 dni).",
                             "deadline_basis": "gmina spoza załączników nr 2 i 3 – termin podstawowy 20 października", "temp_threshold_c": 3.0,
                             "today": date.today().isoformat(),
                             "windows": [{"label": "Nawozy azotowe mineralne i naturalne płynne (grunty orne)", "from": "2026-03-01", "to": "2026-10-20"},
                                         {"label": "Nawozy naturalne stałe (grunty orne)", "from": "2026-03-01", "to": "2026-10-31"},
                                         {"label": "Luty – warunkowo (próg 3°C przez 5 dni)", "from": "2026-02-01", "to": "2026-02-28", "conditional": True}],
                             "provenance": P("Program azotanowy (Dz.U. 2023 poz. 244)", "przepis")},
                "spraying": {"wind_max_ms": 4.0, "wind_max_drift_reduction_ms": 5.0, "hourly": hourly, "provenance": P("Rozporządzenie MRiRW (Dz.U. 2014 poz. 516)", "przepis"),
                             "forecast_provenance": prov("Open-Meteo", "prognoza", "https://open-meteo.com/")},
                "temperature": {"daily": daily, "threshold_c": 3.0, "first_5day_run_start": None, "provenance": prov("Open-Meteo", "prognoza", "https://open-meteo.com/")},
                "sowing": {"daily": soil, "thresholds": [{"crop": "kukurydza", "min_c": 8.0, "max_c": 10.0}, {"crop": "soja", "min_c": 10.0, "max_c": None}],
                           "crop_threshold": None, "first_day_at_threshold": None, "not_found_note": "",
                           "provenance": prov("Zalecenia ODR", "zalecenie", "https://new.podr.pl/"), "soil_provenance": prov("Open-Meteo – temperatura gleby", "model", "https://open-meteo.com/")}}

    @app.get("/api/county/ndvi")
    def county_ndvi(lat: float = 52.2, lon: float = 17.2, day: str = "2026-11-12", ndvi: float = 0.5):
        import time as _t
        _t.sleep(0.4)
        pal = [(0.1, "#a0522d"), (0.25, "#e8c170"), (0.4, "#f2d24b"), (0.5, "#a6d96a"), (0.65, "#4daf4a"), (9, "#0b5d1e")]
        segs = []
        for i in range(20):
            v = round(0.12 + 0.68 * ((i + 0.5) / 20) ** 1.3, 3)
            segs.append({"from_pct": i * 5, "to_pct": i * 5 + 5, "ndvi": v, "color": next(c for t, c in pal if v < t)})
        rank = round(sum(1 for s in segs if s["ndvi"] < ndvi) * 100 / 20)
        return {"status": "ok", "county": {"teryt": "3025", "name": "powiat średzki"}, "day": day, "season": 2026, "ndvi": ndvi,
                "segments": segs, "median": segs[10]["ndvi"], "p10": segs[2]["ndvi"], "p90": segs[18]["ndvi"], "rank_pct": rank,
                "n_px": 41200, "cropland_km2": 612.0, "compared_km2": 412.0, "coverage_pct": 67,
                "provenance": prov(S2, "pomiar", "https://dataspace.copernicus.eu/", day, None, "ok. 100 m (1 ha)",
                                   "NDVI pól uprawnych powiatu z tego samego dnia (atrapa).")}

    @app.get("/api/county/monthly")
    def county_monthly(points: str = "", start: str = "", end: str = ""):
        import math as _m
        import time as _t
        _t.sleep(1.0)
        y0, m0 = (int(start[:4]), int(start[5:7])) if start else (2023, 12)
        y1, m1 = (int(end[:4]), int(end[5:7])) if end else (2026, 11)
        rows, y, m = [], y0, m0
        while (y, m) <= (y1, m1):
            v = 0.45 + 0.3 * _m.sin((m - 3) / 12 * 2 * _m.pi)
            rows.append({"month": f"{y}-{m:02d}", "ndvi": None if m in (1, 12) else round(v, 3)})
            m += 1
            if m > 12:
                y, m = y + 1, 1
        return {"months": rows, "counties": [{"teryt": "3025", "name": "powiat średzki", "weight_pct": 100}],
                "provenance": prov(S2 + " – średnia w powiecie", "pomiar", "https://dataspace.copernicus.eu/", note="Atrapa średniej w powiecie.")}

    @app.get("/api/reference/ndvi")
    def ref_ndvi(parcel_id: str = "", parcel_ids: str = "", lat: float = 52.2, lon: float = 17.2):
        months = [{"month": m, "mean": round(0.35 + 0.25 * (1 - abs(m - 6.5) / 5.5), 3), "n_years": 7, "min": 0.2, "max": 0.8}
                  for m in range(1, 13)]
        return {"parcel_id": parcel_id or None, "period": "2019–2025", "months": months,
                "provenance": prov("Copernicus Sentinel-2 L2A – średnia wieloletnia", "statystyka", "https://dataspace.copernicus.eu/",
                                   note="Makieta: średnia wieloletnia NDVI.")}

    @app.get("/api/reference/yields")
    def ref_yields(crop: str = "", voivodeship: str = "", lat: float = 0, lon: float = 0):
        if not voivodeship:
            return JSONResponse({"detail": "Podaj województwo"}, status_code=422)
        years = [{"year": y, "voivodeship": 50.0 + (y - 2016), "poland": 47.0 + (y - 2016) * 0.8} for y in range(2016, 2026)]
        return {"crop": crop, "gus_crop": "pszenica", "variable_id": 4332, "unit": "dt/ha", "available": True,
                "voivodeship": {"name": "wielkopolskie", "teryt": "30"}, "years": years, "note": "",
                "voivodeship_mean_dt_ha": 54.5, "poland_mean_dt_ha": 50.6,
                "provenance": prov("GUS – Bank Danych Lokalnych, zmienna 4332", "statystyka", "https://bdl.stat.gov.pl/",
                                   note="Makieta: plony GUS.")}

    @app.get("/api/reference/prices")
    def ref_prices(crops: str = "", voivodeship: str = "", lat: float = 0, lon: float = 0):
        table = {"pszenica": (4859, 82.46, 82.70), "jęczmień": (4861, 78.10, 75.40), "rzepak i rzepik": (4868, 210.92, 203.45),
                 "kukurydza (ziarno)": (284047, 71.93, 70.61), "buraki cukrowe": (4867, 13.85, 17.15)}
        keys = {"pszenic": "pszenica", "jęczmie": "jęczmień", "rzepak": "rzepak i rzepik", "kukurydz": "kukurydza (ziarno)", "burak": "buraki cukrowe"}
        rows, seen = [], {}
        for c in [x for x in crops.split("|") if x]:
            prod = next((v for k, v in keys.items() if k in c.lower()), None)
            if prod in seen:
                seen[prod]["crops"].append(c)
                continue
            if prod is None:
                rows.append({"crops": [c], "variable_id": None, "product": None, "unit": None, "available": False,
                             "note": "GUS nie publikuje ceny skupu tej uprawy."})
                continue
            var, cur, prev = table[prod]
            r = {"crops": [c], "variable_id": var, "product": prod, "unit": "zł/dt", "available": True, "scope": "województwo",
                 "year": 2025, "price": cur, "prev_year": 2024, "prev_price": prev, "change": round(cur - prev, 2),
                 "change_pct": round((cur - prev) / prev * 100, 1), "poland_price": cur, "poland_change_pct": None}
            series = {2025: cur, 2024: prev, 2023: prev * 0.92, 2022: prev * 1.18, 2021: prev * 0.86, 2020: prev * 0.74}
            r["changes"] = [{"year": y, "prev_year": y - 1, "change": round(series[y] - series[y - 1], 2),
                             "change_pct": round((series[y] - series[y - 1]) / series[y - 1] * 100, 1)} for y in range(2025, 2020, -1)]
            r["base_year"], r["base_price"] = 2020, round(series[2020], 2)
            seen[prod] = r
            rows.append(r)
        return {"voivodeship": {"name": "wielkopolskie", "teryt": "30"}, "rows": rows,
                "provenance": prov("GUS – Bank Danych Lokalnych: ceny skupu", "statystyka", "https://bdl.stat.gov.pl/", note="Makieta: ceny GUS.")}

    @app.get("/api/hydromelior/coordination")
    def coordination(lat: float = 52.2, lon: float = 17.2):
        return {"demo": True, "note": "Gospodarstwa zanonimizowane – dane demonstracyjne.", "farms": [{"label": "Gospodarstwo A"}, {"label": "Gospodarstwo B"}]}

    @app.get("/api/hydromelior/ecoscheme")
    def ecoscheme(lat: float = 52.2, lon: float = 17.2, parcel_id: str = ""):
        return {"informational": True,
                "disclaimer": "Informacja pomocnicza – nie stanowi dowodu ani wniosku do ARiMR.",
                "criteria": [
                    {"name": "Retencjonowanie wody na TUZ", "status": "brak danych", "evidence": "Brak danych o zalaniu z Sentinel-1", "provenance": prov("Sentinel-1", "brak")},
                    {"name": "Działka w zlewni z urządzeniami melioracyjnymi", "status": "spełnione", "evidence": "Rów w odległości 120 m (dane DEMO)", "provenance": prov("Dane demonstracyjne", "demo")},
                    {"name": "Okres utrzymania wody (min. 12 dni)", "status": "niespełnione", "evidence": "Szacowany zapas wody 8 dni", "provenance": prov(OM, "model")},
                ], "summary": "Część kryteriów wymaga danych z kontroli na miejscu."}

    @app.get("/api/hydromelior/ecoscheme-download", response_class=HTMLResponse)
    def eco_dl():
        return "<h1>DOKUMENT INFORMACYJNY – nie stanowi dowodu ani wniosku do ARiMR</h1>"

    app.include_router(routes_calc.router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

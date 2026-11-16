# mRolnik: plan naprawy po audycie i kontrakt między modułami

Audyt: https://claude.ai/artifact/HWVg1c1ZRYAPXxEDuunQpo (streszczenie w sekcji "Zakres" niżej).
Kontekst: prototyp HackNation (zadanie KOWR), docelowo moduł osadzany w erolnik.gov.pl.
Środowisko docelowe: Windows, Python 3.12, uruchamiane lokalnie przez `.bat`. Język UI i komunikatów: polski.

## Zasady wspólne (obowiązują wszystkie strumienie)

1. **Uczciwość danych ponad efekt.** Żadna liczba nie może udawać pomiaru. Każda wartość w API ma
   proweniencję (patrz kontrakt). Gdy źródło nie odpowiada: `null` + `kind: "brak"` + komunikat,
   nigdy wartość zmyślona/syntetyczna podpisana jak pomiar. Dane demonstracyjne tylko z flagą `demo: true`.
2. **Bez pickle.** Cache wyłącznie przez `src/core/cache.py` (`TTLCache`, SQLite+JSON, TTL, limit wpisów).
3. **Konfiguracja i klucze** wyłącznie przez `src/core/config.py` (`settings`), z env / pliku `.env`.
   Nowe zmienne dopisz do `.env.example` w sekcji swojego strumienia (plik współdzielony: tylko dopisuj swoją sekcję).
4. **Własność plików** (niżej) jest twarda: nie edytuj plików innego strumienia. Jeśli potrzebujesz zmiany
   u innych, opisz ją w raporcie końcowym.
5. **Nie uruchamiaj** `git add/commit/checkout/stash/reset` (integrator commituje). `git diff`/`git status` wolno.
6. **Brak sieci w sandboxie**: zewnętrzne API (CDSE, IMGW, ULDK, Open-Meteo, GUGiK) są tu zablokowane.
   Pisz kod wg oficjalnej dokumentacji API, a testy rób na zamockowanych odpowiedziach (`unittest.mock`/
   `monkeypatch` na `requests`). Wyjątek: `pypi.org` i `registry.npmjs.org` działają (instalacja pakietów, `npm pack`).
7. **Testy** w `tests/` (pytest), każdy strumień w swoich plikach. `python -m pytest -q tests/` musi przechodzić.
8. **Zależności**: nie edytuj `requirements.txt` (integrator). Wypisz potrzebne pakiety (z wersjami) w raporcie.
   Preferuj lekkie, czysto-pythonowe zależności z kołami na Windows (requests, numpy, shapely, pyproj, pillow).
9. Logowanie przez `logging.getLogger(__name__)`, nie `print`; żadnego `except Exception: pass` bez logu.

## Własność plików

| Strumień | Pliki (wyłącznie) |
|---|---|
| **A. Bezpieczeństwo** | `server.py`, `src/security/**`, `src/web/routes_search.py`, `src/web/routes_auth.py` (nowy), `URUCHOM_*.bat`, `URUCHOM_MROLNIK.ps1`, `tests/test_security_*.py`, `docs/BEZPIECZENSTWO.md` |
| **B. Dane** | `src/data/**`, `src/hydromelior/**`, `src/api_service.py`, `src/analysis/**`, `src/web/routes_analysis.py`, `tests/test_data_*.py`, `docs/ZRODLA_DANYCH.md` |
| **C. Kalkulatory + GUI** | `src/calculators/**`, `src/web/routes_calc.py`, `static/**`, `tests/test_calc_*.py`, `tests/test_ui_*.py`, `docs/GUI.md` |
| Integrator | `src/core/**`, `requirements.txt`, `README.md`, `.env.example` (szkielet), `tests/conftest.py`, usuwanie martwego kodu |

## Kontrakt 1: uwierzytelnienie i nagłówki (A dostarcza, C konsumuje)

- `server.py` dołącza routery z `dependencies=[Depends(require_user)]` dla wszystkich `/api/*`
  (oprócz `/api/health`). Routery B i C NIE implementują auth same.
- Tryby (`MROLNIK_AUTH_MODE`): `dev` (tylko gdy host to 127.0.0.1/localhost: automatyczna sesja
  lokalnego użytkownika), `password` (konto lokalne, hasło z env jako hash), `oidc` (generyczny
  OpenID Connect: login.gov.pl / SSO eRolnika, adresy z env). Interfejs dostawcy wymienny.
- Sesja: cookie `mrolnik_session` (HttpOnly, SameSite=Lax, Secure w prod). CSRF: cookie `mrolnik_csrf`
  (czytelne z JS) + nagłówek `X-CSRF-Token` wymagany dla POST/PUT/PATCH/DELETE.
- Endpointy: `GET /auth/me` → `{"authenticated": bool, "user": {"id","name","role"}|null, "auth_mode": str, "login_url": str}`;
  `GET /auth/login`, `POST /auth/login` (password), `GET /auth/callback` (oidc), `POST /auth/logout`. Strona logowania serwowana przez A (prosta, w stylu gov.pl, bez inline JS).
- Odpowiedź 401 JSON: `{"detail": "...", "login_url": "/auth/login"}`. 429: `{"detail": "..."}` + `Retry-After`.
- Frontend: wszystkie wywołania przez jeden helper `api(path, opts)` z `credentials: "same-origin"`,
  nagłówkiem `X-CSRF-Token` z cookie `mrolnik_csrf`; na 401 przekierowanie na `login_url?next=<bieżąca ścieżka>`.
- **CSP** (A ustawia, C musi spełnić): `default-src 'self'; script-src 'self'; style-src 'self';
  img-src 'self' data: blob: https://mapy.geoportal.gov.pl https://*.tile.openstreetmap.org https://tile.openstreetmap.org;
  connect-src 'self'; font-src 'self'; frame-ancestors 'self' https://erolnik.gov.pl https://*.gov.pl; base-uri 'self'; form-action 'self'; object-src 'none'`.
  Wniosek dla C: **zero inline `<script>`, zero `onclick=`/`style=` w HTML, zero CDN** (biblioteki
  w `static/vendor/`, pobrane przez `npm pack`). Style elementów dynamicznych przez klasy CSS / CSSOM (`el.style.x = ...` w JS jest dozwolone przez CSP).
- Limity i walidacja: A dostarcza middleware rate-limit (per IP/sesja) i limit rozmiaru body (np. 256 KB).
  Walidację pól swoich endpointów robi właściciel routera (Pydantic v2 z zakresami), wg reguł:
  lat 49.0–55.0, lon 14.0–24.2, maks. 50 działek na `/api/analyze`, stringi ≤ 120 znaków,
  enumy zamiast dowolnych stringów, odrzucanie nieznanych pól (`extra="forbid"`).

## Kontrakt 2: odpowiedź `/api/analyze` i proweniencja (B dostarcza, C konsumuje)

Żądanie: `{"parcels": [{"lat","lon","crop"?,"soil"?,"id_tag"?,"slope"?,"parcel_id"?}], "weather_model": "auto"|"ecmwf"|"icon"|"gfs"}`.
(`drought_factor` usuwamy: suszę liczymy z danych.)

Obiekt **Provenance** (wszędzie ten sam kształt):
```json
{"source": "Copernicus Sentinel-2 L2A (Copernicus Data Space Ecosystem)",
 "source_url": "https://dataspace.copernicus.eu/",
 "kind": "pomiar | prognoza | model | reanaliza | szacunek | demo | brak",
 "observed_at": "2026-11-12T10:03:00Z" | null,
 "fetched_at": "2026-11-15T11:20:00Z",
 "cloud_cover_pct": 12.5 | null,
 "resolution": "10 m" | null,
 "note": "krótki opis po polsku, np. 'Najnowsze bezchmurne zobrazowanie z ostatnich 30 dni'"}
```

Odpowiedź:
```json
{"generated_at": "...",
 "weather": {"current": {"temp_c","humidity_pct","wind_ms","precip_mm"|null, "station": "Poznań (IMGW-PIB)"|null},
             "daily": [{"date","rain_mm","t_max_c","t_min_c","et0_mm"}],
             "precip_7d_mm", "precip_14d_mm", "climatic_water_balance_14d_mm" | null,
             "soil_temp_c" | null,
             "warnings": [{"level","event","valid_from","valid_to","source"}],
             "provenance": {"current": Provenance, "forecast": Provenance, "warnings": Provenance}},
 "parcels": [{
    "parcel_id", "id_tag", "crop", "soil", "area_ha", "commune", "county", "voivodeship",
    "centroid": [lat, lon], "geojson_coords": [[lon,lat],...],
    "ndvi": {"mean","min","max","std","healthy_pct","stress_pct","bare_soil_pct"} | null,
    "soil_moisture": {"surface_pct" (0–7 cm, % obj.), "root_zone_pct" (7–28 cm) , "trend_7d": "rośnie|stabilna|spada"} | null,
    "water_reserve_days": int | null,
    "health_score": float | null, "status_label": str, "risk": "Niskie|Średnie|Podwyższone|Wysokie|Brak danych",
    "advice_bullets": [str],
    "layers": {"ndvi": {"image": "data:image/png;base64,...", "bounds": [[s,w],[n,e]]} | null,
               "true_color": {...} | null},
    "history": {"ndvi_monthly": [{"month":"2025-06","mean":0.71}], "provenance": Provenance} | null,
    "provenance": {"parcel": Provenance, "ndvi": Provenance, "soil_moisture": Provenance},
    "warnings": [str]
 }],
 "total_area_ha", "map_center", "map_bounds",
 "sources": [{"name","url","kind","licence"}],
 "warnings": [str]}
```
- `status_color` usuwamy z API (kolor to sprawa UI; status zawsze także tekstem).
- Stare klucze (`*_now`, `*_ortho`, `overlay_data_uri`, `ndvi_data_uri_*`, `moisture_data_uri*`,
  `acquisition_date`, `satellite_provider`) **znikają**. C pisze UI tylko pod nowy kontrakt.
- Gdy ULDK nie zwróci działki: działka NIE jest zmyślana; wpis ma `geojson_coords: []`, `area_ha: null`,
  `provenance.parcel.kind = "brak"` i ostrzeżenie.

HydroMelior (B):
- `GET /api/hydromelior/network?lat&lon` → `{"demo": bool, "note": str, "features": [...], "provenance": Provenance}`;
  dane Śnieciska tylko w promieniu ~5 km od nich i zawsze z `demo: true`.
- `GET /api/hydromelior/alerts?lat&lon` → `{"alerts": [{"level","title","text"}], "provenance": {...}}` (z realnej pogody + IMGW hydro jeśli dostępne).
- `GET /api/hydromelior/coordination?lat&lon` → `{"demo": true, ...}` z zanonimizowanymi gospodarstwami (bez imion i nazwisk).
- `GET /api/hydromelior/ecoscheme?lat&lon&parcel_id` → `{"informational": true, "disclaimer": "...",
  "criteria": [{"name","status": "spełnione|niespełnione|brak danych","evidence","provenance"}], "summary": str}`;
  `GET /api/hydromelior/ecoscheme-download` → HTML z nagłówkiem "DOKUMENT INFORMACYJNY – nie stanowi dowodu ani wniosku do ARiMR".
  Nigdy na sztywno "KRYTERIA SPEŁNIONE".
- `GET /api/sources/status` → stan źródeł (`[{"name","ok": bool,"configured": bool,"last_success","message"}]`) do panelu "Źródła danych" w UI.

## Kontrakt 3: kalkulatory (C, backend + frontend)

- Logika w `src/calculators/` (czyste funkcje, bez FastAPI), routery w `src/web/routes_calc.py`.
- Jedno źródło prawdy: frontend woła API (`POST /api/calculators/sowing|fertilizer`) zamiast duplikować wzory w JS.
- Każda odpowiedź kalkulatora zawiera `assumptions` (lista założeń/źródeł, np. normy IUNG-PIB / COBORU) i `warnings` (np. wynik poza typowym zakresem).

## Zakres (z audytu) i przypisanie

A. Bezpieczeństwo: auth (wymienny, pod login.gov.pl), 127.0.0.1 domyślnie, XSS (serwer: sanityzacja
wyników wyszukiwarki, escapowanie w HTML generowanym serwerowo), CORS (lista dozwolonych źródeł), nagłówki
(CSP, HSTS w prod, X-Frame-Options/frame-ancestors, nosniff, Referrer-Policy, Permissions-Policy),
rate limit, limit body, toggle-gate tylko z enum i per-użytkownik (lub wyłączony), urlencode ULDK
w wyszukiwarce, polityka Nominatim (1 req/s, identyfikowalny UA), cache wyszukiwarki w TTLCache,
`.bat` bez `taskkill` cudzych procesów i bez venv z innego projektu, logi audytowe (logowanie, błędy 4xx/5xx).

B. Dane: prawdziwe NDVI Sentinel-2 (CDSE Sentinel Hub Process/Statistical API, B04/B08, maska chmur SCL,
data i zachmurzenie zobrazowania), wilgotność gleby z modelu/reanalizy (Open-Meteo: ERA5-Land / ICON / ECMWF,
soil_moisture 0–7 / 7–28 cm) zamiast NDVI×stała, IMGW-PIB (synop najbliższej stacji, ostrzeżenia meteo,
hydro), Open-Meteo jako prognoza/fallback, GUGiK ULDK (bez fikcyjnych działek, dokładne trafienie zamiast
promienia 20 m, urlencode), ortofoto GUGiK zamiast Esri, historia NDVI z Sentinel-2 zamiast szablonu,
raport ekoschematu informacyjny/liczony z danych, HydroMelior demo tylko jako demo, cache TTL,
wektoryzacja maski, brak lawiny zapytań przy starcie, temperatura gleby z modelu (nie z powietrza).

C. Kalkulatory + GUI: błąd 10× (siew punktowy, strączkowe), dawki N realistyczne i P₂O₅ poprawnie,
usunięcie upraw ogrodowych, testy z wartościami referencyjnymi; nowy, prosty frontend w stylu gov.pl
(jasny motyw domyślnie + ciemny, mapa jako główny element, "co zrobić dziś na polu"), jeden responsywny
interfejs (desktop + telefon) zamiast dwóch kopii, WCAG 2.1 AA, escapowanie danych (textContent),
bez emoji jako ikon, stany błędów i "brak danych", widoczne źródło i czas każdej liczby, biblioteki lokalnie.

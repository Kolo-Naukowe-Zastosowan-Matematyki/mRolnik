# mRolnik – „Zatrzymaj wodę, chroń plony”

**Zespół:** Koło Naukowe Zastosowań Matematyki · **Wyzwanie:** KOWR, HackNation 2026  
**Charakter:** Prototyp aplikacji webowej (projektowany jako moduł portalu `erolnik.gov.pl`)

---

## 1. Opis rozwiązania

- **Problem:** Polska ma jedne z najniższych zasobów wodnych i poziomu retencji w Europie, a otwarte dane satelitarne, meteorologiczne, glebowe i przestrzenne są rozproszone i wymagają wiedzy GIS. Rolnik nie ma prostego narzędzia zamieniającego te dane w konkretne decyzje dla swojej działki.
- **Główne funkcjonalności:** 5-krokowy kreator w przeglądarce (komputer i telefon) dla **dowolnej działki ewidencyjnej w Polsce** (po numerze TERYT lub z mapy): wybór działek → wybór upraw (7 grup retencyjnych) → ocena sytuacji wodnej (bieżącej i wieloletniej) → spersonalizowane rekomendacje w sekwencji KOWR → zapis/wydruk raportu (PDF, HTML, CSV, JSON). Dodatkowo: historia satelitarna i klimatyczna, moduł HydroMelior (wodowskazy, cieki, zastawki), kalkulatory agronomiczne i filtrowanie działek EGiB/BDOT10k.
- **Sposób wykorzystania danych:** Równoległa fuzja 5 kategorii otwartych źródeł danych z jawną proweniencją (`źródło`, `rodzaj`, `czas obserwacji i pobrania`). Wszystkie wskaźniki i rekomendacje są obliczane dynamicznie; brak odpowiedzi źródła lub zachmurzenie zwraca jawne **„brak danych”** z przyczyną (bez zmyślonych wartości).
- **Korzyści dla użytkownika:** Zero płatnych licencji, specjalistycznego sprzętu i żargonu GIS; konkretne wskazówki retencyjne i agrotechniczne dopasowane do działki, gleby, spadku terenu i uprawy.

---

## 2. Uruchomienie rozwiązania

Wymagania: **Python 3.12+**, dostęp do internetu, ok. 500 MB miejsca na dysku. Przeglądarki: Chrome, Edge, Firefox, Safari, Opera (Windows, macOS, Linux, mobile).

- **Windows:** pobierz repozytorium (ZIP lub `git clone`), rozpakuj i uruchom dwukrotnie **`mrolnik.bat`**.
- **macOS / Linux:** w katalogu projektu uruchom **`bash mrolnik.sh`** (na Ubuntu/Debian wymagane `python3-venv`).
- **Otwarcie aplikacji:** skrypt sam tworzy `.venv`, instaluje pakiety i otwiera `http://127.0.0.1:8000/web` (przy zajętym porcie automatycznie wybiera kolejny wolny). Zatrzymanie: zamknij okno / `Ctrl + C`.
- **Zdjęcia satelitarne (opcjonalnie, darmowe klucze CDSE):** utwórz konto na [dataspace.copernicus.eu](https://dataspace.copernicus.eu) → *Sentinel Hub Dashboard → User settings → OAuth clients*, skopiuj `.env.example` do `.env` i wpisz `CDSE_CLIENT_ID` oraz `CDSE_CLIENT_SECRET`. Bez kluczy aplikacja działa w pełni, a warstwy Sentinel-2 pokazują jawne „brak danych”.
- **Publiczne udostępnienie przez HTTPS (np. do testów jury):** `mrolnik_tunnel.bat` (Cloudflare Tunnel w trybie `prod` z hasłem — szczegóły w [docs/TUNEL_CLOUDFLARE.md](docs/TUNEL_CLOUDFLARE.md)).

> **Najczęstsze problemy:** brak `python` w Windows → zainstaluj Python 3.12+ z opcją *Add python.exe to PATH*; błąd `venv` na Linuxie → `sudo apt install python3-venv`; stary widok po aktualizacji → `Ctrl + F5`.

---

## 3. Główne funkcjonalności i sekwencja analizy

Aplikacja prowadzi rolnika przez 5 kroków i realizuje obowiązkową sekwencję przyczynowo-skutkową KOWR ([docs/logika_rekomendacji.md](docs/logika_rekomendacji.md)):

1. **Ocena sytuacji wodnej** – bieżący bilans klimatyczny (KBW), zapas wody w strefie korzeniowej (dni do stresu wg FAO-56), wilgotność gleby (0–7 i 7–28 cm), wskaźniki satelitarne (NDVI, NDMI, NDWI) oraz wieloletnie tło opadowo-klimatyczne (ERA5-Land / NASA POWER, normy IMGW 1991–2020).
2. **Identyfikacja przyczyn niedoboru / nadmiaru wody** – rozróżnienie deficytu opadów i wysokiego $ET_0$, niskiej pojemności wodnej gleby lekkiej, nadmiernego spływu powierzchniowego (spadek z NMT DEM) oraz wrażliwości wybranej grupy upraw.
3. **Rekomendacje retencyjne (priorytet)** – retencja krajobrazowa (zastawki na rowach, pasy buforowe, zadrzewienia śródpolne, ochrona obniżeń) i glebowa (próchnica, międzyplony, resztki pożniwne, mulcz).
4. **Działania agrotechniczne** – uprawa bezorkowa / pasowa, uprawa w poprzek stoku, dobór i ochrona okrywy roślinnej.
5. **Nawadnianie (warunkowe)** – wskazówki dopiero wtedy, gdy retencja i agrotechnika nie wystarczają.
6. **Raport końcowy** – podsumowanie dla gospodarstwa i szczegóły działek wraz z datą, źródłami danych i ograniczeniami; eksport do **PDF (druk)**, **HTML (offline)**, **CSV** i **JSON**.

---

## 4. Architektura

System składa się z 3 warstw (szczegóły i diagramy: [docs/ARCHITEKTURA_I_DZIALANIE.md](docs/ARCHITEKTURA_I_DZIALANIE.md)):

- **Frontend (`static/`):** czysty HTML5 + CSS3 + ES Modules (bez frameworków i bez CDN; Leaflet i Chart.js lokalnie w `static/vendor/`), ścisłe CSP, responsywny układ desktop/mobile, dostępność WCAG 2.1 AA, tryb jasny/ciemny.
- **Backend (`server.py`, `src/`):** Python 3.12+, FastAPI + Uvicorn. Równoległy orkiestrator (`ThreadPoolExecutor` w `src/api_service.py`) z limitami czasu na konektor i obsługą częściowych wyników. Walidacja wejścia Pydantic v2 (`extra="forbid"`).
- **Cache i bezpieczeństwo (`src/core/`, `src/security/`):** lokalna baza SQLite + JSON (`TTLCache`, bez `pickle`); domyślny nasłuch `127.0.0.1`; tryby logowania `dev` / `password` / `oidc` (gotowe pod `login.gov.pl` / SSO eRolnika); sesje HMAC-SHA256, ochrona CSRF, rate limiting, brak gromadzenia zbędnych danych osobowych ([docs/BEZPIECZENSTWO.md](docs/BEZPIECZENSTWO.md)).

| Moduł | Rola |
|---|---|
| `server.py`, `src/web/` | Aplikacja FastAPI, middleware bezpieczeństwa, endpointy API (`/api/*`, `/auth/*`) |
| `src/api_service.py` | Równoległa orkiestracja analizy działek i monitorowanie statusu źródeł |
| `src/data/` | Konektory do źródeł otwartych (ULDK, EGiB, NMT, BDOT10k, CDSE, IMGW, Open-Meteo, NASA POWER, GUS) |
| `src/hydromelior/`, `static/js/recommendationEngine.js` | Bilans wodny (FAO-56), hydrografia, regułowy silnik rekomendacji retencyjnych i melioracyjnych |
| `src/calculators/` | Kalkulatory siewu, nawożenia i opłacalności upraw |
| `scripts/`, `tests/`, `docs/` | Skrypty danych referencyjnych, testy automatyczne (`pytest`, Playwright), dokumentacja |

---

## 5. Źródła danych, zakresy i tryb pozyskiwania

Wszystkie oceny sytuacji wodnej i rekomendacje są **obliczane dynamicznie przez aplikację** z pobranych danych źródłowych. Stan źródeł na żywo: zakładka **Źródła** lub `GET /api/sources/status?check=true`. Pełna specyfikacja: [docs/ZRODLA_DANYCH.md](docs/ZRODLA_DANYCH.md).

### 5.1 Dane pobierane na bieżąco (live + cache TTL)

| Kategoria | Źródło i zakres | Cache TTL | Licencja / warunki |
|---|---|---|---|
| **Przestrzenne** | **GUGiK ULDK** (granice i pow. działek), **EGiB WFS** (działki w gminie/powiecie), **BDOT10k** (cieki, drogi, lasy, zabudowa), **GUGiK NMT WCS 1 m** (spadek terenu DEM), **Geoportal Ortofotomapa**, **PRNG / Photon / Nominatim** (wyszukiwarka miejscowości) | 7–365 dni | Otwarte dane PZGiK (bezpłatne); OSM ODbL (wyszukiwarka, limit 1 req/s dla Nominatim) |
| **Satelitarne** | **Copernicus Sentinel-2 L2A** (CDSE Sentinel Hub: NDVI, NDMI, NDWI, true color, maska chmur SCL, historia 24–36 mies., średnia 2019–2025) | 6 h – 30 dni | Copernicus (rozp. UE 1159/2013), bezpłatne i otwarte (wymagany bezpłatny klucz OAuth) |
| **Meteorologiczne** | **IMGW-PIB** (pomiary synop, ostrzeżenia meteo), **Open-Meteo** (prognoza 14 dni ECMWF/ICON/GFS, opad 7 dni, archiwum wieloletnie **ERA5 / ERA5-Land** od 1991), zapasowo **NASA POWER** (MERRA-2 / GEOS-IT) | 10 min – 365 dni (porcje roczne) | IMGW-PIB (dane publiczne z atrybucją); Open-Meteo CC BY 4.0 (darmowe niekomercyjne\*); NASA Earthdata (domena publiczna) |
| **Glebowe** | **Open-Meteo** (DWD ICON / ECMWF / ERA5-Land: wilgotność 0–7 i 7–28 cm, temp. gleby) + parametry retencyjne **FAO-56** (θFC, θWP) i klasy podatności **IUNG-PIB** (kat. I–IV; link do SMSR) | 1 h | CC BY 4.0 / Copernicus C3S; FAO-56 (literatura naukowa) |
| **Hydrologiczne** | **IMGW-PIB Hydro** (`api/data/hydro`, `hydro-back`: wodowskazy, stany charakterystyczne), **BDOT10k / PGW Wody Polskie** (cieki, rowy, kanały), **ISOK WFS** (mapy zagrożenia powodziowego MZP/MRP) | 30 min – 90 dni | Dane publiczne IMGW-PIB, GUGiK i PGW Wody Polskie (z atrybucją) |
| **Statystyczne** | **GUS BDL** (plony z 1 ha i ceny skupu w województwach) | 30 dni | Dane publiczne GUS (z atrybucją) |

### 5.2 Dane pobrane lub przetworzone wcześniej (pliki referencyjne w repozytorium)

| Plik | Zawartość i źródło | Data pobrania / wygenerowania | Skrypt odświeżający |
|---|---|---|---|
| `src/data/imgw_normals.json` | Normy klimatyczne 1991–2020 (56 stacji synop IMGW-PIB) | `2026-11-15` | `scripts/build_imgw_normals.py` |
| `src/data/nitrates_gminy.json` | Wykazy gmin z Programu azotanowego (Dz.U. 2023 poz. 244, ISAP) | `2026-11-15` | `scripts/build_nitrates_gminy.py` |
| `src/data/teryt_units.json` | Jednostki administracyjne Polski (GUGiK PRG: 16 woj., 380 pow., 2479 gmin) | `2026-11-19` | `scripts/build_teryt_units.py` |
| `src/data/presets_dzialki.json` | Współrzędne działek przykładowych gospodarstw (GUGiK ULDK) | `2026-11-16` | `scripts/build_presets.py` |
| `src/data/odr_costs.json` | Kalkulacje rolnicze PODR Lubań 2025 (ceny IV–V 2025) | `2025-06-02` (wyd. PODR) | aktualizacja ręczna |
| `src/data/crops_retention.json` | Klasyfikacja 7 grup retencyjnych upraw ($K_c$, głębokość korzeni — FAO-56, IUNG-PIB) | opracowanie literaturowe | – |

---

## 6. Wykaz komponentów, modeli, narzędzi AI i licencji

- **Backend (Python 3.12+):** `fastapi` (MIT), `uvicorn` (BSD-3-Clause), `pydantic` v2 (MIT), `requests` (Apache-2.0), `numpy` (BSD-3-Clause), `shapely` (BSD-3-Clause), `pyproj` (MIT), `pillow` (MIT-CMU); testy: `pytest` (MIT), `httpx` (BSD-3-Clause).
- **Frontend (lokalnie w `static/vendor/`):** `Leaflet 1.9.4` (BSD-2-Clause), `Chart.js 4.4.7` (MIT).
- **Modele i metody naukowe:** FAO Irrigation & Drainage Paper No. 56 (ewapotranspiracja $ET_c = K_c \cdot ET_0$, TAW/RAW), klasyfikacja podatności gleb IUNG-PIB (kat. I–IV), katalog działań NWRM (KE 2014/2015), deterministyczny silnik regułowy IF→THEN ([docs/PODSTAWY_NAUKOWE.md](docs/PODSTAWY_NAUKOWE.md)); zewnętrzne modele numeryczne/reanalizy: ECMWF IFS, DWD ICON, NOAA GFS, ERA5-Land, NASA MERRA-2.
- **Zastosowanie AI:** W działaniu aplikacji (runtime) **nie są używane modele generatywne ani płatne API AI** — wszystkie obliczenia i rekomendacje są w pełni deterministyczne i odtwarzalne na podstawie danych. Narzędzia AI (asystenci kodowania LLM: Claude, Gemini) były używane wyłącznie wspomagająco podczas pisania kodu i dokumentacji pod nadzorem zespołu, zgodnie z regulaminem konkursu.
- **\*Usługi z limitem niekomercyjnym i alternatywy:** darmowe API Open-Meteo obowiązuje dla zastosowań niekomercyjnych (limit 10 000 zapytań/dobę na IP). Przy przekroczeniu limitu archiwum aplikacja automatycznie przełącza się na **NASA POWER** (bez klucza). Wdrożenie produkcyjne wymaga komercyjnego planu Open-Meteo (`OPEN_METEO_API_KEY`, od ok. 29 EUR/mies.), własnej instancji open-source Open-Meteo (AGPLv3) albo bezpośredniego pobierania danych ECMWF Open Data / IMGW-PIB.

---

## 7. Znane ograniczenia techniczne

1. **Rozdzielczość modeli meteo/glebowych:** wilgotność gleby (ICON/ERA5-Land, siatka 2–11 km; NASA POWER ~50 km) i opad 7-dniowy to dane modelowe, a nie pomiar czujnikiem na polu; brak publicznego API SMSR IUNG-PIB (zastosowano parametry FAO-56/IUNG i odnośnik).
2. **Zachmurzenie i klucze satelitarne:** wskaźniki Sentinel-2 wymagają ≥ 70% bezchmurnych pikseli działki (maska SCL) oraz bezpłatnych kluczy OAuth CDSE.
3. **Zróżnicowana dostępność rejestrów lokalnych:** niektóre powiatowe WFS EGiB bywają niedostępne; brak otwartego ogólnopolskiego rejestru zastawek melioracyjnych (przykładowa sieć w Śnieciskach oznaczona flagą `demo: true`).
4. **Czynniki lokalne poza zdalną detekcją:** stan techniczny drenażu, zaskorupienie gleby czy punktowe opady burzowe wymagają weryfikacji polowej (komunikowane w stopce raportu).
5. **Zakres daty analizy:** analiza wieloletnia klimatu i wilgotności (ERA5-Land / NASA POWER) oraz historia satelitarna obejmują minione lata; główna analiza bieżąca (`/api/analyze`) odnosi się domyślnie do stanu aktualnego wraz z oknem historycznym.

---

## 8. Weryfikacja wyników i rekomendacji

- **Testy automatyczne (327 testów `pytest` + test smoke UI Playwright):** pokrywają konektory danych (na zamockowanych odpowiedziach), obliczenia FAO-56, logikę zastawek i kalkulatory (porównane z normami IUNG/COBORU/ODR), wyszukiwarkę działek, cache oraz bezpieczeństwo (auth, CSRF, CSP, rate limiting).
  ```bash
  # Windows (na macOS/Linux użyj .venv/bin/...)
  .venv\Scripts\pip install -r requirements-dev.txt
  .venv\Scripts\python -m pytest -q tests
  node tests/test_ui_smoke.mjs   # test UI w Playwright (opis: docs/GUI.md)
  ```
- **Weryfikacja merytoryczna:** testy na rzeczywistych działkach ewidencyjnych z różnych województw (m.in. wielkopolskie, kujawsko-pomorskie, lubelskie, dolnośląskie) – krzyżowe sprawdzenie granic (ULDK/ortofoto), spadków (NMT), wilgotności i zaleceń ze spójnością metodyki FAO-56, IUNG-PIB i NWRM.

---

## 9. Plan skalowalności w skali kraju

Aby obsłużyć wielu rolników w całej Polsce (np. jako moduł `erolnik.gov.pl`):

- **Limity i odporność źródeł:** przeniesienie lokalnego `TTLCache` (SQLite) na **Redis + PostgreSQL/PostGIS**; grupowanie zapytań meteo/glebowych w komórkach siatki ~5 km (cała Polska to ok. 12 tys. komórek, więc wielu sąsiednich rolników korzysta z jednego wpisu cache); kolejkowanie i ogranicznik równoległości zapytań CDSE; automatyczny fallback ERA5 → NASA POWER; rate limiting per sesja/IP.
- **Aktualizacja danych i czas odpowiedzi:** harmonogramowany ingest w tle (IMGW synop/hydro co 1 h, prognozy co 3–6 h, statystyki Sentinel-2 na żądanie z cache 6–24 h, NMT/BDOT10k/EGiB z TTL 30–365 dni). Dzięki temu analiza pojedynczej działki utrzymuje czas **≤ 30 s (cold)** i **< 2 s (warm cache)** nawet przy dużym ruchu.
- **Szacunkowe koszty infrastruktury (brak opłat licencyjnych za stos open-source):**
  - *Pilotaż (do ~10 tys. rolników/mies.):* 1–2 węzły aplikacji + zarządzany PostgreSQL/PostGIS + Redis + storage obiektowy + plan Open-Meteo ≈ **600–1 500 zł/mies.**
  - *Skala ogólnopolska (integracja z KOWR / eRolnik, setki tys. użytkowników):* autoskalowany klaster kontenerowy, pre-obliczony cache siatkowy dla Polski, własna instancja danych ECMWF/IMGW i dedykowana pula CDSE ≈ **4 000–10 000 zł/mies.**

---

> **Zastrzeżenie:** Prototyp ma charakter informacyjno-doradczy i nie zastępuje analizy laboratoryjnej gleby, planu nawozowego, pozwolenia wodnoprawnego ani dokumentów składanych do ARiMR.

# AGENTS.md — KOWR HackNation 2026: „Zatrzymaj wodę, chroń plony”

This file distills the binding requirements, scientific foundations, validation protocol, judging criteria, and constraints from the official competition PDFs in `task_description/` for all agents working on this repository.

---

## 1. Mission & Target User

- **Challenge:** *Zatrzymaj wodę, chroń plony – Inteligentne wsparcie gospodarowania wodą w rolnictwie* (KOWR, HackNation 2026).
- **Goal:** Build a working web-app prototype (Windows: Chrome, Firefox, Edge, Safari, Opera; mobile support is a bonus) that turns open spatial, satellite, meteorological, soil, and hydrological data into concrete, parcel-specific water-management decisions.
- **Target user:** A Polish farmer managing one or more cadastral parcels, with **no GIS, remote sensing, hydrology, or data-analysis background**.
  - UI, report, and explanations MUST be in **plain, actionable Polish** (avoid raw GIS/remote-sensing jargon).
  - Must require **no paid license, no specialized hardware, and no extra software** from the farmer.

---

## 2. Non-Negotiable Jury Testing & Runtime Rules

1. **Any Polish cadastral parcel (`działka ewidencyjna`):** Must work for arbitrary parcels across all of Poland via parcel ID (TERYT) or map selection — never restricted to preloaded demo farms.
2. **Current AND historical analysis dates (last 10 years):**
   - Stage 1 (all submissions) and Stage 2 (live pitch, 1–3 parcels) test arbitrary **parcel ID + analysis date (`data analizy`)**, which may be **today OR any historical date within the last 10 years**.
   - Jury compares prototype outputs against private reference data.
   - **Never hardcode analysis exclusively to `utcnow()`.**
3. **Latency target:** Prefer **≤ 30 seconds** end-to-end to analyze and generate recommendations for a single parcel.
4. **Dynamic computation only:** Results and recommendations MUST be calculated by the application from fetched source data (current + 10-year archive). Pre-baked / static recommendations fail the requirements.
5. **Honest data handling (no false precision):** If a data source is unavailable or incomplete, communicate missing data explicitly (`brak danych`) with the reason — never fabricate measurements or fake precision.

---

## 3. Mandatory Analytical Sequence (Order Matters!)

Per *Pełna treść zadania* (Sec. 3.2) and *Podstawy naukowe* (p. 3), recommendations MUST follow this causal sequence and be justified by data from earlier steps:

```text
1. Ocena sytuacji wodnej (bieżąca/na datę + 10-letnia podatność historyczna)
        ↓
2. Identyfikacja przyczyn ograniczonej dostępności wody
        ↓
3. Rekomendacje retencyjne (krajobrazowe i glebowe) — PRIORYTET ("retain water where it falls")
        ↓
4. Działania agrotechniczne
        ↓
5. Rekomendacje nawadniania (opcjonalne — dopiero po retencji)
```

### Stage-by-stage requirements
1. **Water Status Assessment (`Ocena stanu wilgotności / sytuacji wodnej`):**
   - Distinguish **as-of-date water deficit / drought** from **10-year historical drought vulnerability** (e.g., frequency of deficit years, critical deficit months such as May–July).
   - Integrate meteorological water balance (precipitation, temperature, dry-spell duration, ET₀ / KBW), soil water-holding capacity & drought susceptibility, satellite vegetation/moisture indices, and 10-year archive data.
   - **Crop-aware satellite interpretation:** Satellite indices (e.g., NDVI, NDMI, NDWI) MUST NOT be interpreted in isolation; interpret them accounting for **selected crop type, phenology/season, and meteorological/soil context**.
2. **Cause Identification (`Identyfikacja przyczyn`):**
   - Explain *why* water availability is limited on this specific parcel:
     - Prolonged precipitation deficit + high temperature / evaporative demand (ET₀).
     - Low soil water retention (sandy/light texture, low organic matter / humus).
     - Excessive surface runoff (terrain slope from DEM/NMT, lack of landscape retention elements such as wetlands, ponds, buffer strips, or midfield hedgerows).
     - High water demand / stress sensitivity of the selected crop at the analysis date.
3. **Retention Recommendations (`Retencja krajobrazowa i glebowa` — primary priority):**
   - **Landscape retention** (using DEM/NMT terrain + hydrological/spatial data): micro-retention ponds, protecting/restoring wetlands & ponds, buffer strips, midfield hedgerows (*zadrzewienia śródpolne*), slowing/retaining runoff in drainage ditches (*rowy melioracyjne*) and small streams.
   - **Soil retention:** catch crops (*międzyplony*), leaving crop residues (*resztki pożniwne*), reduced/conservation tillage, increasing soil organic matter (*próchnica*).
4. **Agrotechnical Actions (`Działania agrotechniczne`):**
   - Concrete field practices tailored to parcel soil, slope, and crop (e.g., contour tillage on slopes, mulch/residue management, crop choice / drought-resilient adjustments).
5. **Irrigation Guidance (`Zabiegi nawadniania` — optional, secondary to retention):**
   - Simple guidance on **whether irrigation is justified, timing, and scope/dose**, grounded in earlier stages.

---

## 4. Mandatory User Flow & Report Requirements (Sec. 3.3)

1. Farmer opens web app in browser.
2. Enters cadastral parcel ID or picks parcel location on map.
3. Selects current crop (and analysis date, defaulting to today, supporting up to 10 years back).
4. App fetches source data and computes current + 10-year historical water assessment and recommendations.
5. App produces a **structured report** containing:
   - Water deficit status (as-of date + 10-year vulnerability) and its **causes**.
   - **Data-justified recommendations** (retention, agrotechnical, optional irrigation).
   - **Analysis date** and **data sources** (with observation/fetch timestamps).
   - **Report limitations:** data freshness & spatial/temporal accuracy, **result uncertainty level (`poziom niepewności wyniku`)**, and **factors not included in the analysis**.
   - Explicit notice of any missing data without false precision.
6. **Save / Export / Print:** Report can be saved in the system, exported to a file (e.g., **PDF**), or printed.

---

## 5. Required Data Categories & Licensing

The prototype must integrate **all 5 categories** using **free, legal, open-licensed** sources (no KOWR-provided dataset):

1. **Meteorological:** IMGW-PIB (`danepubliczne.imgw.pl`), Open-Meteo / ERA5 (`open-meteo.com`, note non-commercial free tier & CC BY 4.0).
2. **Satellite:** Copernicus Data Space Ecosystem (Sentinel-2 L2A) and/or keyless public Sentinel-2 STAC (`earth-search.aws.element84.com/v1`).
3. **Soil / Agro-soil:** IUNG-PIB Drought Monitoring / Soil Drought Susceptibility (`susza.iung.pulawy.pl`), ISRIC SoilGrids (`docs.isric.org/globaldata/soilgrids/`).
4. **Hydrological:** PGW Wody Polskie / PIG-PIB / ISOK Hydroportal (`isok.gov.pl`), IMGW hydro gauges, BDOT10k water network.
5. **Spatial (parcels, land use, DEM):** Geoportal NMT/NMPT WCS, GUGiK ULDK (`uldk.gugik.gov.pl`), EGiB, BDOT10k.
- *(Optional drought background):* European Drought Observatory (EDO WMS).

**Scientific references cited by KOWR (*Podstawy naukowe*):**
- FAO Irrigation and Drainage Paper No. 56 (Allen et al., crop evapotranspiration $ET_c = K_c \cdot ET_0$, TAW/RAW soil water balance).
- IUNG-PIB System Monitoringu Suszy Rolniczej (Doroszewski et al. 2012; Żyłowska & Kozyra 2025 — KBW & soil drought susceptibility categories I–IV).
- EU Natural Water Retention Measures (NWRM, 2014/2015) & Soil Carbon 4 per mille (Minasny et al. 2017).

---

## 6. Judging Criteria & Weights

| Criterion | Weight | Key Signals Judges Look For |
|---|---:|---|
| **1. Wartość praktyczna dla użytkownika** | **25%** | Concrete, farm-applicable decisions (retention, soil moisture protection, drought mitigation, rational irrigation) tailored to parcel & crop |
| **2. Wykorzystanie danych i wiarygodność rekomendacji** | **25%** | Quality & fusion of all 5 open-data categories, scientific rigor, crop-aware satellite interpretation, data-backed justifications, explicit uncertainty & limitations |
| **3. Innowacyjność i potencjał rozwojowy** | **20%** | Novel approach/analytics/UX, value-add over existing tools, clear integration & scaling path |
| **4. Prostota obsługi** | **15%** | Intuitive, plain Polish, usable by a non-technical farmer in field conditions |
| **5. Działający prototyp i jakość techniczna** | **15%** | Works reliably for arbitrary Polish parcel + date (≤ 30 s preferred), clean architecture, basic security, complete docs & license compliance |

---

## 7. Security, Privacy, IP & Repository Conventions

- **Privacy & security (Sec. 5.6, Regulamin § 2.6, § 5):**
  - Avoid collecting unnecessary personal data (PII).
  - Keep repository **private** until at least the end of the event (and until handover if winning).
  - Use open-source libraries / open data / AI tools only if their licenses are documented and compatible with copyright transfer to KOWR (§ 5 of Regulamin).
- **Existing `mRolnik` codebase guardrails (preserve these when editing):**
  - **Strict CSP:** zero inline `<script>`, zero `onclick=`/`style=` attributes in HTML, zero external CDNs (vendor JS/CSS locally in `static/vendor/`).
  - **Caching & config:** use `src/core/cache.py` (`TTLCache`, SQLite + JSON; never `pickle`) and `src/core/config.py` (`settings`); never commit `.env` or secrets.
  - **Validation & provenance:** validate API inputs with Pydantic v2 (`extra="forbid"`), escape server-generated HTML (`html.escape`), and attach `provenance` objects to metrics.
  - **Testing:** run `.venv\Scripts\python -m pytest -q tests` after changes.

---

## 8. Repository Map (Update paths if refactored)

- `server.py` & `src/web/` — FastAPI app, routes (`routes_analysis.py`, `routes_water.py`, `routes_search.py`, etc.), and Pydantic v2 request/response schemas
- `src/api_service.py` — Batch parcel analysis orchestration & shared I/O thread pools
- `src/data/` — Open-data connectors: ULDK/EGiB (`cadastre.py`, `egib.py`), IMGW/Open-Meteo (`weather.py`, `climate.py`), Sentinel-2 (`sentinel.py`, `history.py`), soil (`soil.py`), NMT DEM (`terrain.py`), BDOT10k/ISOK (`bdot10k.py`, `water_features.py`, `flood.py`)
- `src/hydromelior/` — Water balance ($ET_0$, $ET_c = K_c \cdot ET_0$, KBW), hydrography, cause diagnosis, and sequential recommendation engine
- `src/report/` *(planned)* — Structured report assembly, uncertainty scoring (`poziom niepewności wyniku`), and printable/PDF export
- `src/core/` & `src/security/` — `TTLCache` (SQLite/JSON), `settings`, CSP/CSRF/auth/rate-limiting
- `static/` — Frontend UI (`index.html`, `js/views/`, `css/`, `vendor/`; strict CSP: zero inline handlers/styles)
- `tests/` — Pytest suite (mocked unit/API tests) + Playwright UI smoke test

---

## 9. Engineering & Anti-Leakage Guardrails

- **No future-data leakage:** Every time-dependent fetcher and analytical function MUST accept `analysis_date: datetime.date` and strictly exclude any observation after `analysis_date`. Never call `date.today()`, `datetime.now()`, or `utcnow()` to compute analytical windows inside `src/data/`, `src/hydromelior/`, or `src/report/` (current time may only be used for `fetched_at` provenance metadata and choosing live vs. archive APIs).
- **Parallelism & timeouts:** Fetch all 5 data categories concurrently with per-connector timeouts (≤ 8–10 s) so total single-parcel latency stays under **30 s**. On connector timeout/error, return a structured missing-data marker (`brak danych` + reason) and downgrade `poziom niepewności wyniku` rather than failing the whole report.
- **Auto-sync licenses:** Whenever you add a dependency, model, dataset, or external API, update the inventory & license notes in `README.md` (and `docs/ZRODLA_DANYCH.md`) in the same task.

---

## 10. Formal Deliverables Checklist (Sec. 4.1, 4.2, 5.3, 5.5)

Before final submission, ensure all mandatory deliverables are complete:
- [x] Team name (avoid personal names per Regulamin § 6.2), solution title, and concise description (problem, features, data usage, user benefits).
- [ ] Private repository link with jury access + working prototype (live URL or run instructions in `README.md`).
- [x] **README / technical docs** covering all 9 mandatory items:
  1. Run instructions.
  2. Core functionalities.
  3. Architecture summary.
  4. Data sources & ranges.
  5. Explicit split of **live-fetched vs. pre-fetched/preprocessed data** (with fetch dates).
  6. Full inventory of **libraries, components, models, external services, APIs, and AI tools**.
  7. **Licenses / terms of use** for every dataset, library, service, and AI tool (plus paid-tier notes & alternatives if any, e.g., Open-Meteo commercial tier).
  8. Known technical limitations.
  9. Verification methodology for results and recommendations.
  10. **Nationwide scalability plan** (Sec. 5.5): API rate limits/caching, data update cadence & high-load latency, estimated infrastructure costs.
- [ ] Sample **generated report(s)** (e.g., PDF).
- [ ] Presentation in **PDF** (≤ 10 slides).
- [ ] Demo **video** (≤ 3 minutes).

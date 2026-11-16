# mRolnik – interfejs użytkownika (strumień C)

Jeden responsywny interfejs (`static/index.html` + `static/css/app.css` + `static/js/*.js`) zastępuje
`web.html` i `mobile.html` (do usunięcia przez integratora). Bez kroku budowania, bez frameworka:
moduły ES, vanilla JS, biblioteki lokalnie w `static/vendor/` (Leaflet 1.9.4, Chart.js 4.4.7 – z `npm pack`).

## Struktura

| Plik | Rola |
|---|---|
| `static/index.html` | Szkielet: nagłówek, pasek narzędzi, mapa, panel z zakładkami, stopka. Zero inline JS/CSS. |
| `static/css/app.css` | Wszystkie style w zakresie `.mr-app`; tokeny kolorów (jasny/ciemny). |
| `static/js/main.js` | Start na `#mrolnik-root`, motyw, auth (`/auth/me`), analiza, zdarzenia. |
| `static/js/api.js` | Jedyny helper `api()` (Kontrakt 1): cookies same-origin, `X-CSRF-Token`, 401 → logowanie, 429, timeout. |
| `static/js/dom.js` | `h(tag, attrs, children)` – tylko `textContent`/atrybuty; ikony SVG; wzorzec disclosure. |
| `static/js/format.js` | Liczby/daty pl-PL, etykiety proweniencji, statusy, stany ładowania/błędu. |
| `static/js/map.js` | Leaflet: ortofoto GUGiK (WMS), OSM, działki, NDVI, cieki (BDOT10k), sieć melioracyjna. |
| `static/js/farm.js` | Pasek narzędzi: wyszukiwarka, „Dodaj własne pole”, CSV, „Przykłady (demo)”. |
| `static/js/csv.js` | Bezpieczny import/eksport CSV. |
| `static/js/charts.js` | Leniwe ładowanie Chart.js, wykres + tabela danych. |
| `static/js/views/*.js` | Zakładki: Pole, Pogoda, Woda, Kalkulatory, Źródła. |
| `static/deklaracja-dostepnosci.html` | Szablon deklaracji dostępności (do uzupełnienia przez podmiot). |
| `tests/ui_mock_server.py` | Serwer makietowy (dev): statyka + CSP z kontraktu + dane wg Kontraktu 2. |
| `tests/test_ui_smoke.mjs` | Test Playwright: 1440×900, 390×844, 320×640, motyw ciemny, stan błędu, axe-core. |

## Decyzje projektowe

- **Język wizualny gov.pl**: jasne tło (#fff / #f2f2f2), niebieski #0052a5 jako kolor główny, czerwony #d4213d
  oszczędnie (oznaczenia PROTOTYP/DEMO), tekst #1b1b1b, systemowy krój pisma (bez Google Fonts), bazowo 16 px,
  cele dotykowe ≥ 44 px. Ciemny motyw: `prefers-color-scheme` + przełącznik (zapamiętany w localStorage).
- **Mapa jako główny element**: na komputerze zajmuje całą prawą część ekranu; na telefonie jest zaraz pod paskiem
  wyszukiwania, a panel z zakładkami wysuwa się pod nią jak arkusz (bottom sheet).
- **„Dziś na polu” przed wskaźnikami**: 3–5 zaleceń prostym językiem (ostrzeżenia IMGW, przymrozek, opad, wiatr
  przy opryskach, `advice_bullets` z analizy), każde z etykietą źródła.
- **Proweniencja przy każdej liczbie**: etykieta „Sentinel-2 · 12.11.2026 · zachm. 8%” – po kliknięciu pełne źródło,
  rodzaj (pomiar/prognoza/model/szacunek/DEMO/brak), czas pomiaru i pobrania, uwagi. `kind: "brak"` → „Brak danych”
  + przyczyna; `demo` → czerwona plakietka DEMO. Prognozy/modele mają przerywaną ramkę.
- **Bez żargonu**: „Kondycja roślin (NDVI z satelity Sentinel-2)”, „Wilgotność gleby (model)”, przyciski „i” z wyjaśnieniem.
- **Kalkulatory bez wzorów w JS**: formularze pobierają słowniki z `GET /api/calculators/meta` i liczą przez
  `POST /api/calculators/sowing|fertilizer`; błędy 422 trafiają pod konkretne pola (`aria-describedby`).

### Uwagi właściciela produktu (docs/UWAGI_UZYTKOWNIKA.md) – realizacja

1. Jeden rząd: wyszukiwarka 50% (placeholder zawija się do 2 linii), „Dodaj własne pole” 25%, „CSV” 25% – także na
   telefonie (sprawdzane w teście: 48/24/24% z odstępami).
2. Brak wiersza z województwem/gospodarstwem – przykłady tylko w zwiniętej sekcji „DEMO Przykłady gospodarstw”.
3. Sekcje domyślnie zwinięte: dodawanie pola, CSV, przykłady, szczegóły działek, kalkulatory, korekty, założenia, tabele.
4. Nad mapą nie ma listy wskaźników ani zakresu dat – jeden przełącznik warstw Leaflet na mapie.
5. Aktywna zakładka zawsze podświetlona (`aria-selected="true"`, gruba niebieska linia, pogrubienie, tło) – od startu.
6. Brak zakładki „Web”; `/mobile` przekierowuje na `/web` (server.py).
7. Te same komponenty, kolejność i nazwy na komputerze i telefonie – zmienia się tylko układ (CSS grid).
8. Każda zakładka pokazuje szkielet od razu; sekcje „Woda”, „Źródła”, kalkulatory ładują się osobno, z limitem
   czasu (analiza 60 s, pozostałe 20–30 s) i stanem „Brak danych” + przyczyna + „Spróbuj ponownie”.
9. „Woda”: infografika zastawek (zamknięta/przymknięta/otwarta, zalecenie wyróżnione), wykres bilansu wodnego
   (opad vs ET₀, pomiary + prognoza, bilans narastająco), wskaźnik wilgotności gleby względem punktu więdnięcia
   i pojemności polowej, wodowskaz IMGW (poziom, trend, stany ostrzegawczy/alarmowy), komunikaty, warstwa cieków
   i rowów z BDOT10k (GUGiK) na mapie, ekoschemat informacyjnie.

## Dane, na których opiera się UI (poza Kontraktem 2 – opcjonalne)

- `GET /api/hydromelior/alerts` może zawierać: `water_balance: [{date, precip_mm, et0_mm, kind: "obs"|"forecast"}]`,
  `river_gauge: {station, river, level_cm, trend, observed_at, warning_level_cm, alarm_level_cm, provenance}`,
  `gate_recommendation: {position: "zamknieta"|"przymknieta"|"otwarta", reason, provenance}`.
  Brak `water_balance` → wykres z `weather.daily` (prognoza). Brak `gate_recommendation` → szacunek z
  `climatic_water_balance_14d_mm` (< −15 mm zamknij, > +15 mm otwórz), wyraźnie oznaczony jako „szacunek”.
- `parcels[].soil_moisture.field_capacity_pct` / `wilting_point_pct` – bez nich wskaźnik pokazuje samą wilgotność.
- `GET /api/search-location` – `results[].is_parcel`, `parcel_id`, `area_ha` (wynik z ULDK dodaje się jako działka).
- Paleta legendy NDVI (warstwa `layers.ndvi.image`): #a0522d (gleba), #e0a030 (stres), #9acd32 (umiarkowana), #1a9641 (dobra).

## Bezpieczeństwo

- CSP z Kontraktu 1 spełniona: zero `<script>` inline, zero `on*=` i `style=` w HTML, zero CDN. Style dynamiczne tylko
  przez CSSOM (`el.style.width = …`). Test sprawdza zdarzenia `securitypolicyviolation` – 0 naruszeń.
- Dane z API/OSM/CSV wyłącznie przez `textContent`; popupy i etykiety Leaflet budowane z elementów DOM.
  Test podstawia nazwę `<img src=x onerror=alert(1)>` z wyszukiwarki – nic się nie wykonuje.
- CSV: parser z obsługą cudzysłowów, `;`/`,`, przecinka dziesiętnego, limit 256 KB i 50 działek; wiersze bez
  współrzędnych lub poza Polską (49–55 N, 14–24,2 E) są odrzucane z komunikatem (żadnego losowego położenia).
  Eksport: `=`, `+`, `-`, `@`, tab, CR na początku pola poprzedzone apostrofem; separator `;`, BOM UTF-8.
- localStorage tylko na preferencje UI (motyw, ostatnia zakładka, ostatnie gospodarstwo przykładowe), w try/catch.
  Działki użytkownika są tylko w pamięci karty (RODO) – można je zapisać do CSV.

## WCAG 2.1 AA – lista kontrolna

| Kryterium | Realizacja |
|---|---|
| 1.1.1 Treść nietekstowa | Ikony SVG `aria-hidden` + tekst; mapa ma opis i tekstową listę działek; wykresy mają tabele danych. |
| 1.3.1 Informacje i relacje | Landmarki header/main/footer, nagłówki h1–h3, `<label>` dla wszystkich pól, `fieldset/legend`, tabele z `th scope`. |
| 1.4.1 Użycie koloru | Status = ikona + tekst; działki na mapie różnią się wzorem linii i etykietą; paski mają wzory. |
| 1.4.3 Kontrast | Sprawdzony liczbowo: tekst #1b1b1b/#fff 17,2:1; #4d4d4d/#f2f2f2 7,6:1; #0052a5/#fff 7,6:1; ciemny: #e8eaed/#1d2128 13,4:1, #7cb2ff/#1d2128 7,4:1; statusy ≥ 5,7:1. |
| 1.4.4 / 1.4.10 Powiększanie, zawijanie | Brak `maximum-scale`/`user-scalable=no`; układ od 320 px bez poziomego przewijania (test). |
| 2.1.1 Klawiatura | Wszystko to przyciski/linki/pola; zakładki ze strzałkami, Home/End; Esc zamyka tryb dodawania i podpowiedzi. |
| 2.4.1 Pomijanie bloków | Link „Przejdź do treści”. |
| 2.4.7 Widoczny fokus | Obrys 3 px w kolorze głównym z odstępem 2 px. |
| 3.3.1 / 3.3.3 Błędy | Komunikaty przy polach (`aria-invalid`, `aria-describedby`), podsumowanie z `role="alert"`. |
| 4.1.3 Komunikaty o stanie | Region `aria-live` (analiza, wyniki wyszukiwania, CSV); `aria-busy` w ładujących się sekcjach. |
| 2.3.3 / ruch | `prefers-reduced-motion` wyłącza animacje (także w wykresach). |

**axe-core 4.10.2** (tagi wcag2a, wcag2aa, wcag21a, wcag21aa) na wszystkich zakładkach w 1440×900, 390×844,
320×640 i w motywie ciemnym: **0 naruszeń**. Ograniczenia: brak testów z czytnikiem ekranu i z użytkownikami;
podkłady mapy (kafelki) nie mają opisów – informacje są dostępne w liście działek.

## Osadzanie w stronie eRolnika

```html
<link rel="stylesheet" href="/mrolnik/static/vendor/leaflet/leaflet.css">
<link rel="stylesheet" href="/mrolnik/static/css/app.css">
<script src="/mrolnik/static/vendor/leaflet/leaflet.js" defer></script>
<script type="module" src="/mrolnik/static/js/main.js"></script>

<div id="mrolnik-root" class="mr-app" data-mode="embedded" data-api-base="/mrolnik">
  … (zawartość jak w static/index.html, od paska narzędzi do zakładek) …
</div>
```

- Wszystkie style są pod `.mr-app` (reset elementów przez `:where()` – nie nadpisuje stylów strony-gospodarza).
- `data-mode="embedded"` ukrywa własny nagłówek i stopkę (dostarcza je eRolnik) i ustala wysokość modułu na 720 px.
- `data-api-base` – prefiks adresów API; `data-chart-src` – inna ścieżka do Chart.js;
  `data-default-farm` – domyślne gospodarstwo przykładowe; `data-cadastre-wms="1"` – warstwa działek ewidencyjnych
  GUGiK (wymaga `https://integracja.gugik.gov.pl` w `img-src`); `data-water-wms` / `data-water-wms-layers` – inna
  usługa cieków (np. MPHP Wód Polskich; jej host trzeba dopisać do `img-src`).
- CSP strony-gospodarza musi dopuszczać `script-src`/`style-src` z hosta modułu oraz hosty kafelków mapy;
  `frame-ancestors` w Kontrakcie 1 pozwala też osadzić całość w `<iframe>` na `*.gov.pl`.

## Uruchomienie testów

```bash
python -m pytest -q tests/test_calc_*.py                    # kalkulatory (32 testy)
python tests/ui_mock_server.py 8765                         # podgląd UI na makiecie: http://127.0.0.1:8765/web
AXE_PATH=/ścieżka/axe-core/axe.min.js SCREENS_DIR=screens node tests/test_ui_smoke.mjs
```

Test dymny wymaga Node ≥ 18 z pakietem `playwright` (Chromium) i opcjonalnie `axe-core`
(`npm pack axe-core@4.10.2`, rozpakować, wskazać `AXE_PATH`). Kafelki map są w teście podmieniane lokalnym obrazkiem.
Makieta wywołuje błąd analizy dla `/web?mock_fail=analyze`.

Zrzuty ekranu (ostatni przebieg): `/home/claude/work/screens/` – `desktop-1-pole.png` … `desktop-9-ciemny-woda.png`,
`mobile-1-pole.png` … `mobile-9-blad.png`, `mobile320-*.png`.

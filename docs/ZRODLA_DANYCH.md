# Źródła danych mRolnik

Zasada: każda liczba w API ma proweniencję (`source`, `kind`, `observed_at`, `fetched_at`, `note`).
`kind`: **pomiar** (obserwacja/rejestr), **prognoza**, **model** (model numeryczny lub obliczenie mRolnik),
**reanaliza**, **statystyka** (statystyka publiczna GUS lub średnia wieloletnia z danych państwowych),
**tabelaryczna** (wartość literaturowa, np. FAO-56 – nie pomiar), **przepis** (próg/termin z aktu prawnego),
**zalecenie** (zalecenie ośrodka doradztwa rolniczego ODR – nie przepis), **demo** (dane przykładowe),
**brak** (źródło nie odpowiedziało / nieskonfigurowane → wartość `null`).
Nie ma żadnych wartości zastępczych: brak danych to `null` i komunikat po polsku.

## 1. Zestawienie

| Dana w API | Źródło | Rodzaj | Aktualizacja / opóźnienie | Licencja / atrybucja |
|---|---|---|---|---|
| Granice, nr, powierzchnia działki (`geojson_coords`, `area_ha`) | GUGiK ULDK `uldk.gugik.gov.pl` (EGiB) | pomiar (rejestr) | wg aktualizacji EGiB w powiecie; cache 30 dni | dane otwarte PZGiK, bezpłatne |
| NDVI działki (`ndvi.*`), nakładka NDVI, historia NDVI | Copernicus Sentinel-2 L2A przez CDSE Sentinel Hub (Catalog, Statistical, Process API) | pomiar (satelita) | przelot co 2–5 dni, produkt L2A zwykle w ciągu kilku–kilkunastu godzin; w praktyce decyduje zachmurzenie; cache 6 h (historia 24 h) | Copernicus: bezpłatne, pełne i otwarte; „Zawiera zmodyfikowane dane Copernicus Sentinel [rok]” |
| Bieżąca pogoda (`weather.current`) | IMGW-PIB `danepubliczne.imgw.pl/api/data/synop` – najbliższa stacja synoptyczna | pomiar | co godzinę, opóźnienie ok. 1 h; cache 20 min | „Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy” (+ „Dane IMGW-PIB zostały przetworzone”, gdy przetwarzamy) |
| Bieżąca pogoda – zapas | Open-Meteo `current` | model | 15 min; cache 30 min | CC BY 4.0 |
| Ostrzeżenia (`weather.warnings`) | IMGW-PIB `api/data/warningsmeteo`, filtr po powiecie (TERYT z ULDK) lub województwie | prognoza | na bieżąco; cache 10 min | jak IMGW-PIB |
| Prognoza 14 dni (`weather.daily`, sumy, bilans) | Open-Meteo: ECMWF IFS 0,25°, DWD ICON (ok. 7 dni), NOAA GFS lub automatyczny dobór | prognoza | kilka przebiegów na dobę; cache 30 min | CC BY 4.0, „Weather data by Open-Meteo.com” |
| Opad ostatnich 7 dni (`precip_past_7d_mm`, `water_balance[kind=obs]`) | Open-Meteo `past_days=7` (analiza modelu) | model | – | CC BY 4.0 |
| Wilgotność gleby 0–7 i 7–28 cm, temperatura gleby | Open-Meteo: DWD ICON (warstwy 0–1, 1–3, 3–9, 9–27 cm przeliczone ważeniem grubości), zapasowo ERA5-Land (Copernicus C3S) z archiwum | model / reanaliza | ICON co 3–6 h; ERA5-Land opóźnienie ok. 5 dni; cache 1 h | CC BY 4.0; ERA5-Land: Copernicus Climate Change Service |
| Zapas wody (`water_reserve_days`) | obliczenie mRolnik (FAO-56) z wilgotności gleby, ET0 i opadu z prognozy | model | – | – |
| Wodowskaz (`river_gauge`) | IMGW-PIB `api/data/hydro` (czas pomiaru w UTC), najbliższy posterunek ≤25 km | pomiar | co 1 h (zwykle), cache 30 min | jak IMGW-PIB |
| Hydrografia (cieki, rowy, kanały) – warstwa mapy | WMS kompozycji BDOT10k GUGiK `mapy.geoportal.gov.pl/wss/service/pub/guest/kompozycja_BDOT10k_WMS/MapServer/WMSServer`, warstwy `WPow, rzOk, rz, kanOkr, kan, row, BHydL, Prze, rzEt, kanEt, RowEt`, EPSG:4326 (usługa nie obsługuje 3857); opcjonalnie WFS z `MROLNIK_HYDRO_WFS_URLS` | pomiar (rejestr) | wg aktualizacji BDOT10k w powiecie | dane otwarte GUGiK |
| NDMI działki (`layers.ndmi`) | Sentinel-2 L2A, ta sama scena i maska SCL co NDVI; NDMI = (B8A − B11)/(B8A + B11) | pomiar (satelita) | jak NDVI | jak Copernicus |
| NDWI działki (`layers.ndwi`) | Sentinel-2 L2A, ta sama scena i maska SCL co NDVI; NDWI = (B03 − B08)/(B03 + B08) (McFeeters, 1996) | pomiar (satelita) | jak NDVI | jak Copernicus |
| Zdjęcie działki w barwach naturalnych (`layers.true_color`) | Sentinel-2 L2A, ta sama scena co NDVI (B04/B03/B02 × 2,5); błąd pobrania → tylko ostrzeżenie | pomiar (satelita) | jak NDVI | jak Copernicus |
| Normy klimatyczne 1991–2020 (`reference.climate_normals`) | IMGW-PIB, archiwum danych miesięcznych synop (`danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/dane_meteorologiczne/miesieczne/synop/`), policzone skryptem `scripts/build_imgw_normals.py` → `src/data/imgw_normals.json` (56 stacji, ≥24 lata w miesiącu) | statystyka | jednorazowo (bez pobierania w trakcie pracy) | jak IMGW-PIB (dane przetworzone) |
| Stany charakterystyczne wodowskazu i 7 dni (`river_gauge.*_level_cm`, `series_7d`) | IMGW-PIB `hydro-back.imgw.pl/station/hydro/status/data` (to samo co hydro.imgw.pl) | pomiar | cache 30 min | jak IMGW-PIB |
| Plony z 1 ha (`/api/reference/yields`) | GUS – Bank Danych Lokalnych API `bdl.stat.gov.pl/api/v1` (zmienne „plony z 1 ha, ogółem”, województwa i Polska) | statystyka | dane roczne; cache 30 dni | „Źródło: GUS, Bank Danych Lokalnych” |
| Średnia wieloletnia NDVI (`/api/reference/ndvi`) | Sentinel-2, jedno zapytanie P1M 2019–2025 dla działki | statystyka | cache 30 dni | jak Copernicus |
| Historia: NDVI, NDMI, NDWI, EVI, gleba odsłonięta – 36 mies. i klatki animacji (`/api/history/*`) | Sentinel-2 L2A, kompozyty miesięczne (mediana bezchmurnych obserwacji piksela) | pomiar | wskaźniki cache 24 h; klatki z minionych miesięcy 30 dni | jak Copernicus |
| Opady miesięczne i wilgotność gleby (Krok 2, Historia), klimat pola od 1991 (`/api/history/climate`, `/api/reference/climate`) | ERA5 / ERA5-Land (Copernicus C3S, ECMWF) przez archiwum Open-Meteo (`src/data/era5_archive.py`; opcjonalnie `OPEN_METEO_API_KEY` → `customer-archive-api`) | reanaliza | opóźnienie ok. 5 dni; porcje roczne na siatce 0,1° w SQLite (zakończone lata 365 dni, ostatnie ~4 mies. 30 dni) | CC BY 4.0; Copernicus Climate Change Service; darmowe API niekomercyjne (10 000 zapytań/dobę) |
| Zapasowe archiwum opadów, temperatur i wilgotności strefy korzeniowej (przy HTTP 429 / awarii Open-Meteo Archive) | NASA POWER `power.larc.nasa.gov/api/temporal/daily/point` (reanaliza MERRA-2 / GEOS-IT, NASA GMAO; `PRECTOTCORR`, `GWETROOT`, `T2M*`; ET₀ wg Hargreavesa) | reanaliza | ok. 0,5° × 0,625° (~50 km); cache porcji rocznych w SQLite, wynik zapasowy buforowany 1 h | dane publiczne NASA Langley Research Center POWER Project (otwarta polityka danych NASA Earthdata, bez klucza) |
| Progi z przepisów (`/api/reference/rules`) | Dz.U. 2014 poz. 516 zm. Dz.U. 2026 poz. 353 (wiatr przy opryskach); Dz.U. 2023 poz. 244 – program azotanowy (terminy nawożenia, progi 3°C/5°C, wykazy gmin → `src/data/nitrates_gminy.json`) | przepis | stan prawny 15.11.2026 | teksty aktów prawnych (ISAP) |
| Progi siewu (temperatura gleby) | zalecenia ODR: PODR Stare Pole (kukurydza 8–10°C na głębokości siewu), WODR Poznań (soja 10°C w górnej warstwie gleby) + temperatura gleby 6 cm z Open-Meteo | zalecenie / model | – | dokumenty ODR (linki w API) |
| Pojemność polowa i punkt więdnięcia | FAO-56 (tab. 19), typowe wartości dla klasy uziarnienia | tabelaryczna | – | literatura (nie pomiar, nie IUNG-PIB) |
| Zastawki, rowy w Śnieciskach, koordynacja sąsiadów | dane przykładowe | demo | – | fikcyjne, zanonimizowane |
| Ekoschemat | fakty z powyższych źródeł; warunki formalne = „brak danych” | – | – | dokument wyłącznie informacyjny |
| Susza rolnicza (odnośnik) | IUNG-PIB System Monitoringu Suszy Rolniczej `susza.iung.pulawy.pl` | – | brak API, tylko link | – |

Pomiar ≠ model: wilgotność gleby i zapas wody to wartości modelowe dla komórki siatki 2–11 km, a nie pomiar
na działce. Opad z ostatnich 7 dni pochodzi z analizy modelu, nie z deszczomierza.

## 2. NDVI Sentinel-2 – szczegóły

- NDVI = (B08 − B04) / (B08 + B04), rozdzielczość 10 m, geometria działki z ULDK.
- Maska SCL (Scene Classification): wykluczone 0 (brak danych), 1 (wadliwy), 3 (cień chmur), 8/9 (chmury), 10 (cirrus), 11 (śnieg).
- Wybór zobrazowania: Statistical API z interwałem dobowym za ostatnie 45 dni; bierzemy **najnowszy dzień, w którym
  ≥ 70% pikseli działki jest bez chmur**. W API: `ndvi.acquired_on`, `ndvi.valid_pct`, `provenance.ndvi.observed_at`
  (czas zobrazowania z Catalog API), `provenance.ndvi.cloud_cover_pct` (zachmurzenie całej sceny wg metadanych).
- Brak takiego dnia → `ndvi: null`, `kind: "brak"` (jesienią i zimą częste).
- Historia: interwał miesięczny, 24 miesiące; dla każdego piksela mediana bezchmurnych obserwacji w miesiącu,
  potem średnia po działce. Miesiące z < 50% pikseli bez chmur mają `mean: null`.
- Klasy powierzchni: gleba odsłonięta NDVI < 0,25; stres / rzadka roślinność 0,25–0,50; dobry łan ≥ 0,50.

Paleta nakładki (`layers.ndvi.legend`):

| NDVI | Kolor | Opis |
|---|---|---|
| < 0,00 | `#7f3b08` | woda / brak roślin |
| 0,00–0,25 | `#c48a3a` | gleba odsłonięta |
| 0,25–0,40 | `#f2d24b` | roślinność rzadka / stres |
| 0,40–0,50 | `#c7e36b` | roślinność umiarkowana |
| 0,50–0,65 | `#7cc24a` | łan dobry |
| 0,65–0,80 | `#2f9a3a` | łan gęsty |
| ≥ 0,80 | `#0b5d1e` | łan bardzo gęsty |

Piksele bez danych (chmura, poza działką) są przezroczyste.

### NDMI, NDWI i zdjęcie w barwach naturalnych

Jedno zapytanie Process API zwraca raster 3-kanałowy UINT8 z tej samej sceny i maski SCL: **R = NDVI, G = NDMI, B = NDWI**
(kodowanie 1..255 = wartość z zakresu [−1, 1], 0 = brak danych). NDMI = (B8A − B11)/(B8A + B11) (pasma 20 m przepróbkowane do 10 m).
API: `layers.ndmi = {image, bounds, legend, mean, acquired_on, note}`. NDMI opisuje **wodę w roślinach i w wierzchniej
warstwie powierzchni** – to nie jest wilgotność gleby w % ani zapas wody w strefie korzeni (ten pochodzi z modelu, rozdz. 3).
Na pasku nad mapą przycisk „Woda w roślinach (NDMI)” zastąpił dawny „Wilgotność gleby” (jedna wartość modelu dla całej działki).

| NDMI | Kolor | Opis |
|---|---|---|
| < −0,2 | `#a50026` | silny niedobór wody / gleba odkryta |
| −0,2–0 | `#f46d43` | niedobór wody w roślinach |
| 0–0,2 | `#fee08b` | umiarkowane uwodnienie |
| 0,2–0,4 | `#74add1` | dobre uwodnienie |
| ≥ 0,4 | `#313695` | wysokie uwodnienie |

NDWI = (B03 − B08)/(B03 + B08) (McFeeters, 1996) wykrywa **wodę stojącą na powierzchni** (zastoiska, podtopienia): woda
odbija zieleń i pochłania bliską podczerwień, rośliny i gleba – odwrotnie. Nie mierzy wilgotności gleby ani wody w roślinach.
API: `layers.ndwi = {image, bounds, legend, mean, water_pct, wet_pct, acquired_on, note}`; `water_pct` = % pikseli działki
z NDWI > 0,2, `wet_pct` = % pikseli z NDWI > 0. Przycisk „Woda na polu (NDWI)” na pasku nad mapą. Progi jak w opisie wskaźnika
w repozytorium Sentinel Hub custom-scripts.

| NDWI | Kolor | Opis |
|---|---|---|
| < −0,3 | `#8c6d31` | sucha powierzchnia / gęsty łan |
| −0,3–0 | `#e7d9a8` | brak wody na powierzchni |
| 0–0,2 | `#6baed6` | podmokło – możliwe zastoiska |
| ≥ 0,2 | `#08306b` | woda stojąca |

Proste wyjaśnienia wskaźników dla rolnika (co mierzą, jak, jak czytać) są w jednym miejscu: `static/js/indices.js`
(legenda mapy, karta działki na Głównej, Historia).

`layers.true_color` – drugie zapytanie Process API: B04/B03/B02 × 2,5, kanał alfa = dataMask (przezroczyste poza działką);
pokazywane w widoku „Samo zdjęcie”. Błąd tego zapytania daje tylko ostrzeżenie w `warnings` – NDVI, NDMI i NDWI pozostają.

Ocena kondycji (`health_score`, 0–100) liczona tylko z NDVI: 70 × skala((NDVI − 0,1)/0,7) + 0,3 × udział dobrego łanu.
Bez NDVI: `health_score: null`, `status_label: "Brak danych satelitarnych"`. `risk` = gorsze z ryzyka z NDVI i z zapasu wody
(0 dni – Wysokie, ≤3 – Podwyższone, ≤7 – Średnie).

## 3. Wilgotność gleby i zapas wody

Wartości θFC (pojemność polowa) i θWP (punkt trwałego więdnięcia), m³/m³ – środki zakresów FAO-56, tab. 19;
przypisanie polskich nazw gleb – heurystyka mRolnik (nazwa wpisana przez użytkownika):

| Klasa | Przykładowe nazwy | θFC | θWP |
|---|---|---|---|
| piasek | piasek luźny | 0,12 | 0,045 |
| piasek gliniasty | gleba lekka, piaszczysta | 0,15 | 0,065 |
| glina piaszczysta | piaszczysto-gliniasta | 0,23 | 0,11 |
| glina | gliniasta, brunatna, rędzina, glina morenowa (domyślna) | 0,25 | 0,12 |
| pył / less | less, pyłowa, czarnoziem | 0,29 | 0,15 |
| glina pylasta / ilasta | mada, czarne ziemie, gleba ciężka | 0,34 | 0,21 |
| ił | ił, ilasta | 0,36 | 0,22 |
| organiczna | torfowa, murszowa (orientacyjnie) | 0,55 | 0,25 |

Zapas wody: warstwa 0–28 cm; TAW = (θFC − θWP)·280 mm; AW = (θ − θWP)·280 mm; dzień po dniu
AW ← min(TAW, AW + 0,8·opad − ET0) (Kc = 1); wynik = liczba dni do spadku AW poniżej 50% TAW
(początek stresu wodnego, FAO-56 p = 0,5). Gdy nie spada w horyzoncie prognozy: „co najmniej N dni”.
Bilans klimatyczny 14 dni = Σ opadu − Σ ET0 z prognozy (zastępuje dawne „odchylenie od normy 28 mm”).

## 4. HydroMelior

- `GET /api/hydromelior/network?lat&lon` – cieki/rowy z WFS (GeoJSON WGS84, `properties.demo=false`), lista warstw WMS
  (`wms`), a tylko w promieniu 5 km od Śnieciska dodatkowo rowy i zastawki przykładowe (`demo=true`).
- `GET /api/hydromelior/water?lat&lon&soil` – `water_balance` (7 dni `obs` + 14 dni `forecast`), `river_gauge`,
  `soil_moisture` z `field_capacity_pct`/`wilting_point_pct`, `gate_recommendation` (`open|half|closed` + powody).
- Reguły zalecenia dla zastawki: **otwarta** – intensywny opad (≥ 20 mm/dobę lub ≥ 30 mm w 3 dni), ostrzeżenie IMGW o
  opadach/roztopach (stopień ≥ 2), wodowskaz ≥ stan ostrzegawczy (jeśli znany) lub gleba nasycona; **zamknięta** – w glebie
  < 50% wody dostępnej i bilans 14 dni < −10 mm; **przymknięta** – pozostałe przypadki. Zalecenie orientacyjne; manewrowanie
  zastawkami zgodnie z pozwoleniem wodnoprawnym i ustaleniami spółki wodnej.
- Stany ostrzegawcze/alarmowe wodowskazów: API IMGW ich nie publikuje → `null` (chyba że pole pojawi się w odpowiedzi).
  Trend stanu wody liczony z kolejnych odczytów zapamiętanych przez serwer (potrzebne ≥ 3 h historii).
- Adresy WFS/WMS hydrografii są konfigurowalne (`MROLNIK_HYDRO_*` w `.env`); nazwy warstw wykrywane z GetCapabilities.
  **Domyślnych adresów nie dało się sprawdzić bez sieci** – zweryfikuj je przy pierwszym uruchomieniu w panelu „Źródła danych”
  (`GET /api/sources/status?check=true`) i w razie potrzeby wpisz właściwe w `.env`.
- Hosty, które frontend musi dopuścić w CSP `img-src` dla warstwy WMS: domyślnie `https://mapy.geoportal.gov.pl`
  (już dozwolony). Przy zmianie na usługę PGW Wody Polskie: `https://wody.isok.gov.pl` (dopisz do `MROLNIK_CSP_IMG_HOSTS`).

## 4a. Linie referencyjne i progi na wykresach

Każda linia przerywana na wykresie ma własną proweniencję i nazwę w legendzie oraz kolumnę w tabeli danych:

- **Normy IMGW-PIB 1991–2020** (prognoza 14 dni, bilans wodny, opady miesięczne w Historii) – stacja synoptyczna
  najbliższa działce z kompletem danych (`statystyka`). Stacje bez pełnej serii synop 1991–2020 (m.in. Gdańsk, Zamość,
  Przemyśl, Ostrołęka, Szczecinek) są pomijane – wybierana jest kolejna najbliższa.
- **Stany ostrzegawczy, alarmowy, granice stanów niskich i wysokich** wodowskazu – IMGW-PIB (`pomiar`).
- **Pojemność polowa / punkt więdnięcia** – wartości tabelaryczne FAO-56 (`tabelaryczna`).
- **Średnia NDVI 2019–2025** dla miesiąca kalendarzowego – Sentinel-2 dla tej samej działki (`statystyka`).
- **Średnia Polski** w plonach z 1 ha – GUS BDL (`statystyka`). GUS nie rozdziela plonów pszenicy i jęczmienia na ozime/jare.
- **Przepisy** (`przepis`): wiatr przy opryskach ≤ 4 m/s, ≤ 5 m/s przy technice ograniczającej znoszenie ≥ 75 %
  (rozp. MRiRW, Dz.U. 2014 poz. 516 w brzmieniu Dz.U. 2026 poz. 353; akt **nie** określa progu temperatury);
  program azotanowy (Dz.U. 2023 poz. 244): terminy nawożenia azotem 1 III – 15/20/25 X wg gminy (załączniki 2 i 3),
  nawozy naturalne stałe do 31 X, od 1 II po 5 dniach z Tśr > 3°C (ozime, TUZ) lub > 5°C (pozostałe).
- **Zalecenia doradztwa** (`zalecenie`, wyraźnie oznaczone, nie przepisy): kukurydza 8–10°C na głębokości siewu
  (Pomorski ODR, Stare Pole 2022), soja 10°C w górnej warstwie gleby (WODR Poznań, poradnik na gov.pl). Dla buraka,
  zbóż jarych i ziemniaków oraz progów zbioru nie znaleziono progów w dokumentach ODR – nie są pokazywane.
- **Klimat pola** (Plony): trendy 1991–2025 z ERA5 (`reanaliza`), średnia odniesienia 1991–2020.

## 4b. Filtrowanie działek („Filtruj działki”)

Filtruje działki z „Moich działek” i pola dodane przez użytkownika (granice z GUGiK ULDK) kryteriami z otwartych danych.
Kod: `src/data/parcel_search.py`, API `POST /api/parcel-search` → `GET /api/parcel-search/{job_id}` (postęp i wyniki).
Brak danych dla kryterium koniecznego **nie odrzuca** działki – trafia ona do „do sprawdzenia”.

| Kryterium | Źródło | Uwagi |
|---|---|---|
| Powierzchnia, kształt, gmina/powiat/województwo, scalanie | GUGiK ULDK | powierzchnia geometryczna w EPSG:2180; zwartość 4πA/P², prostokątność A/MRR, szerokość ≈ 2× największy bufor ujemny; scalanie = granice w odstępie ≤ 1 m |
| Wody (SWRS_L, SWKN_L, SWRM_L, PTWP_A), drogi (SKJZ_L, SKDR_L, kategoria zarządzania), linie elektroenergetyczne (SULN_L), tory (SKTR_L), zabudowa (BUBD_A), las i zadrzewienia (PTLZ_A) | GUGiK BDOT10k, paczki GML per powiat: `https://opendata.geoportal.gov.pl/bdot10k/schemat2021/{WW}/{TERYT4}_GML.zip` | czytamy tylko potrzebne klasy przez HTTP Range (np. dla pow. średzkiego ok. 9 MB zamiast 24 MB); cache `output/cache/bdot10k/{TERYT4}/{KLASA}.wkb/.json`, geometrie uproszczone do 1 m, TTL 180 dni; data wersji pliku (Last-Modified) w proweniencji |
| Odległość od miasta, liczba mieszkańców | GUGiK PRNG WFS (`ms:M1_UrzedoweNazwyMiejscowosci`, filtr RODZAJOBIEKTU = miasto) + BDOT10k ADMS_A/ADMS_P (granica, `liczbaMieszkancow`) | odległość od granicy miasta; gdy granicy brak – od punktu PRNG z dopiskiem „od centrum” |
| Nachylenie | GUGiK NMT WCS 2.0.1, coverage `DTM_PL-KRON86-NH_TIFF` (1 m, EPSG:2180) | `SUBSET=x(E)`, `y(N)`; duże działki z mniejszą rozdzielczością (≤ 1500 px na bok); wartość 0,0 = luka w modelu; cache 365 dni |
| Zagrożenie powodziowe | ISOK / PGW Wody Polskie, WFS INSPIRE `INSPIRE_NZ_HY_MZPMRP_WFS`, `nz-core:HazardArea` | tylko `outputFormat=application/json` (GML z BBOX zwraca błąd serwera), bbox w kolejności szerokość, długość; Q10%/Q1%/Q0,2% wg `returnPeriod`; kafle 0,05° (po 3 naraz), przycięte i uproszczone do 1 m, cache 90 dni. Mapy obejmują wybrane rzeki – 0% nie gwarantuje braku zalewania |
| NDVI | Sentinel-2 (CDSE) | tylko z pamięci podręcznej ostatniej analizy gospodarstwa – bez nowych zapytań |

Bilans wodny gleby nie jest kryterium: wymaga pobrania pogody i modelu gleby dla każdego rejonu (jak pełna analiza).

**Wszystkie działki w wybranym obszarze (EGiB).** Zamiast „Moich działek” można filtrować wszystkie działki ewidencyjne gminy,
powiatu lub województwa:

| Element | Źródło | Uwagi |
|---|---|---|
| Działki (identyfikator, numer, obręb, gmina, powierzchnia ewidencyjna, granice) | GUGiK – zbiorcza usługa WFS EGiB `https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza`, warstwa `ms:dzialki` | WFS 2.0, GML 3.2, EPSG:2180 (BBOX w kolejności N, E), maks. 1000 obiektów na stronę + stronicowanie `next`; kafle 3 km, cache 30 dni (`output/cache/egib/t3000/`), 4 równoległe połączenia; działki trafiają do oceny partiami po 80 w miarę pobierania kafli (wyniki pojawiają się na mapie przyrostowo). **Pokrycie zależy od starostw**: usługa łączy powiatowe WFS EGiB z EZiUDP – gdy WFS powiatu nie odpowiada, aplikacja pokazuje „brak danych EGiB dla powiatu X”. Nie pobieramy i nie zapisujemy klas użytków ani grup rejestrowych |
| Granice gminy / powiatu / województwa | GUGiK ULDK (`GetCommuneById`, `GetCountyById`, `GetVoivodeshipById`) | cache 90 dni |
| Listy jednostek (cała Polska) | GUS BDL `bdl.stat.gov.pl/api/v1/units` | TERYT z identyfikatora BDL; cache 1 rok |

Przebieg: granica jednostki ∩ strefy kryteriów „najwyżej” (≤ X km od miasta, ≤ X m od wód) → kafle przecinające obszar →
szacunek liczby działek (`resultType=hits`); powyżej 20 000 – od razu komunikat „obszar za duży”. Pobrane działki: tylko z tej
jednostki (prefiks TERYT) i nachodzące na obszar; od razu odrzucane te, które nie spełniają powierzchni lub kształtu (liczba
podana w wyniku). Do drogich kryteriów trafia najwyżej 2000 działek; powyżej – komunikat, bez cichego obcinania. Dla
powiatu i województwa wymagane jest kryterium zawężające (miasto albo cieki „najwyżej”).

## 5. Konta i klucze

### Copernicus Data Space Ecosystem (wymagane dla NDVI)
1. Wejdź na https://dataspace.copernicus.eu → **Login / Register** → załóż bezpłatne konto i potwierdź e-mail.
2. Otwórz panel Sentinel Hub: https://shapps.dataspace.copernicus.eu/dashboard (zaloguj się tym samym kontem).
3. **User settings** → **OAuth clients** → **Create** (nazwa np. „mRolnik”), skopiuj *Client ID* i *Client secret*
   (sekret jest pokazywany tylko raz).
4. W pliku `.env`: `CDSE_CLIENT_ID=...`, `CDSE_CLIENT_SECRET=...`; uruchom ponownie serwer.
5. Sprawdź: `GET /api/sources/status?check=true` – pozycja Copernicus powinna mieć `ok: true`.

Limity: konto „Copernicus General” ma bezpłatny miesięczny przydział jednostek przetwarzania (PU) i żądań oraz limity na minutę
(aktualne wartości: zakładka *Quotas* w panelu). Jedna analiza działki to ok. 3–4 żądania (katalog, statystyki dzienne,
raster, historia miesięczna); wyniki są buforowane 6–24 h.

### Bez kluczy
- IMGW-PIB (danepubliczne.imgw.pl) – bez rejestracji; wymagane podanie źródła (patrz tabela).
- GUGiK ULDK, BDOT10k (WFS/WMS) – bez rejestracji.
- Open-Meteo – bez klucza do użytku **niekomercyjnego**. Wdrożenie produkcyjne (administracja, eRolnik) wymaga
  planu komercyjnego Open-Meteo albo własnej instancji (open-source, dane ECMWF/DWD) lub przejścia na dane IMGW-PIB / ECMWF
  bezpośrednio.

## 6. Kierunki rozwoju
- ARiMR LPIS/IACS (po zalogowaniu rolnika): działki rolne, uprawy z wniosku, TUZ – zastąpi ręczne wpisy i umożliwi
  rzetelną informację o ekoschematach.
- PGW Wody Polskie: ewidencja urządzeń melioracji wodnych (rowy, zastawki, spółki wodne) – zastąpi dane demo.
- IUNG-PIB: System Monitoringu Suszy Rolniczej (klimatyczny bilans wodny, kategorie gleb) – po udostępnieniu API.
- IMGW-PIB: dane pomiarowe deszczomierzy (opad z ostatnich dni zamiast analizy modelu), stany ostrzegawcze wodowskazów.
- Sentinel-1 (radar) – wilgotność powierzchniowa niezależna od chmur.

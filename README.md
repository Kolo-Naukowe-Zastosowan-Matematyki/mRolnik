# mRolnik

Prototyp modułu doradczego dla rolnika (HackNation, zadanie KOWR), projektowany jako rozszerzenie
erolnik.gov.pl. Jedna responsywna aplikacja (komputer i telefon): mapa działek, kondycja roślin
z Sentinel-2, wilgotność gleby, pogoda IMGW-PIB, woda i melioracja, kalkulatory siewu i nawożenia.

Każda liczba w interfejsie ma podane źródło i czas. Gdy źródło nie odpowiada, aplikacja pokazuje
„brak danych” z przyczyną, nigdy wartości zmyślone.

## Jak pobrać i uruchomić mRolnik – krok po kroku

Potrzebujesz komputera z internetem i ok. 500 MB wolnego miejsca. Aplikacja działa w przeglądarce,
ale serwer uruchamiasz u siebie – nic nie trzeba instalować poza Pythonem.

> W poleceniach poniżej `ADRES-REPOZYTORIUM` to adres tej strony na GitHubie
> (zielony przycisk **Code** → zakładka **HTTPS** → ikonka kopiowania).

### Windows

1. **Zainstaluj Pythona.** Wejdź na https://www.python.org/downloads/ i pobierz Pythona 3.12 lub nowszego.
   Uruchom instalator i **na pierwszym ekranie zaznacz „Add python.exe to PATH”**, potem kliknij **Install Now**.
2. **Pobierz mRolnik.** Na stronie repozytorium kliknij zielony przycisk **Code** → **Download ZIP**.
   W folderze Pobrane kliknij plik ZIP prawym przyciskiem → **Wyodrębnij wszystkie…** → **Wyodrębnij**.
   (Jeśli masz Gita: `git clone ADRES-REPOZYTORIUM mrolnik`.)
3. **Uruchom.** Otwórz rozpakowany folder i kliknij dwukrotnie plik **`mrolnik.bat`**.
   - Jeśli pojawi się okno „System Windows ochronił ten komputer”, kliknij **Więcej informacji** → **Uruchom mimo to**.
   - Za pierwszym razem skrypt przez kilka minut instaluje biblioteki. Kolejne uruchomienia trwają kilka sekund.
4. **Gotowe.** Przeglądarka sama otworzy adres http://127.0.0.1:8000/web.
   Aby zakończyć pracę, zamknij czarne okno z serwerem.

### macOS

1. **Zainstaluj Pythona.** Wejdź na https://www.python.org/downloads/ , pobierz instalator dla macOS
   (Python 3.12 lub nowszy) i przeklikaj instalację.
2. **Pobierz mRolnik.** Na stronie repozytorium kliknij zielony przycisk **Code** → **Download ZIP**.
   Safari sam rozpakuje plik w folderze **Pobrane** (powstanie folder np. `mrolnik-main`).
3. **Otwórz Terminal:** naciśnij **Cmd + Spacja**, wpisz `Terminal` i naciśnij Enter.
4. **Przejdź do folderu mRolnik.** Wpisz `cd ` (ze spacją na końcu), przeciągnij folder mRolnik z Findera
   do okna Terminala i naciśnij Enter.
5. **Uruchom:**
   ```bash
   bash mrolnik.sh
   ```
   Za pierwszym razem skrypt przez kilka minut instaluje biblioteki.
6. **Gotowe.** Przeglądarka sama otworzy adres http://127.0.0.1:8000/web.
   Aby zakończyć pracę, naciśnij **Ctrl + C** w oknie Terminala.

### Linux (Ubuntu / Debian; na innych dystrybucjach analogicznie)

1. **Zainstaluj Pythona i Gita** (Ubuntu 24.04 i nowsze mają odpowiednią wersję Pythona 3.12+):
   ```bash
   sudo apt update
   sudo apt install python3 python3-venv python3-pip git
   python3 --version        # powinno pokazać 3.12 lub wyżej
   ```
   Fedora: `sudo dnf install python3 git`.
2. **Pobierz mRolnik:**
   ```bash
   git clone ADRES-REPOZYTORIUM mrolnik
   cd mrolnik
   ```
3. **Uruchom:**
   ```bash
   bash mrolnik.sh
   ```
4. **Gotowe.** Przeglądarka otworzy http://127.0.0.1:8000/web (jeśli nie – otwórz ten adres ręcznie).
   Zakończenie pracy: **Ctrl + C** w terminalu.

### Zdjęcia satelitarne – darmowe klucze Copernicus (opcjonalnie, ok. 5 minut)

Bez kluczy aplikacja działa (mapa, działki, pogoda, woda, kalkulatory), ale kondycja roślin (NDVI) i historia
z satelity pokażą „brak danych”. Aby je włączyć:

1. Załóż bezpłatne konto na https://dataspace.copernicus.eu (**Login / Register**) i potwierdź e-mail.
2. Zaloguj się do panelu https://shapps.dataspace.copernicus.eu/dashboard → **User settings** → **OAuth clients**
   → **Create** (nazwa np. „mRolnik”). Skopiuj **Client ID** i **Client secret** – sekret jest pokazywany tylko raz.
3. W folderze mRolnik zrób kopię pliku `.env.example` i nazwij ją `.env`:
   - Windows (w oknie folderu wpisz `cmd` w pasku adresu i naciśnij Enter): `copy .env.example .env`
   - macOS / Linux (w Terminalu, w folderze mRolnik): `cp .env.example .env`
4. Otwórz `.env` w Notatniku / TextEdit / dowolnym edytorze i uzupełnij dwie linie:
   ```
   CDSE_CLIENT_ID=tu-wklej-client-id
   CDSE_CLIENT_SECRET=tu-wklej-client-secret
   ```
5. Uruchom mRolnik ponownie. W zakładce **Źródła** pozycja Copernicus powinna mieć status „działa”.

Plik `.env` zawiera Twoje prywatne klucze – jest w `.gitignore` i **nigdy nie wysyłaj go do repozytorium**.

### Najczęstsze problemy

| Objaw | Co zrobić |
|---|---|
| „nie znaleziono Pythona” (Windows) | Zainstaluj Pythona ponownie i zaznacz **Add python.exe to PATH**. |
| `ensurepip is not available` / błąd `venv` (Linux) | `sudo apt install python3-venv`, potem uruchom skrypt jeszcze raz. |
| Port 8000 jest zajęty | Nic nie trzeba robić: mRolnik sam wybierze następny wolny port (8001, 8002…) i otworzy właściwy adres – widać go w oknie serwera. |
| Przeglądarka się nie otworzyła | Otwórz ręcznie adres podany w oknie serwera (zwykle http://127.0.0.1:8000/web). |
| Strona wygląda „po staremu” po aktualizacji | Odśwież ją z pominięciem pamięci podręcznej: **Ctrl + F5** (macOS: **Cmd + Shift + R**). |
| Chcę pobrać nowszą wersję | Z Gitem: `git pull` w folderze mRolnik i uruchom ponownie. Bez Gita: pobierz nowy ZIP. |

Domyślnie serwer słucha tylko na 127.0.0.1 w trybie `dev` (lokalny użytkownik bez logowania), więc aplikacja
jest dostępna wyłącznie z Twojego komputera. Dostęp z telefonu w sieci lokalnej i logowanie: `docs/BEZPIECZENSTWO.md`.

## Źródła danych

| Dane | Źródło | Klucz |
|---|---|---|
| Granice działek | GUGiK ULDK | nie |
| NDVI, historia NDVI | Copernicus Sentinel-2 L2A (Copernicus Data Space Ecosystem) | tak (darmowy) |
| Pogoda bieżąca, ostrzeżenia, stany rzek | IMGW-PIB (danepubliczne.imgw.pl) | nie |
| Prognoza, wilgotność i temperatura gleby, reanaliza 10-letnia | Open-Meteo (modele ECMWF/DWD, ERA5 / ERA5-Land) | nie* |
| Uziarnienie i próchnica gleby (0–30 cm), kategoria suszowa I–IV | ISRIC SoilGrids v2.0 (`rest.isric.org`, CC BY 4.0) | nie |
| Ortofotomapa, cieki | GUGiK (geoportal.gov.pl) | nie |
| Plony w województwach | GUS BDL | nie |

\* Open-Meteo jest darmowe do użytku niekomercyjnego; wdrożenie produkcyjne wymaga planu płatnego lub własnej instancji.
Szczegóły, licencje i częstotliwość aktualizacji: `docs/ZRODLA_DANYCH.md`. Stan źródeł na żywo: zakładka „Źródła” lub
`/api/sources/status?check=true`.

Uprawy w gospodarstwach przykładowych są przyjęte (typowy płodozmian regionu) – działki i ich granice są prawdziwe (ULDK).

## Struktura

- `server.py` – aplikacja FastAPI, middleware bezpieczeństwa, routery
- `src/security/` – logowanie (dev / hasło / OIDC, przygotowane pod login.gov.pl), sesje, CSRF, CSP, limity
- `src/data/` – konektory: ULDK, Sentinel-2 (CDSE), IMGW-PIB, Open-Meteo, GUS
- `src/hydromelior/` – woda i melioracja: bilans wodny, wodowskazy, cieki, zalecenia zastawek, ekoschemat (informacyjnie)
- `src/calculators/` – kalkulatory siewu i nawożenia
- `src/web/` – endpointy API
- `static/` – interfejs (HTML + CSS + JS, bez CDN; Leaflet i Chart.js w `static/vendor/`)
- `scripts/` – skrypty budujące dane referencyjne (normy IMGW, gminy azotanowe, działki przykładów)
- `docs/` – plan, bezpieczeństwo, źródła danych, GUI
- `archive/` – stare wersje (tylko lokalnie, poza repozytorium)

## Testy (dla programistów)

Windows:
```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q tests
```
macOS / Linux:
```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
```
Test interfejsu (Node + Playwright): `node tests/test_ui_smoke.mjs` (opis w `docs/GUI.md`).

Nowe działki dla gospodarstw przykładowych (pyta GUGiK ULDK, trwa ok. 5–10 minut, zapisuje `src/data/presets_dzialki.json`;
potem uruchom mRolnik ponownie):
```
.venv\Scripts\python scripts\build_presets.py      # Windows
.venv/bin/python scripts/build_presets.py          # macOS / Linux
```

## Status

Prototyp. Dane mają charakter informacyjny i nie zastępują planu nawozowego, analizy gleby ani
dokumentów składanych do ARiMR.

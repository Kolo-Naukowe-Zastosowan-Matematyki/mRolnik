# Uwagi właściciela produktu (Maciek, 15.11.2026, ok. 13:20) do interfejsu

Uwagi dotyczą starego UI (web.html/mobile.html), ale obowiązują nowy, jeden responsywny interfejs.

1. **Wiersz wyszukiwania, jeden rząd:** pole wyszukiwania (z lupą) zajmuje 0–1/2 szerokości,
   przycisk "Dodaj własne pole" 1/2–3/4, przycisk CSV 3/4–1. Placeholder wyszukiwarki może się
   zawijać do 2 linii, żeby cały tekst był czytelny. Ten układ także na telefonie.
2. **Usuń wiersz z województwem / gospodarstwem przykładowym** jako osobny wiersz u góry.
   Przykładowe gospodarstwa (demo) mogą zostać najwyżej w zwiniętej sekcji "Przykłady (demo)".
3. **Pola wyboru i sekcje domyślnie zwinięte**, z możliwością rozwinięcia (wzorzec `<details>/<summary>`
   lub przycisk z `aria-expanded`).
4. **Usuń rozwijaną listę wskaźników nad mapą** (wybór NDVI/NDWI/EVI + zakres dat + link do Copernicus
   Browser). Wystarczą przełączniki warstw bezpośrednio nad/na mapie (jeden zestaw, bez duplikatów).
5. **Aktywna zakładka zawsze podświetlona**, także pierwsza ("Główna"/"Pole"), zarówno na starcie, jak i po
   powrocie (stan `aria-selected="true"` + wyraźny styl).
6. **Usuń zakładkę "Web"** z paska (nie ma przełączania web/mobile; jedna aplikacja responsywna).
7. **Spójność web i mobile:** te same komponenty, kolejność, nazwy i dane; różni się tylko układ.
8. **Wolne ładowanie zakładek** (stara zakładka "Plony" długo stała z "--"): żadna zakładka nie może czekać
   na wszystko. Szkielet od razu, sekcje doładowują się osobno, limit czasu, jasny stan "brak danych" z
   przyczyną i przyciskiem "Spróbuj ponownie".
9. **Melioracja / retencja:**
   - dane mają być pobierane automatycznie z rządowych źródeł (nie ręcznie wpisana sieć rowów),
   - sekcja ma być uporządkowana, z wykresami i infografikami zamiast ścian tekstu.

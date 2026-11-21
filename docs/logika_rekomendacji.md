# Logika Generowania Rekomendacji Gospodarowania Wodą i Retencji
## System mRolnik – Moduł doradczy gospodarki wodnej (KOWR: „Zatrzymaj wodę, chroń plony”)

> **Kluczowe założenie merytoryczne:** Niniejszy silnik rekomendacji koncentruje się **wyłącznie na gospodarowaniu wodą, retencji krajobrazowej i glebowej, przeciwdziałaniu suszy oraz racjonalnym nawadnianiu**. Wszelkie zalecenia niezwiązane z bilansem wodnym (np. nawożenie azotowe, terminy stosowania chemicznych środków ochrony roślin, regulatory wzrostu) zostały wykluczone, aby zagwarantować pełną zgodność z celem wyzwania KOWR.

---

## 1. Zestawienie Grup Retencyjnych Upraw w Polsce

Klasyfikacja upraw opiera się wyłącznie na **fizjologii pobierania wody**, **architekturze systemu korzeniowego**, **dynamice ewapotranspiracji ($ET_c = K_c \cdot ET_0$)** oraz **wpływie uprawy na pojemność wodną i retencję gleby**. Każda uprawa zgłoszona przez rolnika zostaje przypisana do jednej z 7 grup retencyjnych:

| ID Grupy | Nazwa Grupy Retencyjnej | Reprezentatywne Uprawy w Polsce | Głębokość Korzeni | Zapotrzebowanie Wodne & Wrażliwość | Rola w Retencji Wodnej i Uzasadnienie |
|---|---|---|---|---|---|
| `deep_structured` | **Głęboko korzeniące się i strukturotwórcze** | Rzepak ozimy, Lucerna siewna, Koniczyna czerwona, Bobik, Łubin żółty, Łubin wąskolistny, Groch siewny, Facelia błękitna, Rzodkiew oleista | 120–250 cm (korzeń palowy silnie penetrujący) | Średnie/wysokie; wysoka odporność na suszę dzięki sięganiu do wód podglebia; wrażliwe na długotrwałe podtopienia | **Tworzenie bioporów drenarsko-retencyjnych** rozluźniających podeszwę płużną; ułatwiają szybką infiltrację opadów nawalnych w głąb profilu glebowego zamiast spływu powierzchniowego. Zwiększają retencyjną pojemność wodną podglebia. |
| `high_transpiration_late` | **Wysokotranspirujące o dużym zapotrzebowaniu wodnym (późny sezon)** | Kukurydza na ziarno, Kukurydza na kiszonkę, Burak cukrowy, Burak pastewny, Słonecznik, Soja, Sorgo | 80–150 cm (silny wiązkowy lub korzeń spichrzowy) | Bardzo wysokie (500–700 mm); szczyt poboru wody w lipcu i sierpniu ($K_c \approx 1,20$); wrażliwe na suszę w fazie wiechowania/kwitnienia | **Intensywne osuszanie profilu glebowego w szczycie lata**. Wczesną wiosną gleba jest odsłonięta (wysokie parowanie fizyczne wody z gleby). Wymagają maksymalnej retencji wody pozimowej, mulczowania międzyrzędzi i wczesnego siewu. |
| `winter_cereals_early` | **Zboża ozime o wczesnym cyklu wegetacji** | Pszenica ozima, Żyto ozime, Pszenżyto ozime, Jęczmień ozimy | 60–120 cm (rozwinięty jesienią system wiązkowy) | Umiarkowane (300–450 mm); szczyt zapotrzebowania na wodę: maj–początek czerwca; wysoka efektywność wykorzystania zapasu wody pozimowej | **Ochrona przed parowaniem glebowym wczesną wiosną** dzięki wczesnemu zwarciu łanu. Pozostawiają dużą masę resztek pożniwnych na powierzchni gleby, tworzącą mulcz chroniący wilgoć przed upałami. |
| `spring_cereals_sensitive` | **Zboża jare o płytkim ukorzenieniu (wrażliwe na susze majowe)** | Jęczmień jary, Pszenica jara, Owies, Pszenżyto jare, Proso, Gryka | 40–80 cm (płytki system wiązkowy) | Umiarkowane, ale silnie skumulowane w krótkim oknie wegetacji; **bardzo niska odporność na deficyty wody w maju i czerwcu** | Płytkie korzenie nie sięgają wody kapilarnej z głębszych warstw profilu. Odsłonięta gleba do połowy maja nasila parowanie wody. Wymagają uprawy bezorkowej zatrzymującej wilgoć w wierzchniej warstwie. |
| `shallow_vegetables_tubers` | **Okopowe płytko korzeniące się i warzywa gruntowe** | Ziemniak wczesny, Ziemniak późny, Marchew, Cebula, Kapusta, Burak ćwikłowy, Ogórek, Pomidor, Seler | 30–60 cm (płytki, wrażliwy na zagęszczenie) | Bardzo wysokie, stałe i równomierne; **skrajnie wrażliwe na suszę (drobnienie plonu) oraz na zastoje wody (gnicie korzeni i bulw)** | Uprawa w redlinach sprzyja parowaniu i spływowi wody wzdłuż stoku. Wymagają precyzyjnego mikronawadniania kroplowego oraz poprzecznego profilowania redlin zapobiegającego ucieczce wody opadowej. |
| `grassland_cover` | **Trwała okrywa roślinna i retencja darniowa (TUZ i trawy)** | Łąki trwałe, Pastwiska trwałe, Trawy na gruntach ornych, Mieszanki traw z motylkowatymi, Koniczyna biała | 20–50 cm (gęsta, zwarta darń korzeniowa) | Ciągłe, wysokie przez cały sezon wegetacyjny; wysoka zdolność szybkiej regeneracji; wysoka odporność na okresowe zalanie | **Najwyższy potencjał retencji krajobrazowej i przeciwerozyjnej**. Gęsta darń działa jak naturalna gąbka, spowalnia odpływ powierzchniowy wody, zatrzymuje wodę w strefie korzeniowej i zapobiega zamulaniu rowów melioracyjnych. |
| `perennial_orchard_berry` | **Wieloletnie uprawy sadownicze i jagodowe** | Sady jabłoniowe, gruszowe, wiśniowe, śliwowe, Truskawka, Malina, Porzeczka, Borówka amerykańska | 60–180 cm (wieloletnie korzenie zdrewniałe / krzewy) | Średnie/wysokie; krytyczne w czasie kwitnienia i dorastania owoców; drzewa wrażliwe na asfiksję korzeni przy długotrwałych zastoiskach | **Zadarnione międzyrzędzia stabilizują retencję wody na stokach**. Wymagają ściółkowania strefy podkoronowej (zrębki, słoma, agrowłóknina) ograniczającego bezproduktywne parowanie wody z gleby. |

---

## 2. Matryca Rozumowania Wodnego (IF → THEN)

Zgodnie z wymogami konkursu KOWR (*Pełna treść zadania*, Rozdz. 3.2 oraz *Podstawy naukowe*), generowanie rekomendacji przebiega w **bezwzględnej, 5-etapowej sekwencji przyczynowo-skutkowej**:

```
[1. Ocena sytuacji wodnej] ──> [2. Identyfikacja przyczyn] ──> [3. Retencja krajobrazowa i glebowa (PRIORYTET)] ──> [4. Agrotechnika retencyjna] ──> [5. Nawadnianie (warunkowe)]
```

---

### ETAP 1 & 2: Ocena Sytuacji Wodnej i Identyfikacja Przyczyn Deficytu / Nadmiaru Wody

#### Reguła D-01: Głęboki deficyt opadów i susza glebowa
- **JEŚLI (IF):**
  - Klimatyczny Bilans Wodny ($KBW_{14d} < -25\text{ mm}$ lub $KBW_{30d} < -50\text{ mm}$), ORAZ
  - Wskaźnik wilgotności satelitarnej $NDMI < 0,15$ (lub spadek $NDMI > 30\%$ w stosunku do normy wieloletniej), ORAZ
  - Gleba należy do kategorii podatności IUNG I (bardzo lekka) lub II (lekka).
- **TO IDENTYFIKUJ PRZYCZYNY (THEN CAUSES):**
  - Krytyczny deficyt opadów atmosferycznych w połączeniu z wysokim parowaniem ($ET_0$).
  - Niska pojemność wodna gleby lekkiej (szybkie przesiąkanie grawitacyjne poza zasięg strefy korzeniowej).
  - Przyspieszone wysychanie wierzchniej warstwy gleby (0–30 cm) zagrażające wegetacji.

#### Reguła D-02: Ryzyko erozji wodnej i ucieczki wody na stokach (NMT DEM)
- **JEŚLI (IF):**
  - Średnie nachylenie stoku z Numerycznego Modelu Terenu $DEM > 3\%$ (umiarkowane) lub $> 6\%$ (duże), ORAZ
  - Uprawa w grupie `high_transpiration_late` (kukurydza, buraki) lub `shallow_vegetables_tubers` (ziemniaki, warzywa), ORAZ
  - Występują prognozy opadów nawalnych ($\ge 20\text{ mm/dobę}$).
- **TO IDENTYFIKUJ PRZYCZYNY (THEN CAUSES):**
  - Gwałtowny spływ powierzchniowy wody wzdłuż linii spadku stoku zamiast infiltracji w głąb profilu.
  - Odsłonięta gleba i uprawa rzędowa sprzyjająca erozji wodnej oraz tworzeniu żłobin erozyjnych.
  - Bezpowrotny odpływ wody opadowej z działki przed zasileniem strefy korzeniowej.

#### Reguła D-03: Ryzyko zastoisk wodnych i asfiksji korzeni w obniżeniach terenu
- **JEŚLI (IF):**
  - Wskaźnik wilgotności gleby $AWC > 95\%$ (nasycenie powyżej polowej pojemności wodnej), ORAZ
  - Wskaźnik $NDWI > 0,25$ lub rzeźba terenu wskazuje bezodpływowe zagłębienie (zlewnia lokalna), ORAZ
  - Gleba ciężka/zwięzła (kategoria IUNG IV: glina, ił).
- **TO IDENTYFIKUJ PRZYCZYNY (THEN CAUSES):**
  - Zastoiska wodne spowodowane słabą przepuszczalnością podglebia i brakiem odprowadzenia nadmiaru wód spływowych.
  - Ryzyko asfiksji (uduszenia z braku tlenu) korzeni, gnicia systemu korzeniowego i zamierania roślin.

---

### ETAP 3: Rekomendacje Retencyjne – Krajobrazowe i Glebowe (NAJWYŻSZY PRIORYTET)

> **Zasada naczelna KOWR:** *„Zatrzymaj wodę tam, gdzie spadła”*. Zanim rolnik pomyśli o kosztownym nawadnianiu, system musi wskazać środki zatrzymania wody w krajobrazie i profilu glebowym.

#### Reguła R-LAND-01: Zastawki na rowach melioracyjnych (Sterowanie odpływem)
- **JEŚLI (IF):**
  - Na działce lub w buforze $\le 100\text{ m}$ znajduje się rów melioracyjny (BDOT10k / EGiB), ORAZ
  - Prognozowany bilans wodny $KBW_{14d} < -10\text{ mm}$ i brak ostrzeżeń powodziowych IMGW-PIB.
- **TO REKOMENDUJ RETENCJĘ KRAJOBRAZOWĄ (THEN):**
  - **Działanie:** Zamknięcie lub przymknięcie zastawek na rowie melioracyjnym (piętrzenie wód drenażowych do poziomu 40–60 cm pod powierzchnią terenu).
  - **Efekt wodny:** Podsiąk kapilarny zasilający strefę korzeniową roślin; spowolnienie odpływu wód roztopowych i wiosennych; ochrona przed przesuszeniem mikrozlewni.
  - **Wsparcie finansowe:** Ekoschemat WPR: *Retencjonowanie wody na trwałych użytkach zielonych* (ok. 280 zł/ha) lub programy małej retencji KOWR / Wody Polskie.

#### Reguła R-LAND-02: Pasy buforowe i zadrzewienia śródpolne na stokach
- **JEŚLI (IF):**
  - Średni spadek działki $DEM > 3\%$, ORAZ
  - Działka graniczy z ciekiem wodnym, rowem melioracyjnym lub doliną rzeczną.
- **TO REKOMENDUJ RETENCJĘ KRAJOBRAZOWĄ (THEN):**
  - **Działanie:** Założenie zadarnionego pasa buforowego (szerokość 3–5 m) wzdłuż dolnej krawędzi działki lub wprowadzenie śródpolnych pasów zadrzewień / zakrzaczeń (np. wierzba, głóg, tarnina).
  - **Efekt wodny:** Przechwycenie 70–90% spływu powierzchniowego wody opadowej, wymuszenie infiltracji w strefę korzeniową, obniżenie prędkości wiatru nad polem i redukcja parowania eolicznego o 30%.

#### Reguła R-SOIL-01: Budowanie pojemności wodnej gleby (Próchnicowa gąbka wodna)
- **JEŚLI (IF):**
  - Gleba zaliczona do kategorii podatności na suszę IUNG I lub II (piaski słabogliniaste, piaski luźne), ORAZ
  - Uprawa w grupie `high_transpiration_late`, `spring_cereals_sensitive` lub `winter_cereals_early`.
- **TO REKOMENDUJ RETENCJĘ GLEBOWĄ (THEN):**
  - **Działanie:** Zwiększanie zawartości substancji organicznej w glebie poprzez wprowadzanie obornika, kompostu oraz bezwzględne pozostawianie na polu całości rozdrobnionej słomy i resztek pożniwnych jako mulczu.
  - **Efekt wodny:** Każdy wzrost zawartości próchnicy o 1% zwiększa pojemność wodną gleby o ok. 150–200 $m^3$/ha (15–20 mm opadu zatrzymanego w warstwie ornej zamiast bezproduktywnego odpływu grawitacyjnego).

#### Reguła R-SOIL-02: Międzyplony okrywowe i strukturotwórcze (Ochrona wilgoci)
- **JEŚLI (IF):**
  - Uprawa schodzi z pola w lipcu lub sierpniu (zboża ozime, rzepak), a po niej planowana jest uprawa jara lub kukurydza.
- **TO REKOMENDUJ RETENCJĘ GLEBOWĄ (THEN):**
  - **Działanie:** Natychmiastowy siew międzyplonu ścierniskowego lub ozimego (mieszanka rzodkwi oleistej, facelii, łubinu i wyki).
  - **Efekt wodny:** Ciągła zielona okrywa gleby chroniąca przed parowaniem w upalnym okresie sierpień–wrzesień; głębokie korzenie drenują profil glebowy; mulcz wymarzający zimą zatrzymuje i chroni zapas wody pozimowej.

---

### ETAP 4: Działania Agrotechniczne (Zorientowane na Ochronę Wody)

#### Reguła AGRO-01: Uprawa konserwująca i bezorkowa (Ograniczenie parowania gleby)
- **JEŚLI (IF):**
  - Grupa retencyjna: `high_transpiration_late` (kukurydza), `deep_structured` (rzepak) lub `winter_cereals_early`, ORAZ
  - Wysokie ryzyko suszy wiosennej w historii 10-letniej.
- **TO REKOMENDUJ AGROTECHNIKĘ (THEN):**
  - **Działanie:** Rezygnacja z tradycyjnej orki płużnej z odkładaniem skiby na rzecz uprawy pasowej (strip-till) lub spulchniania bezodwracowego z pozostawieniem warstwy mulczu na powierzchni pola.
  - **Uzasadnienie wodne:** Tradycyjna orka wiosenna powoduje natychmiastowe odparowanie 15–25 mm wody z wierzchniej warstwy gleby. Mulcz na powierzchni obniża temperaturę gleby o 3–6°C i redukuje parowanie fizyczne o 40%.

#### Reguła AGRO-02: Uprawa w poprzek stoku (Konturowa retencja opadu)
- **JEŚLI (IF):**
  - Nachylenie terenu $DEM > 3\%$.
- **TO REKOMENDUJ AGROTECHNIKĘ (THEN):**
  - **Działanie:** Prowadzenie wszystkich zabiegów uprawowych, siewu i formowania redlin ściśle w poprzek stoku (równolegle do poziomic terenu).
  - **Uzasadnienie wodne:** Każda redlina i bruzda działa jak mikrował zatrzymujący spływającą wodę opadową, wielokrotnie wydłużając czas na jej wsiąknięcie w głąb profilu glebowego.

#### Reguła AGRO-03: Głęboszowanie podeszwy płużnej (Udostępnienie wody kapilarnej)
- **JEŚLI (IF):**
  - Gleba zwięzła lub średnia z widoczną historią powstawania zastoisk po ulewach, ORAZ
  - Planowana uprawa rzepaku ozimego lub lucerny (`deep_structured`).
- **TO REKOMENDUJ AGROTECHNIKĘ (THEN):**
  - **Działanie:** Zastosowanie głębosza na głębokość 45–55 cm pod koniec lata (w warunkach gleby suchej/kruchej).
  - **Uzasadnienie wodne:** Rozbicie zagęszczonej podeszwy płużnej przywraca podsiąk kapilarny wody z głębszych warstw podglebia oraz umożliwia odprowadzenie nadmiaru wód opadowych w głąb profilu, likwidując zastoje.

---

### ETAP 5: Rekomendacje Nawadniania (Opcjonalne, Warunkowe i Precyzyjne)

> **Zasada:** Nawadnianie jest rekomendowane **wyłącznie** dla upraw o wysokiej wrażliwości ekonomicznej i fizjologicznej (`shallow_vegetables_tubers`, `perennial_orchard_berry`, selektywnie kukurydza i buraki), gdy bilans wodny wykazuje długotrwały deficyt, a metody retencyjne nie pokrywają zapotrzebowania w fazie krytycznej.

#### Reguła IRRIG-01: Mikronawadnianie kroplowe w warzywach i jagodnikach
- **JEŚLI (IF):**
  - Grupa retencyjna: `shallow_vegetables_tubers` lub `perennial_orchard_berry` (truskawki, borówki, sady), ORAZ
  - Dostępna woda w glebie $AWC < 50\%$ w warstwie 0–30 cm, ORAZ
  - Faza krytyczna zapotrzebowania na wodę: kwitnienie, wiązanie owoców / przyrost bulw.
- **TO REKOMENDUJ NAWADNIANIE (THEN):**
  - **Decyzja:** Nawadnianie uzasadnione ekonomicznie i hydrologicznie.
  - **Technologia:** Wyłącznie linie kroplowe podkoronowe / podściółkowe (unikać deszczowni szpulowych powodujących 30–40% strat wskutek bezpośredniego parowania kropel w powietrzu).
  - **Dawka i termin:** Dawki małe i precyzyjne (10–15 mm na cykl), podawane w godzinach nocnych lub wczesnoporannych w celu minimalizacji strat ewaporacyjnych.

#### Reguła IRRIG-02: Ograniczenie lub brak uzasadnienia deszczowania zbóż
- **JEŚLI (IF):**
  - Grupa retencyjna: `winter_cereals_early` lub `spring_cereals_sensitive` w uprawie polowej, ORAZ
  - Brak dostępnego legalnego punktu poboru wody / deficyt wód w zlewni.
- **TO REKOMENDUJ (THEN):**
  - **Decyzja:** Deszczowanie nieuzasadnione ekonomicznie i hydrologicznie (wysoki koszt energii, wysokie straty ewaporacyjne).
  - **Alternatywa retencyjna:** Zastąpienie sztucznego deszczowania retencją korytową w rowach melioracyjnych (podsiąk) oraz agrotechniką bezorkową ograniczającą parowanie wody z gleby.

---

## 3. Szablon Rozbudowy Reguł Retencji Wodnej

Aby dodać nową regułę do silnika rekomendacji, należy zastosować poniższy znormalizowany format dedykowany gospodarce wodnej:

```markdown
### [KOD_REGUŁY]: [Nazwa reguły w języku polskim]
- **Kategoria:** [Ocena bilansu wodnego | Retencja krajobrazowa | Retencja glebowa | Agrotechnika retencyjna | Nawadnianie precyzyjne]
- **Priorytet wykonania:** [Krytyczny | Wysoki | Średni | Opcjonalny]
- **Warunki wejściowe (IF):**
  - Uprawa / Grupa retencyjna: ...
  - Gleba (IUNG / tekstura / AWC / podatność na suszę): ...
  - Rzeźba terenu (DEM / spadek % / bezodpływowe obniżenia): ...
  - Wskaźniki satelitarne (NDVI / NDMI / NDWI): ...
  - Bilans wodny (KBW / opad / ET0): ...
  - Sieć melioracyjna / cieki (odległość m / obecność zastawek): ...
- **Działanie rekomendowane (THEN):**
  - Tytuł rekomendacji dla rolnika: ...
  - Szczegółowe wskazówki wykonawcze (jasny język bez żargonu): ...
  - Oczekiwany zysk wodny (np. zatrzymanie X mm wody, ograniczenie parowania o Y%): ...
  - Zgodność z ekoschematami retencyjnymi / źródła finansowania: ...
- **Poziom niepewności (Uncertainty Level):** [Niski / Średni / Podwyższony – zależny od kompletności danych źródłowych]
```

---

## 4. Integracja z Wymogami Konkursowymi KOWR

1. **Brak fałszywej precyzji:** Jeśli brakuje danych (np. zachmurzenie satelitarne, brak pobliskiej stacji IMGW), reguła obniża wskaźnik wiarygodności i wprost informuje rolnika: *„Brak aktualnego zdjęcia satelitarnego z powodu zachmurzenia – wnioski oparto na modelu hydrologicznym ERA5-Land i stacji meteorologicznej”*.
2. **Uwzględnienie daty analizy (teraźniejszość i 10 lat wstecz):** Warunki w regułach zawsze przyjmują `analysis_date` jako punkt odcięcia czasowego. Nigdy nie korzystają z danych przyszłych ani nie bazują wyłącznie na dacie dzisiejszej.
3. **Zgodność z normami ochrony wód:** Reguły uwzględniają normę GAEC 6 (minimalna okrywa glebowa w celu ochrony wody i ograniczenia erozji wodnej) oraz ekoschemat *Retencjonowanie wody na trwałych użytkach zielonych*. Wszelkie porady wykraczające poza gospodarkę wodną zostały usunięte.

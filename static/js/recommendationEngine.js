// Silnik rekomendacji retencji wody w gospodarstwie rolnym
// Zgodny z wytycznymi KOWR: „Zatrzymaj wodę, chroń plony”
// oraz logiką opisaną w logika_rekomendacji.md.
//
// Zwraca dane podzielone na:
// 1. Główną rekomendację zbiorczą dla całego gospodarstwa
// 2. Sekcję „Szczegóły” z rozbiciem na pojedyncze działki

import { getCropRetentionGroup, findCrop } from './crops_retention.js';

/**
 * Bezpieczna konwersja do liczby lub null
 */
function toNum(v) {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * Rozpoznaje kategorię i zwięzłość gleby
 */
function classifySoil(soilStr) {
  if (!soilStr) return { isLight: false, isHeavy: false, category: null, desc: 'Brak danych o glebie' };
  const s = String(soilStr).toLowerCase();

  const isLight = s.includes('bardzo lekk') || s.includes('lekk') || s.includes('piask') ||
                  s.includes('luźn') || s.includes('kat. i') || s.includes('kat. 1') ||
                  s.includes('kat. ii') || s.includes('kat. 2');

  const isHeavy = s.includes('ciężk') || s.includes('ił') || s.includes('glin') ||
                  s.includes('zwięzł') || s.includes('kat. iv') || s.includes('kat. 4');

  let category = null;
  let desc = 'Gleba średnia';
  if (s.includes('kat. i') || s.includes('kat. 1') || s.includes('bardzo lekk')) {
    category = 'I';
    desc = 'Gleba bardzo lekka (piasek) – woda ucieka bardzo szybko';
  } else if (s.includes('kat. ii') || s.includes('kat. 2') || s.includes('lekk')) {
    category = 'II';
    desc = 'Gleba lekka – niska zdolność zatrzymywania wody';
  } else if (s.includes('kat. iii') || s.includes('kat. 3') || s.includes('średn')) {
    category = 'III';
    desc = 'Gleba średnia – umiarkowane zatrzymywanie wody';
  } else if (s.includes('kat. iv') || s.includes('kat. 4') || s.includes('ciężk')) {
    category = 'IV';
    desc = 'Gleba ciężka/zwięzła – ryzyko zastoisk po ulewach';
  }

  return { isLight, isHeavy, category, desc };
}

/**
 * Generuje szczegółowe rekomendacje dla pojedynczej działki.
 * Teksty pisane krótkimi, prostymi zdaniami, zrozumiałymi dla rolnika.
 *
 * @param {Object} plotData Dane działki (z ULDK, DEM, ERA5, Sentinel-2)
 * @param {Object|null} cropInfo Informacja o uprawie
 * @returns {Array<Object>} Lista zaleceń dla działki
 */
export function generateRecommendations(plotData, cropInfo = null) {
  if (!plotData || typeof plotData !== 'object') {
    return [];
  }

  // 1. Uprawa i grupa retencyjna
  let resolvedCrop = cropInfo;
  const cropName = plotData.crop || (cropInfo && cropInfo.name) || '';
  if (!resolvedCrop && cropName) {
    resolvedCrop = findCrop(plotData.cropId || cropName);
  }

  const retentionGroup = (resolvedCrop && resolvedCrop.retention_group_id)
    ? getCropRetentionGroup(resolvedCrop.retention_group_id)
    : getCropRetentionGroup(cropName) || {
        id: 'standard',
        name: 'Standardowa uprawa polowa',
        short_label: 'Uprawa polowa',
      };

  const groupId = retentionGroup.id || 'standard';
  const groupName = retentionGroup.name || 'Uprawa polowa';

  // 2. Parametry działki
  const sm = plotData.soil_moisture || {};
  const awc = toNum(sm.available_water_pct);
  const surfaceMoist = toNum(sm.surface_pct);
  const fieldCap = toNum(sm.field_capacity_pct);
  const waterReserveDays = toNum(plotData.water_reserve_days);

  const slope = toNum(plotData.slope) !== null
    ? toNum(plotData.slope)
    : (toNum(plotData.slope_pct) !== null ? toNum(plotData.slope_pct) : toNum(plotData.dem_slope));

  const kbw = toNum(plotData.kbw) !== null
    ? toNum(plotData.kbw)
    : (plotData.weather_local && toNum(plotData.weather_local.climatic_water_balance_14d_mm) !== null
        ? toNum(plotData.weather_local.climatic_water_balance_14d_mm)
        : null);

  const precip14d = plotData.weather_local ? toNum(plotData.weather_local.precip_14d_mm) : null;

  const ndmi = toNum(plotData.ndmi) !== null
    ? toNum(plotData.ndmi)
    : (plotData.layers && toNum(plotData.layers.ndmi) !== null ? toNum(plotData.layers.ndmi) : null);

  const ndwi = toNum(plotData.ndwi) !== null
    ? toNum(plotData.ndwi)
    : (plotData.layers && toNum(plotData.layers.ndwi) !== null ? toNum(plotData.layers.ndwi) : null);

  const ndvi = plotData.ndvi && typeof plotData.ndvi === 'object' ? toNum(plotData.ndvi.mean) : null;

  const soilInfo = classifySoil(plotData.soil);

  const nearDitch = Boolean(
    plotData.has_ditch ||
    plotData.near_water ||
    (toNum(plotData.ditch_distance_m) !== null && toNum(plotData.ditch_distance_m) <= 120)
  );

  let missingPoints = 0;
  if (kbw === null) missingPoints++;
  if (awc === null && surfaceMoist === null) missingPoints++;
  if (slope === null) missingPoints++;
  if (ndmi === null && ndvi === null) missingPoints++;

  const poziomNiepewnosci = missingPoints >= 2 ? 'podwyższony' : (missingPoints === 1 ? 'średni' : 'niski');

  const recommendations = [];

  // =========================================================================
  // ETAP 1 & 2: Diagnoza sytuacji wodnej
  // =========================================================================

  // Reguła D-01: Głęboki deficyt opadów i susza glebowa
  const isSevereDrought = (
    (kbw !== null && kbw < -20) ||
    (waterReserveDays !== null && waterReserveDays <= 3) ||
    (awc !== null && awc < 40)
  );
  const isVulnerableSoil = soilInfo.isLight || soilInfo.category === 'I' || soilInfo.category === 'II';

  if (isSevereDrought && (isVulnerableSoil || (ndmi !== null && ndmi < 0.18))) {
    recommendations.push({
      id: 'D-01',
      kategoria: 'Diagnoza wodna',
      priorytet: 'krytyczny',
      tytul: 'Silny brak wody w glebie (zagrożenie suszą)',
      opis: 'Nie wykonuj płytkich uprawek broną ani kultywatorem. Każde wzruszenie ziemi w upale przyspiesza parowanie resztek wilgoci. Chroń zapas wody w strefie korzeni.',
      uzasadnienie: `Klimatyczny bilans wodny wynosi ${kbw !== null ? `${kbw.toFixed(1)} mm` : 'poniżej normy'}. Zapas wody w glebie wystarczy na mniej niż 3 dni. Gleba jest lekka i woda szybko ucieka w głąb.`,
      zyskWodny: 'Zatrzymujesz 10–15 mm wody w glebie przez ograniczenie parowania.',
      ekoschemat: 'Ekoschemat: Rolnictwo węglowe – Uproszczone systemy uprawy',
      poziomNiepewnosci,
    });
  }

  // Reguła D-02: Ryzyko erozji wodnej i ucieczki wody na stokach
  const isSteep = slope !== null && slope >= 3.0;
  const isRowCrop = groupId === 'high_transpiration_late' || groupId === 'shallow_vegetables_tubers';

  if (isSteep && (isRowCrop || (precip14d !== null && precip14d >= 20))) {
    recommendations.push({
      id: 'D-02',
      kategoria: 'Diagnoza wodna',
      priorytet: 'wysoki',
      tytul: 'Ryzyko spływu wody i zmywania ziemi na stoku',
      opis: 'Prowadź wszystkie zabiegi ściśle w poprzek stoku. Nie uprawiaj pola wzdłuż linii spadu. Woda po ulewie spływa zboczem i nie wsiąka w korzenie.',
      uzasadnienie: `Spadek terenu wynosi ${slope.toFixed(1)}%. Uprawa szerokorzędowa sprzyja powstawaniu żłobin i ucieczce wody opadowej.`,
      zyskWodny: 'Ograniczasz spływ powierzchniowy o 60–80%. Woda wsiąka w pole.',
      ekoschemat: 'Normy GAEC 5 i GAEC 6 – Ochrona gleb na stokach',
      poziomNiepewnosci,
    });
  }

  // Reguła D-03: Ryzyko zastoisk wodnych
  const isSaturated = (awc !== null && awc > 90) || (surfaceMoist !== null && fieldCap !== null && surfaceMoist >= fieldCap + 3);
  const isWaterLogging = isSaturated || (ndwi !== null && ndwi > 0.20);

  if (isWaterLogging && (soilInfo.isHeavy || (slope !== null && slope < 1.0))) {
    recommendations.push({
      id: 'D-03',
      kategoria: 'Diagnoza wodna',
      priorytet: 'wysoki',
      tytul: 'Zastoiska wody i ryzyko uduszenia korzeni',
      opis: 'Nie wjeżdżaj na pole ciężkim ciągnikiem ani kombajnem. Mokra ziemia łatwo się ugniata. Zniszczona struktura gleby zablokuje wsiąkanie wody na lata.',
      uzasadnienie: `Gleba jest mocno nasycona wodą. Teren jest płaski lub w zagłębieniu. Podglebie zwięzłe słabo przepuszcza wodę.`,
      zyskWodny: 'Chronisz korzenie przed zgnilizną i brakiem tlenu.',
      ekoschemat: null,
      poziomNiepewnosci,
    });
  }

  // =========================================================================
  // ETAP 3: Retencja krajobrazowa i glebowa (NAJWYŻSZY PRIORYTET)
  // =========================================================================

  // Reguła R-LAND-01: Zastawki na rowach melioracyjnych
  if (nearDitch || (kbw !== null && kbw < -5)) {
    recommendations.push({
      id: 'R-LAND-01',
      kategoria: 'Retencja krajobrazowa',
      priorytet: isSevereDrought ? 'krytyczny' : 'wysoki',
      tytul: 'Zamknij zastawki na rowach melioracyjnych',
      opis: 'Opuść zastawki na rowie przy polu. Zatrzymaj wodę na głębokości 40–60 cm pod powierzchnią gruntu. Woda podsiąknie do korzeni roślin.',
      uzasadnienie: `Rów melioracyjny znajduje się blisko działki, a bilans wodny jest ujemny (${kbw !== null ? `${kbw.toFixed(1)} mm` : 'brak opadów'}). Nie pozwól wodzie odpływać z pola.`,
      zyskWodny: 'Zyskujesz 20–40 mm darmowej wody podsiąkowej prosto w strefę korzeni.',
      ekoschemat: 'Ekoschemat: Retencjonowanie wody na TUZ (ok. 280 zł/ha) oraz dotacje KOWR i Wód Polskich',
      poziomNiepewnosci,
    });
  }

  // Reguła R-LAND-02: Pasy buforowe i zadrzewienia na stokach
  if (isSteep) {
    recommendations.push({
      id: 'R-LAND-02',
      kategoria: 'Retencja krajobrazowa',
      priorytet: 'wysoki',
      tytul: 'Zostaw pas trawy lub krzewów na dole stoku',
      opis: 'Wysiej pas trawy o szerokości 3–5 metrów wzdłuż dolnej krawędzi zbocza. Możesz posadzić też krzewy. Taki pas wyhamuje spływającą wodę i zatrzyma ją na polu.',
      uzasadnienie: `Spadek terenu wynosi ${slope.toFixed(1)}%. Woda deszczowa ucieka ze stoku. Rośliny pasowe osłaniają też pole przed suchym wiatrem.`,
      zyskWodny: 'Zatrzymujesz do 80% spływu wody opadowej.',
      ekoschemat: 'Ekoschemat: Pasy buforowe i miedze śródpolne',
      poziomNiepewnosci,
    });
  }

  // Reguła R-SOIL-01: Budowanie pojemności wodnej gleby (Próchnica)
  if (isVulnerableSoil || groupId === 'high_transpiration_late' || groupId === 'spring_cereals_sensitive') {
    recommendations.push({
      id: 'R-SOIL-01',
      kategoria: 'Retencja glebowa',
      priorytet: isSevereDrought ? 'krytyczny' : 'wysoki',
      tytul: 'Zostawiaj całą słomę na polu – buduj próchnicę',
      opis: 'Rozdrobnij całą słomę i wymieszaj płytko z ziemią. Stosuj obornik lub kompost. Próchnica w glebie działa jak gąbka i trzyma wodę deszczową.',
      uzasadnienie: `Gleba lekka (${plotData.soil || 'piasek/bielica'}) szybko wysycha. Każdy 1% próchnicy więcej pozwala zatrzymać na polu dodatkowo do 200 tysięcy litrów wody na 1 ha.`,
      zyskWodny: '+15–20 mm zatrzymanej wody na każdy 1% wzrostu próchnicy.',
      ekoschemat: 'Ekoschemat: Wymieszanie słomy z glebą (Rolnictwo węglowe)',
      poziomNiepewnosci,
    });
  }

  // Reguła R-SOIL-02: Międzyplony okrywowe
  if (
    groupId === 'winter_cereals_early' ||
    groupId === 'spring_cereals_sensitive' ||
    groupId === 'deep_structured'
  ) {
    recommendations.push({
      id: 'R-SOIL-02',
      kategoria: 'Retencja glebowa',
      priorytet: 'wysoki',
      tytul: 'Wysiej międzyplon zaraz po żniwach',
      opis: 'Zaraz po zbiorze uprawy wysiej mieszankę poplonową (np. rzodkiew, facelia, łubin). Nie zostawiaj czarnego ścierniska na słońcu. Poplon cieniuje glebę i chroni wilgoć.',
      uzasadnienie: `Uprawa z grupy ${groupName} zwalnia pole w środku lata. Odsłonięta ziemia traci 2–3 litry wody z metra kwadratowego każdego dnia.`,
      zyskWodny: 'Zmniejszasz parowanie z gleby o połowę i zatrzymujesz wodę na zimę.',
      ekoschemat: 'Ekoschemat: Międzyplony ozime / ścierniskowe (ok. 500 zł/ha)',
      poziomNiepewnosci,
    });
  }

  // =========================================================================
  // ETAP 4: Agrotechnika retencyjna
  // =========================================================================

  // Reguła AGRO-01: Uprawa bezorkowa i mulcz
  if (
    groupId === 'high_transpiration_late' ||
    groupId === 'deep_structured' ||
    groupId === 'winter_cereals_early' ||
    isSevereDrought
  ) {
    recommendations.push({
      id: 'AGRO-01',
      kategoria: 'Agrotechnika retencyjna',
      priorytet: 'wysoki',
      tytul: 'Ogranicz tradycyjną orkę – przejdź na uprawę bezorkową',
      opis: 'Zrezygnuj z orki odwracającej skibę na rzecz spulchniania bezodwracowego lub siewu pasowego (strip-till). Zostawiaj warstwę resztek na wierzchu pola.',
      uzasadnienie: `Tradycyjna orka wiosenna wyciąga wilgoć na słońce. W dwa dni z wysuszonej skiby ucieka do 25 litrów wody na metr kwadratowy.`,
      zyskWodny: 'Oszczędzasz 20–30 mm wody w glebie na starcie wegetacji.',
      ekoschemat: 'Ekoschemat: Uproszczone systemy uprawy (Rolnictwo węglowe)',
      poziomNiepewnosci,
    });
  }

  // Reguła AGRO-02: Uprawa w poprzek stoku
  if (isSteep) {
    recommendations.push({
      id: 'AGRO-02',
      kategoria: 'Agrotechnika retencyjna',
      priorytet: 'wysoki',
      tytul: 'Wszystkie uprawki i siew prowadź w poprzek stoku',
      opis: 'Prowadź ciągnik ściśle wzdłuż poziomic terenu. Każda redlina w poprzek zbocza zadziała jak mała tama i zatrzyma spływającą wodę.',
      uzasadnienie: `Spadek terenu wynosi ${slope.toFixed(1)}%. Bruzdy wzdłuż stoku tworzą rynny, którymi woda ucieka z pola.`,
      zyskWodny: 'Woda z ulew wsiąka w glebę zamiast spływać do rowów.',
      ekoschemat: 'Wymóg normy GAEC 5',
      poziomNiepewnosci,
    });
  }

  // Reguła AGRO-03: Głęboszowanie podeszwy płużnej
  if (
    soilInfo.isHeavy ||
    groupId === 'deep_structured' ||
    (plotData.has_plow_pan === true)
  ) {
    recommendations.push({
      id: 'AGRO-03',
      kategoria: 'Agrotechnika retencyjna',
      priorytet: 'średni',
      tytul: 'Zgłęboszuj zbitą podeszwę płużną na 45–55 cm',
      opis: 'Przejedź pole głęboszem późnym latem, gdy gleba jest sucha i pęka. Udrożnisz podsiąk wody z głębi ziemi do korzeni roślin.',
      uzasadnienie: `Zwięzłe podglebie blokuje podsiąk wody kapilarnej w czasie suszy. Zbita podeszwa płużna nie pozwala korzeniom sięgać głębiej.`,
      zyskWodny: 'Korzenie zyskują dostęp do wody z głębokości poniżej 40 cm.',
      ekoschemat: null,
      poziomNiepewnosci,
    });
  }

  // =========================================================================
  // ETAP 5: Nawadnianie precyzyjne
  // =========================================================================

  // Reguła IRRIG-01: Mikronawadnianie kroplowe
  if (
    groupId === 'shallow_vegetables_tubers' ||
    groupId === 'perennial_orchard_berry'
  ) {
    const isWaterStress = (awc !== null && awc < 55) || (waterReserveDays !== null && waterReserveDays <= 4);
    recommendations.push({
      id: 'IRRIG-01',
      kategoria: 'Nawadnianie precyzyjne',
      priorytet: isWaterStress ? 'krytyczny' : 'średni',
      tytul: 'Podlewaj kropelkowo nocą lub wczesnym rankiem',
      opis: 'Zastosuj podpowierzchniowe lub podściółkowe linie kroplowe. Podlewaj małymi dawkami (10–15 mm) w nocy. Unikaj deszczowni szpulowych w upale.',
      uzasadnienie: `Uprawa z grupy ${groupName} ma płytkie korzenie i szybko więdnie. Zwykłe zraszacze tracą w dzień do 40% wody przez parowanie w powietrzu.`,
      zyskWodny: 'Do 95% podanej wody trafia prosto do strefy korzeniowej.',
      ekoschemat: 'Wsparcie na inwestycje w nawadnianie w gospodarstwie (Plan Strategiczny WPR)',
      poziomNiepewnosci,
    });
  }

  // Reguła IRRIG-02: Ograniczenie deszczowania zbóż
  if (
    groupId === 'winter_cereals_early' ||
    groupId === 'spring_cereals_sensitive'
  ) {
    recommendations.push({
      id: 'IRRIG-02',
      kategoria: 'Nawadnianie precyzyjne',
      priorytet: 'informacyjny',
      tytul: 'Nie podlewaj zbóż deszczowniami – to się nie opłaca',
      opis: 'Zrezygnuj ze sztucznego deszczowania zbóż. Podlewanie polowe kosztuje za dużo prądu i wody. Zadbaj o zatrzymanie wody w rowach i mulczowanie.',
      uzasadnienie: `Zboża mają umiarkowaną opłacalność podlewania. Taniej i skuteczniej podniesiesz plon przez retencję wody w rowach i próchnicę.`,
      zyskWodny: 'Oszczędzasz wodę oraz wysokie koszty energii i paliwa.',
      ekoschemat: null,
      poziomNiepewnosci,
    });
  }

  // =========================================================================
  // FALLBACK: Reguła BASE-01
  // =========================================================================
  if (recommendations.length === 0) {
    recommendations.push({
      id: 'BASE-01',
      kategoria: 'Zalecenie bazowe',
      priorytet: 'średni',
      tytul: `Podstawowa ochrona wody w glebie dla uprawy: ${cropName || groupName}`,
      opis: 'Chroń ziemię przed wysychaniem. Zostawiaj resztki roślinne na powierzchni pola i unikaj głębokiej orki w czasie posuchy.',
      uzasadnienie: `Zalecenie ogólne dla grupy retencyjnej: ${groupName}. Chroni profil glebowy przed ucieczką wilgoci.`,
      zyskWodny: 'Zmniejszasz parowanie z wierzchniej warstwy gleby.',
      ekoschemat: null,
      poziomNiepewnosci: 'podwyższony',
    });
  }

  return recommendations;
}

/**
 * Generuje spójną, syntetyczną Główną Rekomendację Zbiorczą dla całego gospodarstwa.
 * Napisana krótkimi, prostymi zdaniami bez żargonu.
 */
function buildMainCollectiveRecommendation(parcels, detailsParcels) {
  const count = parcels.length;
  const totalArea = parcels.reduce((sum, p) => sum + (toNum(p.area_ha) || 0), 0);
  const totalAreaStr = `${totalArea.toFixed(2).replace('.', ',')} ha`;

  // Analiza statystyk działek
  let steepCount = 0;
  let lightSoilCount = 0;
  let heavySoilCount = 0;
  let droughtCount = 0;
  let ditchCount = 0;
  let criticalCount = 0;

  detailsParcels.forEach((dp) => {
    const p = dp.daneDzialki;
    const slope = toNum(p.slope);
    if (slope !== null && slope >= 3.0) steepCount++;

    const soilInfo = classifySoil(p.soil);
    if (soilInfo.isLight) lightSoilCount++;
    if (soilInfo.isHeavy) heavySoilCount++;

    const sm = p.soil_moisture || {};
    const awc = toNum(sm.available_water_pct);
    const days = toNum(p.water_reserve_days);
    const kbw = toNum(p.kbw) !== null ? toNum(p.kbw) : (p.weather_local ? toNum(p.weather_local.climatic_water_balance_14d_mm) : null);

    if ((kbw !== null && kbw < -15) || (days !== null && days <= 3) || (awc !== null && awc < 45)) {
      droughtCount++;
    }

    if (p.has_ditch || p.near_water || (toNum(p.ditch_distance_m) !== null && toNum(p.ditch_distance_m) <= 120)) {
      ditchCount++;
    }

    dp.rekomendacje.forEach((r) => {
      if (r.priorytet === 'krytyczny') criticalCount++;
    });
  });

  // Ustalenie statusu i priorytetu
  let status = 'Dobra sytuacja wodna';
  let priorytet = 'sredni';
  if (droughtCount > 0 || criticalCount > 0) {
    status = 'Wysokie zagrożenie suszą glebową';
    priorytet = 'krytyczny';
  } else if (steepCount > 0 || lightSoilCount > 0) {
    status = 'Wymagane pilne działania retencyjne';
    priorytet = 'wysoki';
  }

  // Zwięzła, syntetyczna diagnoza zbiorcza
  const diagnozaCzesci = [];
  diagnozaCzesci.push(`Przeanalizowano ${count} ${count === 1 ? 'działkę' : 'działki'} o łącznej powierzchni ${totalAreaStr}.`);

  if (droughtCount > 0) {
    diagnozaCzesci.push(`Na ${droughtCount === count ? 'wszystkich polach' : `${droughtCount} z nich`} brakuje wody w glebie. Zapas wilgoci dla roślin jest krytycznie mały.`);
  } else {
    diagnozaCzesci.push('Zapas wilgoci w glebie jest obecnie zadowalający, ale wymaga stałej ochrony przed parowaniem.');
  }

  if (lightSoilCount > 0) {
    diagnozaCzesci.push(`${lightSoilCount === count ? 'Wszystkie pola mają' : `${lightSoilCount} działek ma`} lekką, piaszczystą glebę, z której woda szybko ucieka w głąb.`);
  }

  if (steepCount > 0) {
    diagnozaCzesci.push(`${steepCount === count ? 'Pola leżą' : `${steepCount} działek leży`} na stoku o spadku powyżej 3%. Woda po ulewach spływa po zboczu i nie zasila korzeni.`);
  }

  if (ditchCount > 0) {
    diagnozaCzesci.push(`W sąsiedztwie pól znajdują się rowy melioracyjne, które można wykorzystać do nawadniania podsiąkowego.`);
  }

  // Kluczowe zalecenia krok po kroku
  const kroki = [];

  // Krok 1: rowy / wstrzymanie uprawek
  if (ditchCount > 0) {
    kroki.push('Zamknij zastawki na rowach melioracyjnych. Przytrzymaj wodę na poziomie 40–60 cm pod ziemią. Woda sama podsiąknie do korzeni roślin.');
  } else if (droughtCount > 0) {
    kroki.push('Nie wykonuj płytkich uprawek przedsiewnych w czasie upałów. Każde ruszenie ziemi wysusza glebę.');
  } else {
    kroki.push('Chroń wilgoć pozimową. Nie dopuszczaj do przesuszania wierzchniej warstwy gleby przez zbędne przejazdy.');
  }

  // Krok 2: ochrona gleby i próchnica
  kroki.push('Zostaw całą słomę na polu i wymieszaj ją płytko z ziemią. Próchnica działa jak gąbka. Zwiększa ilość zatrzymanej wody deszczowej.');

  // Krok 3: stok lub uprawa bezorkowa
  if (steepCount > 0) {
    kroki.push('Wszystkie zabiegi na stoku wykonuj wyłącznie w poprzek zbocza. Zostaw pasy zieleni na dole pola, aby zatrzymać spływającą wodę.');
  } else {
    kroki.push('Zastąp tradycyjną orkę ze skibą uprawą bezorkową lub pasową (strip-till). Warstwa mulczu na wierzchu pola zmniejsza parowanie wody o 40%.');
  }

  // Krok 4: międzyplony
  kroki.push('Zaraz po żniwach wysiej międzyplon ścierniskowy. Zielona okrywa chroni glebę przed upałem w sierpniu i zatrzymuje wodę na jesień.');

  // Zysk wodny i ekoschematy
  const zyskWodny = droughtCount > 0 || lightSoilCount > 0
    ? 'Wdrożenie tych działań zatrzyma w glebie od 20 do 35 mm dodatkowej wody. Rośliny przetrwają okresy bezdeszczowe nawet o 2–3 tygodnie dłużej.'
    : 'Stabilizacja bilansu wilgotnościowego gleby i zatrzymanie do 20 mm wody z opadów nawalnych.';

  const ekoschematy = [
    'Wymieszanie słomy z glebą (Rolnictwo węglowe)',
    'Uproszczone systemy uprawy bezorkowej',
    ditchCount > 0 ? 'Retencjonowanie wody na TUZ (ok. 280 zł/ha)' : null,
    'Międzyplony ozime / ścierniskowe (ok. 500 zł/ha)',
  ].filter(Boolean);

  return {
    tytul: 'Główny plan retencji wody dla Twojego gospodarstwa',
    statusZagrozenia: status,
    priorytet,
    lacznaPowierzchniaHa: totalAreaStr,
    liczbaDzialek: count,
    syntetycznaTresc: diagnozaCzesci.join(' '),
    glowneZaleceniaKrokPoKroku: kroki,
    zyskWodnyGospodarstwa: zyskWodny,
    ekoschematyWPR: ekoschematy,
  };
}

/**
 * Główna funkcja analityczna projektu mRolnik dla modułu rekomendacji.
 * Zwraca ustrukturyzowany raport podzielony ściśle na:
 * 1. `glownaRekomendacja` (zbiorcza dla całego gospodarstwa)
 * 2. `szczegoly` (dedykowana sekcja z rozbiciem na pojedyncze działki)
 *
 * @param {Array<Object>} parcels Lista przeanalizowanych działek
 * @returns {Object} Gotowy raport retencyjny
 */
export function generateRetentionReport(parcels) {
  if (!Array.isArray(parcels) || parcels.length === 0) {
    return {
      glownaRekomendacja: {
        tytul: 'Brak wybranych działek',
        statusZagrozenia: 'Brak danych',
        priorytet: 'informacyjny',
        lacznaPowierzchniaHa: '0,00 ha',
        liczbaDzialek: 0,
        syntetycznaTresc: 'Wybierz przynajmniej jedną działkę w Kroku 1, aby przygotować rekomendacje retencji wody.',
        glowneZaleceniaKrokPoKroku: [],
        zyskWodnyGospodarstwa: 'Brak danych',
        ekoschematyWPR: [],
      },
      szczegoly: {
        liczbaDzialek: 0,
        lacznaPowierzchniaHa: '0,00 ha',
        dzialki: [],
      },
    };
  }

  // 1. Opracowanie szczegółów dla każdej działki
  const dzialkiSzczegoly = parcels.map((p, idx) => {
    const area = toNum(p.area_ha) || 0;
    const title = p.id_tag || (p.parcel_id ? `Dz. ${p.parcel_id.split('.').pop()}` : `Działka ${idx + 1}`);

    const cropObj = findCrop(p.cropId || p.crop);
    const retGroup = getCropRetentionGroup(p.cropId || p.crop) || {
      id: 'standard',
      name: 'Standardowa uprawa polowa',
      short_label: 'Uprawa polowa',
      badge_class: 'mr-badge--ret-generic',
    };

    const soilInfo = classifySoil(p.soil);
    const slope = toNum(p.slope);
    const kbw = toNum(p.kbw) !== null ? toNum(p.kbw) : (p.weather_local ? toNum(p.weather_local.climatic_water_balance_14d_mm) : null);
    const days = toNum(p.water_reserve_days);
    const ndmi = toNum(p.ndmi) !== null ? toNum(p.ndmi) : (p.layers ? toNum(p.layers.ndmi) : null);

    const recs = generateRecommendations(p);

    const locationParts = [p.commune ? `gm. ${p.commune}` : null, p.county ? `pow. ${p.county}` : null].filter(Boolean);

    return {
      indeks: idx,
      tytul: title,
      teryt: p.parcel_id || '',
      powierzchniaHa: area,
      powierzchniaStr: `${area.toFixed(2).replace('.', ',')} ha`,
      lokalizacjaStr: locationParts.join(' · '),
      uprawa: p.crop || 'Uprawa nieokreślona',
      grupaRetencyjnaId: retGroup.id,
      grupaRetencyjnaNazwa: retGroup.name,
      grupaRetencyjnaKrotka: retGroup.short_label || retGroup.name,
      grupaRetencyjnaBadge: retGroup.badge_class || 'mr-badge--ret-generic',
      parametryWodne: {
        glebaTekst: soilInfo.desc,
        glebaKat: soilInfo.category ? `Kat. IUNG ${soilInfo.category}` : (p.soil || 'Średnia'),
        spadekTekst: slope !== null ? `${slope.toFixed(1).replace('.', ',')}% (${slope >= 3 ? 'stok – ryzyko spływu' : 'teren nizinny'})` : 'Poniżej 2% (płasko)',
        spadekWartosc: slope,
        kbwTekst: kbw !== null ? `${kbw.toFixed(1).replace('.', ',')} mm (${kbw < 0 ? 'deficyt' : 'optymalnie'})` : 'Deficyt wody',
        kbwWartosc: kbw,
        zapasWodyTekst: days !== null ? `${days} dni do stresu suszowego` : 'Brak danych',
        zapasWodyDni: days,
        ndmiTekst: ndmi !== null ? `${ndmi.toFixed(2).replace('.', ',')} (uwodnienie roślin)` : 'W normie',
        ndmiWartosc: ndmi,
      },
      rekomendacje: recs,
      daneDzialki: p,
    };
  });

  // 2. Opracowanie Głównej Rekomendacji Zbiorczej
  const glownaRekomendacja = buildMainCollectiveRecommendation(parcels, dzialkiSzczegoly);

  const totalArea = parcels.reduce((sum, p) => sum + (toNum(p.area_ha) || 0), 0);

  return {
    glownaRekomendacja,
    szczegoly: {
      liczbaDzialek: parcels.length,
      lacznaPowierzchniaHa: `${totalArea.toFixed(2).replace('.', ',')} ha`,
      dzialki: dzialkiSzczegoly,
    },
  };
}

/**
 * Podsumowanie dla wstecznej kompatybilności z poprzednimi importami
 */
export function generateFarmRetentionSummary(parcels) {
  const rep = generateRetentionReport(parcels);
  const priorityCounts = { krytyczny: 0, wysoki: 0, sredni: 0, informacyjny: 0 };

  rep.szczegoly.dzialki.forEach((d) => {
    d.rekomendacje.forEach((r) => {
      const p = r.priorytet || 'sredni';
      if (priorityCounts[p] !== undefined) priorityCounts[p]++;
    });
  });

  return {
    totalParcels: rep.szczegoly.liczbaDzialek,
    totalAreaHa: parcels.reduce((sum, p) => sum + (toNum(p.area_ha) || 0), 0),
    priorityCounts,
    overallStatus: rep.glownaRekomendacja.statusZagrozenia,
    statusClass: rep.glownaRekomendacja.priorytet === 'krytyczny' ? 'dry' : (rep.glownaRekomendacja.priorytet === 'wysoki' ? 'low' : 'ok'),
    topAction: rep.glownaRekomendacja.glowneZaleceniaKrokPoKroku[0] || 'Chroń wodę w profilu glebowym.',
    report: rep,
  };
}

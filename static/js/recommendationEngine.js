// Silnik rekomendacji retencji wody w gospodarstwie rolnym
// Zaimplementowany na podstawie wytycznych KOWR („Zatrzymaj wodę, chroń plony”)
// oraz pliku logika_rekomendacji.md.
//
// Bezwzględna sekwencja przyczynowo-skutkowa:
// 1. Ocena sytuacji wodnej i identyfikacja przyczyn (D-01, D-02, D-03)
// 2. Retencja krajobrazowa i glebowa (R-LAND-01, R-LAND-02, R-SOIL-01, R-SOIL-02) – PRIORYTET
// 3. Agrotechnika retencyjna (AGRO-01, AGRO-02, AGRO-03)
// 4. Nawadnianie precyzyjne (IRRIG-01, IRRIG-02) – warunkowe
// 5. Zalecenie bazowe (BASE-01) – fallback

import { getCropRetentionGroup, findCrop } from './crops_retention.js';

/**
 * Bezpieczna konwersja do liczby lub null
 */
function toNum(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === 'string' && v.trim() === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * Ekstrahuje wartość liczbową wskaźnika satelitarnego (liczba lub obiekt { mean })
 */
function extractIndexMean(directVal, layerVal) {
  if (directVal !== null && directVal !== undefined) {
    if (typeof directVal === 'object') {
      const m = toNum(directVal.mean);
      if (m !== null) return m;
    } else {
      const n = toNum(directVal);
      if (n !== null) return n;
    }
  }
  if (layerVal !== null && layerVal !== undefined) {
    if (typeof layerVal === 'object') {
      return toNum(layerVal.mean);
    }
    return toNum(layerVal);
  }
  return null;
}

/**
 * Wykrywa kategorię gleby wg IUNG (I–IV) lub teksturę lekką/ciężką
 */
function classifySoil(soilStr) {
  if (!soilStr) return { isLight: false, isHeavy: false, category: null };
  const s = String(soilStr).toLowerCase();

  // Wykrywanie kategorii IUNG z zachowaniem granic słów (IV przed I, III przed II/I)
  let cat = null;
  if (/\bkat\.?\s*(?:iv|4)\b/.test(s)) cat = 'IV';
  else if (/\bkat\.?\s*(?:iii|3)\b/.test(s)) cat = 'III';
  else if (/\bkat\.?\s*(?:ii|2)\b/.test(s)) cat = 'II';
  else if (/\bkat\.?\s*(?:i|1)\b/.test(s)) cat = 'I';

  const isVeryLight = s.includes('bardzo lekk') || s.includes('piasek luźn') || (/\bpiasek\b/.test(s) && !s.includes('gliniast'));
  const isLightText = isVeryLight || s.includes('gleba lekk') || (s.includes('lekk') && !s.includes('glina lekk')) ||
                      s.includes('piasek gliniast') || s.includes('słabogliniast') || s.includes('piaszczyst') ||
                      s.includes('luźn') || s.includes('bielic');
  const isHeavyText = s.includes('bardzo ciężk') || s.includes('ciężk') || /\bił/.test(s) ||
                      s.includes('ilast') || s.includes('zwięzł') || s.includes('mada') ||
                      s.includes('czarne ziemie') || s.includes('czarna ziemia');
  const isMediumText = s.includes('średn') || (!isHeavyText && !isLightText && s.includes('glin'));

  if (!cat) {
    if (isVeryLight) cat = 'I';
    else if (isLightText && !isHeavyText) cat = 'II';
    else if (isHeavyText) cat = 'IV';
    else if (isMediumText) cat = 'III';
  }

  const isLight = cat === 'I' || cat === 'II' || (isLightText && !isHeavyText && cat !== 'III' && cat !== 'IV');
  const isHeavy = cat === 'IV' || (isHeavyText && !isLight);

  return { isLight, isHeavy, category: cat };
}

/**
 * Normalizuje klucz priorytetu do postaci ASCII ('krytyczny' | 'wysoki' | 'sredni' | 'informacyjny')
 */
export function normalizePriority(p) {
  if (!p) return 'sredni';
  const s = String(p).toLowerCase().trim();
  if (s === 'średni' || s === 'sredni') return 'sredni';
  if (s === 'krytyczny' || s === 'wysoki' || s === 'informacyjny') return s;
  return 'sredni';
}

/**
 * Główna funkcja generująca rekomendacje retencyjne dla pojedynczej działki.
 *
 * @param {Object} plotData Dane działki (z modelu danych aplikacji, np. ULDK, NMT, ERA5, Sentinel-2)
 * @param {Object|null} cropInfo Informacja o uprawie (lub wyszukiwana automatycznie)
 * @returns {Array<Object>} Lista wygenerowanych rekomendacji retencyjnych
 */
export function generateRecommendations(plotData, cropInfo = null) {
  if (!plotData || typeof plotData !== 'object') {
    return [];
  }

  // 1. Ustalenie uprawy i grupy retencyjnej
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

  // 2. Bezpieczna ekstrakcja parametrów wodno-glebowo-terenowych
  const sm = plotData.soil_moisture || {};
  const awc = toNum(sm.available_water_pct);
  const surfaceMoist = toNum(sm.surface_pct);
  const rootMoist = toNum(sm.root_zone_pct);
  const fieldCap = toNum(sm.field_capacity_pct);
  const waterReserveDays = toNum(plotData.water_reserve_days);

  // Nachylenie ze skanu DEM/NMT
  const slope = toNum(plotData.slope) !== null
    ? toNum(plotData.slope)
    : (toNum(plotData.slope_pct) !== null ? toNum(plotData.slope_pct) : toNum(plotData.dem_slope));

  // Klimatyczny bilans wodny (KBW) i opady
  const kbw = toNum(plotData.kbw) !== null
    ? toNum(plotData.kbw)
    : (plotData.weather_local && toNum(plotData.weather_local.climatic_water_balance_14d_mm) !== null
        ? toNum(plotData.weather_local.climatic_water_balance_14d_mm)
        : null);

  const precip14d = plotData.weather_local ? toNum(plotData.weather_local.precip_14d_mm) : null;

  // Wskaźniki satelitarne (obsługa liczb oraz obiektów warstw { mean, water_pct, wet_pct })
  const layers = plotData.layers || {};
  const ndmi = extractIndexMean(plotData.ndmi, layers.ndmi);
  const ndwi = extractIndexMean(plotData.ndwi, layers.ndwi);
  const ndwiLayer = (layers.ndwi && typeof layers.ndwi === 'object') ? layers.ndwi : null;
  const ndwiWaterPct = ndwiLayer ? toNum(ndwiLayer.water_pct) : null;
  const ndwiWetPct = ndwiLayer ? toNum(ndwiLayer.wet_pct) : null;
  const ndvi = extractIndexMean(plotData.ndvi, layers.ndvi);

  // Klasyfikacja gleby (preferuj jawną kategorię działki, z zapasowym soil_class z modelu)
  const explicitSoil = plotData.soil || null;
  const modelSoil = sm && sm.soil_class ? sm.soil_class : null;
  const soilLabel = explicitSoil || modelSoil;
  const soilInfo = classifySoil(soilLabel);

  // Wskaźniki obecności rowów i cieków
  const nearDitch = Boolean(
    plotData.has_ditch ||
    plotData.near_water ||
    (toNum(plotData.ditch_distance_m) !== null && toNum(plotData.ditch_distance_m) <= 120)
  );

  // Obliczenie poziomu niepewności
  let missingPoints = 0;
  if (kbw === null) missingPoints++;
  if (awc === null && surfaceMoist === null && rootMoist === null) missingPoints++;
  if (slope === null) missingPoints++;
  if (ndmi === null && ndvi === null) missingPoints++;

  const poziomNiepewnosci = missingPoints >= 2 ? 'podwyższony' : (missingPoints === 1 ? 'średni' : 'niski');

  const recommendations = [];

  // =========================================================================
  // ETAP 1 & 2: Ocena sytuacji wodnej i identyfikacja przyczyn deficytu/nadmiaru
  // =========================================================================

  // Reguła D-01: Głęboki deficyt opadów i susza glebowa
  const isSevereDrought = (
    (kbw !== null && kbw < -20) ||
    (waterReserveDays !== null && waterReserveDays <= 3) ||
    (awc !== null && awc < 40)
  );
  const isExtremeMeasuredDrought = (
    (waterReserveDays !== null && waterReserveDays <= 2) ||
    (awc !== null && awc < 30)
  );
  const isVulnerableSoil = soilInfo.isLight || soilInfo.category === 'I' || soilInfo.category === 'II';

  if (isSevereDrought && (isVulnerableSoil || (ndmi !== null && ndmi < 0.18) || isExtremeMeasuredDrought)) {
    const reasons = [];
    if (kbw !== null) reasons.push(`klimatyczny bilans wodny 14d: ${kbw.toFixed(1).replace('.', ',')} mm`);
    if (waterReserveDays !== null) reasons.push(`zapas wody: ${waterReserveDays} dni`);
    if (awc !== null) reasons.push(`woda użyteczna: ${Math.round(awc)}%`);
    if (ndmi !== null && ndmi < 0.18) reasons.push(`obniżony wskaźnik uwodnienia NDMI: ${ndmi.toFixed(2).replace('.', ',')}`);
    if (soilLabel) reasons.push(`gleba: ${soilLabel}`);
    recommendations.push({
      id: 'D-01',
      kategoria: 'Diagnoza wodna',
      priorytet: 'krytyczny',
      tytul: 'Krytyczny deficyt wody w glebie (zagrożenie suszą rolniczą)',
      opis: 'Stwierdzono wyczerpanie zapasów wody łatwo dostępnej w warstwie ornej i korzeniowej. Wstrzymaj wszelkie płytkie uprawki napowietrzające i intensywne bronowanie, które gwałtownie przyspieszają parowanie resztek wilgoci z gleby.',
      uzasadnienie: reasons.length
        ? `${reasons.join('; ')}.`
        : 'Wykryto głęboki deficyt wody w profilu glebowym.',
      zyskWodny: 'Zatrzymanie 10–15 mm wody w glebie przez ograniczenie bezproduktywnej ewaporacji.',
      ekoschemat: 'Ekoschemat: Rolnictwo węglowe – Uproszczone systemy uprawy',
      poziomNiepewnosci,
    });
  }

  // Reguła D-02: Ryzyko erozji wodnej i ucieczki wody na stokach (NMT DEM)
  const isSteep = slope !== null && slope >= 3.0;
  const isRowCrop = groupId === 'high_transpiration_late' || groupId === 'shallow_vegetables_tubers';

  if (isSteep && (isRowCrop || (precip14d !== null && precip14d >= 20))) {
    const precipInfo = precip14d !== null && precip14d >= 20
      ? `, prognozowane/odnotowane opady 14d: ${precip14d.toFixed(0)} mm`
      : '';
    recommendations.push({
      id: 'D-02',
      kategoria: 'Diagnoza wodna',
      priorytet: 'wysoki',
      tytul: 'Ryzyko szybkiego spływu powierzchniowego i erozji wodnej',
      opis: 'Nachylenie terenu sprzyja bezpowrotnemu odpływowi wód opadowych wzdłuż linii spadu zamiast ich wsiąkania w profil glebowy. Na uprawach szerokorzędowych grozi to tworzeniem bruzd erozyjnych.',
      uzasadnienie: `Średni spadek działki z modelu terenu DEM wynosi ${slope.toFixed(1).replace('.', ',')}% przy uprawie z grupy: ${groupName}${precipInfo}.`,
      zyskWodny: 'Zmniejszenie strat spływowych o 60–80% i zwiększenie infiltracji w strefę korzeniową.',
      ekoschemat: 'Norma GAEC 5 i GAEC 6 – Ochrona gleb na stokach',
      poziomNiepewnosci,
    });
  }

  // Reguła D-03: Ryzyko zastoisk wodnych i asfiksji korzeni w obniżeniach terenu
  const isSaturated = (awc !== null && awc > 90) || (surfaceMoist !== null && fieldCap !== null && surfaceMoist >= fieldCap + 3);
  const isWaterLogging = isSaturated ||
    (ndwi !== null && ndwi > 0.20) ||
    (ndwiWaterPct !== null && ndwiWaterPct >= 5) ||
    (ndwiWetPct !== null && ndwiWetPct >= 15);

  if (isWaterLogging && (soilInfo.isHeavy || (slope !== null && slope < 1.0) || (ndwiWaterPct !== null && ndwiWaterPct >= 5))) {
    const reasons = [];
    if (awc !== null && awc > 90) reasons.push(`wysokie wysycenie gleby wodą (woda użyteczna: ${Math.round(awc)}%)`);
    else if (surfaceMoist !== null && fieldCap !== null && surfaceMoist >= fieldCap + 3) {
      reasons.push(`wilgotność powierzchniowa (${surfaceMoist.toFixed(0)}%) przekracza pojemność polową (${fieldCap.toFixed(0)}%)`);
    }
    if (ndwiWaterPct !== null && ndwiWaterPct >= 5) reasons.push(`woda stojąca wg Sentinel-2 (NDWI) na ok. ${Math.round(ndwiWaterPct)}% działki`);
    else if (ndwi !== null && ndwi > 0.20) reasons.push(`podwyższony wskaźnik wody powierzchniowej NDWI: ${ndwi.toFixed(2).replace('.', ',')}`);
    if (slope !== null && slope < 1.0) reasons.push(`teren płaski (${slope.toFixed(1).replace('.', ',')}%)`);
    if (soilInfo.isHeavy && soilLabel) reasons.push(`gleba zwięzła (${soilLabel})`);
    recommendations.push({
      id: 'D-03',
      kategoria: 'Diagnoza wodna',
      priorytet: 'wysoki',
      tytul: 'Zagrożenie zastoiskami wodnymi i asfiksją korzeni',
      opis: 'W obniżeniach terenu oraz na glebach zwięzłych stwierdzono nadmierne wysycenie profilu wodą. Unikaj wjazdu ciężkim sprzętem, aby zapobiec niszczeniu struktury gruzełkowatej i głębokiemu zagęszczeniu gleby.',
      uzasadnienie: `${reasons.join('; ')}.`,
      zyskWodny: 'Ochrona przed asfiksją korzeni i zagęszczeniem podglebia, które trwale blokuje retencję.',
      ekoschemat: null,
      poziomNiepewnosci,
    });
  }

  // =========================================================================
  // ETAP 3: Rekomendacje Retencyjne – Krajobrazowe i Glebowe (NAJWYŻSZY PRIORYTET)
  // =========================================================================

  // Reguła R-LAND-01: Zastawki na rowach melioracyjnych (Sterowanie odpływem)
  if (nearDitch || (kbw !== null && kbw < -5)) {
    const landReasons = [];
    if (nearDitch) landReasons.push('wykryto sąsiedztwo rowu melioracyjnego lub cieku wodnego');
    else landReasons.push('zalecane sprawdzenie obecności rowów melioracyjnych w mikrozlewni działki');
    if (kbw !== null) landReasons.push(`ujemny bilans wodny KBW 14d (${kbw.toFixed(1).replace('.', ',')} mm)`);
    recommendations.push({
      id: 'R-LAND-01',
      kategoria: 'Retencja krajobrazowa',
      priorytet: isSevereDrought ? 'krytyczny' : 'wysoki',
      tytul: 'Sterowanie odpływem i piętrzenie wody w rowach melioracyjnych',
      opis: 'Zamknij lub przymknij zastawki na rowach melioracyjnych w sąsiedztwie działki (jeśli występują w zlewni). Utrzymanie poziomu wody na głębokości 40–60 cm pod powierzchnią wymusza podsiąk kapilarny do strefy korzeniowej.',
      uzasadnienie: `${landReasons.join('; ')}. Woda drenażowa powinna zasilać rośliny zamiast odpływać z mikrozlewni.`,
      zyskWodny: 'Podsiąk kapilarny zasilający rośliny ilością 20–40 mm wody w krytycznym okresie wegetacji.',
      ekoschemat: 'Ekoschemat: Retencjonowanie wody na trwałych użytkach zielonych (płatność ok. 280 zł/ha) oraz dotacje Wody Polskie / KOWR na małą retencję',
      poziomNiepewnosci,
    });
  }

  // Reguła R-LAND-02: Pasy buforowe i zadrzewienia śródpolne na stokach
  if (isSteep) {
    recommendations.push({
      id: 'R-LAND-02',
      kategoria: 'Retencja krajobrazowa',
      priorytet: 'wysoki',
      tytul: 'Zadarnione pasy buforowe i zadrzewienia śródpolne przechwytujące wodę',
      opis: 'Załóż pasy trawiaste lub zadrzewienia śródpolne (szer. 3–5 m) wzdłuż dolnej krawędzi działki w poprzek stoku. Pasy spowalniają spływ powierzchniowy i wymuszają infiltrację wody w głąb profilu.',
      uzasadnienie: `Spadek terenu ${slope.toFixed(1).replace('.', ',')}% stwarza ryzyko utraty wód opadowych. Zadrzewienia redukują parowanie eoliczne o ok. 25–30% w strefie osłonowej.`,
      zyskWodny: 'Przechwycenie 70–90% spływu powierzchniowego i zasilenie wód gruntowych.',
      ekoschemat: 'Ekoschemat: Obszary z roślinami miododajnymi / Pasy buforowe i miedze śródpolne',
      poziomNiepewnosci,
    });
  }

  // Reguła R-SOIL-01: Budowanie pojemności wodnej gleby (Próchnicowa gąbka wodna)
  if (isVulnerableSoil || groupId === 'high_transpiration_late' || groupId === 'spring_cereals_sensitive') {
    const soilBasis = isVulnerableSoil && soilLabel
      ? `Gleba o ograniczonej naturalnej retencji (${soilLabel})`
      : `Uprawa z grupy „${groupName}” wymaga wysokiej pojemności wodnej profilu glebowego${soilLabel ? ` (gleba: ${soilLabel})` : ''}`;
    recommendations.push({
      id: 'R-SOIL-01',
      kategoria: 'Retencja glebowa',
      priorytet: isSevereDrought ? 'krytyczny' : 'wysoki',
      tytul: 'Budowa „próchnicowej gąbki wodnej” – zwiększanie pojemności retencyjnej gleby',
      opis: 'Bezwzględnie pozostawiaj całą rozdrobnioną słomę i resztki pożniwne na polu. Stosuj obornik, kompost lub nawozy zielone. Każda dodatkowa tona materii organicznej trwale podnosi zdolność gleby do zatrzymywania wody opadowej.',
      uzasadnienie: `${soilBasis}. Zwiększenie próchnicy o 1% pozwala zatrzymać dodatkowo ok. 150–200 m³ wody na 1 ha (15–20 mm opadu).`,
      zyskWodny: '+15–20 mm zatrzymanej wody w profilu ornym na każdy 1% wzrostu próchnicy.',
      ekoschemat: 'Ekoschemat: Wymieszanie słomy z glebą / Przyoranie obornika (Rolnictwo węglowe)',
      poziomNiepewnosci,
    });
  }

  // Reguła R-SOIL-02: Międzyplony okrywowe i strukturotwórcze (Ochrona wilgoci)
  if (
    groupId === 'winter_cereals_early' ||
    groupId === 'spring_cereals_sensitive' ||
    groupId === 'deep_structured'
  ) {
    recommendations.push({
      id: 'R-SOIL-02',
      kategoria: 'Retencja glebowa',
      priorytet: 'wysoki',
      tytul: 'Wysiew głęboko korzeniących się międzyplonów ścierniskowych',
      opis: 'Zaraz po zbiorze uprawy głównej wysiej wielogatunkowy międzyplon (mieszanka rzodkwi oleistej, facelii, łubinu i wyki). Korzenie palowe rozbiją zagęszczone podglebie, a gęsta masa zielona osłoni glebę przed upałami.',
      uzasadnienie: `Uprawa z grupy ${groupName} zwalnia pole w środku lata. Pozostawienie odsłoniętego ścierniska powoduje utratę 1,5–3,0 mm wody dziennie na skutek parowania fizycznego.`,
      zyskWodny: 'Ograniczenie parowania glebowego o 50% i retencja zapasu wody pozimowej pod wymarzniętym mulczem.',
      ekoschemat: 'Ekoschemat: Międzyplony ozime / wsiewki śródplonowe (ok. 500 zł/ha)',
      poziomNiepewnosci,
    });
  }

  // =========================================================================
  // ETAP 4: Działania Agrotechniczne (Zorientowane na Ochronę Wody)
  // =========================================================================

  // Reguła AGRO-01: Uprawa konserwująca i bezorkowa (Ograniczenie parowania gleby)
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
      tytul: 'Przejście na uprawę bezorkową lub pasową (strip-till) z mulczem',
      opis: 'Zrezygnuj z tradycyjnej orki płużnej odwracającej skibę na rzecz uprawy pasowej (strip-till) lub spulchniania bezodwracowego z pozostawieniem warstwy ochronnego mulczu na powierzchni pola.',
      uzasadnienie: 'Tradycyjna orka wiosenna odwraca wilgotną glebę, powodując natychmiastowe odparowanie 15–25 mm wody w ciągu pierwszych 48 godzin. Mulcz chroni glebę przed nagrzewaniem przez słońce.',
      zyskWodny: 'Zaoszczędzenie 20–30 mm wody glebowej na starcie wegetacji i obniżenie temperatury gleby o 3–5°C.',
      ekoschemat: 'Ekoschemat: Uproszczone systemy uprawy (Rolnictwo węglowe)',
      poziomNiepewnosci,
    });
  }

  // Reguła AGRO-02: Uprawa w poprzek stoku (Konturowa retencja opadu)
  if (isSteep) {
    recommendations.push({
      id: 'AGRO-02',
      kategoria: 'Agrotechnika retencyjna',
      priorytet: 'wysoki',
      tytul: 'Uprawa konturowa – prowadzenie zabiegów ściśle w poprzek stoku',
      opis: 'Wykonuj wszelkie uprawki, siew i formowanie redlin wyłącznie wzdłuż poziomic terenu (poprzecznie do linii spadu). Każda bruzda zadziała jak miniaturowy polder zatrzymujący wodę opadową.',
      uzasadnienie: `Nachylenie terenu DEM: ${slope.toFixed(1).replace('.', ',')}%. Uprawa wzdłuż stoku tworzy korytarze przyspieszające spływ wody i wymywanie cząstek próchnicy.`,
      zyskWodny: 'Przekształcenie spływu powierzchniowego w infiltrację podsiąkową (zatrzymanie do 25 mm deszczu nawalnego).',
      ekoschemat: 'Wymóg wzajemnej zgodności GAEC 5 (zagrożenie erozją na stokach)',
      poziomNiepewnosci,
    });
  }

  // Reguła AGRO-03: Głęboszowanie podeszwy płużnej (Udostępnienie wody kapilarnej)
  if (
    soilInfo.isHeavy ||
    groupId === 'deep_structured' ||
    (plotData.has_plow_pan === true)
  ) {
    const agroReasons = [];
    if (soilInfo.isHeavy && soilLabel) agroReasons.push(`zwięzłe podglebie (${soilLabel})`);
    if (groupId === 'deep_structured') agroReasons.push(`uprawa o głębokim korzeniu (${groupName})`);
    if (plotData.has_plow_pan === true) agroReasons.push('stwierdzona podeszwa płużna');
    recommendations.push({
      id: 'AGRO-03',
      kategoria: 'Agrotechnika retencyjna',
      priorytet: 'sredni',
      tytul: 'Głęboszowanie podeszwy płużnej na głębokość 45–55 cm',
      opis: 'Wykonaj zabieg głęboszowania pod koniec lata przy suchym profilu glebowym. Pęknięcia w zagęszczonej podeszwie płużnej udrożnią podsiąk wody z głębszych warstw profilu oraz ułatwią korzeniom sięganie do wilgoci.',
      uzasadnienie: `${agroReasons.join(', ')}. Podeszwa płużna blokuje podsiąk wody kapilarnej w okresach letniej posuchy.`,
      zyskWodny: 'Udostępnienie roślinie rezerwuaru wody kapilarnej z głębokości poniżej 40 cm.',
      ekoschemat: null,
      poziomNiepewnosci,
    });
  }

  // =========================================================================
  // ETAP 5: Rekomendacje Nawadniania (Opcjonalne, Warunkowe i Precyzyjne)
  // =========================================================================

  // Reguła IRRIG-01: Mikronawadnianie kroplowe w warzywach i jagodnikach
  if (
    groupId === 'shallow_vegetables_tubers' ||
    groupId === 'perennial_orchard_berry'
  ) {
    const isWaterStress = (awc !== null && awc < 55) || (waterReserveDays !== null && waterReserveDays <= 4);
    recommendations.push({
      id: 'IRRIG-01',
      kategoria: 'Nawadnianie precyzyjne',
      priorytet: isWaterStress ? 'krytyczny' : 'sredni',
      tytul: 'Precyzyjne mikronawadnianie kroplowe podkoronowe / podściółkowe',
      opis: 'Zastosuj podpowierzchniowe lub podściółkowe linie kroplowe. Nawadniaj w cyklach nocnych lub wczesnoporannych (dawki 10–15 mm), aby wyeliminować straty wskutek bezpośredniego parowania kropel wody.',
      uzasadnienie: `Uprawa z grupy: ${groupName} charakteryzuje się płytkim systemem korzeniowym i skrajną wrażliwością na suszę. Klasyczne deszczownie tracą nawet 30–40% wody w powietrzu.`,
      zyskWodny: '90–95% sprawności aplikacji wody bezpośrednio do strefy korzeniowej roślin.',
      ekoschemat: 'Inwestycje w gospodarstwach w zakresie nawadniania (PROW / Plan Strategiczny WPR)',
      poziomNiepewnosci,
    });
  }

  // Reguła IRRIG-02: Ograniczenie lub brak uzasadnienia deszczowania zbóż
  if (
    groupId === 'winter_cereals_early' ||
    groupId === 'spring_cereals_sensitive'
  ) {
    recommendations.push({
      id: 'IRRIG-02',
      kategoria: 'Nawadnianie precyzyjne',
      priorytet: 'informacyjny',
      tytul: 'Brak uzasadnienia ekonomicznego dla sztucznego deszczowania zbóż',
      opis: 'Deszczowanie upraw zbóż polowych jest hydrologicznie i ekonomicznie nieuzasadnione (wysoki koszt energii i deficyt zasobów wodnych). Zastąp je retencją korytową w rowach melioracyjnych i mulczowaniem.',
      uzasadnienie: `Uprawa polowa o umiarkowanej marży jednostkowej (${groupName}). Efektywność retencji glebowej i podsiąku kapilarnego wielokrotnie przewyższa opłacalność sztucznego deszczowania.`,
      zyskWodny: 'Ochrona zasobów wód podziemnych i uniknięcie wysokich nakładów energetycznych.',
      ekoschemat: null,
      poziomNiepewnosci,
    });
  }

  // =========================================================================
  // FALLBACK: Reguła BASE-01 (Zalecenie bazowe w oparciu o grupę retencyjną)
  // =========================================================================
  if (recommendations.length === 0) {
    recommendations.push({
      id: 'BASE-01',
      kategoria: 'Zalecenie bazowe',
      priorytet: 'sredni',
      tytul: `Podstawowe zarządzanie retencją wody dla uprawy: ${cropName || groupName}`,
      opis: `Dla uprawy z grupy „${groupName}” kluczowe jest utrzymanie wilgotności strefy korzeniowej poprzez ochronę gleby przed parowaniem i racjonalne zagospodarowanie wód opadowych.`,
      uzasadnienie: `Zalecenie ogólne wynikające ze specyfiki fizjologicznej grupy retencyjnej uprawy (${retentionGroup.retention_role || 'retencja glebowa i struktura profilu'}).`,
      zyskWodny: 'Stabilizacja bilansu wilgotnościowego wierzchniej warstwy gleby.',
      ekoschemat: null,
      poziomNiepewnosci: 'podwyższony',
    });
  }

  return recommendations;
}

/**
 * Generuje podsumowanie retencyjne dla całego zestawu działek gospodarstwa.
 *
 * @param {Array<Object>} parcels Lista przeanalizowanych działek
 * @returns {Object} Statystyki i agregacja zaleceń
 */
export function generateFarmRetentionSummary(parcels) {
  if (!Array.isArray(parcels) || parcels.length === 0) {
    return {
      totalParcels: 0,
      totalAreaHa: 0,
      priorityCounts: { krytyczny: 0, wysoki: 0, sredni: 0, informacyjny: 0 },
      overallStatus: 'Brak wybranych działek',
      statusClass: 'plain',
      topAction: 'Wybierz działki w Kroku 1, aby opracować plan retencji.',
      parcelsWithRecs: [],
    };
  }

  let totalArea = 0;
  const priorityCounts = { krytyczny: 0, wysoki: 0, sredni: 0, informacyjny: 0 };
  const parcelsWithRecs = [];

  parcels.forEach((p, idx) => {
    const area = toNum(p.area_ha) || 0;
    totalArea += area;

    const recs = generateRecommendations(p);
    recs.forEach((r) => {
      const pKey = normalizePriority(r.priorytet);
      if (priorityCounts[pKey] !== undefined) {
        priorityCounts[pKey]++;
      }
    });

    parcelsWithRecs.push({
      parcel: p,
      index: idx,
      recommendations: recs,
    });
  });

  let overallStatus = 'Stabilna sytuacja wilgotnościowa';
  let statusClass = 'ok';
  let topAction = 'Kontynuuj działania glebowe zwiększające zawartość próchnicy.';

  if (priorityCounts.krytyczny > 0) {
    overallStatus = 'Wysokie zagrożenie suszą / deficytem wody';
    statusClass = 'dry';
    topAction = 'Wstrzymaj zabiegi przesuszające glebę i zamknij zastawki na rowach melioracyjnych.';
  } else if (priorityCounts.wysoki > 0) {
    overallStatus = 'Wymagane pilne działania retencyjne';
    statusClass = 'low';
    topAction = 'Wdrożenie uprawy bezorkowej z mulczem i ochrona wody na stokach.';
  }

  return {
    totalParcels: parcels.length,
    totalAreaHa: Math.round(totalArea * 100) / 100,
    priorityCounts,
    overallStatus,
    statusClass,
    topAction,
    parcelsWithRecs,
  };
}

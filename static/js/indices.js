// Proste wyjaśnienia wskaźników satelitarnych (Sentinel-2): co mierzą, z grubsza jak i jak je czytać.
// Jedno źródło tekstów dla mapy (legenda), Głównej (karta działki) i Historii.
import { h, icon, disclosure } from './dom.js';

export const INDEX_HELP = {
  retention: {
    name: 'Retencja wody',
    full: 'zapas wody w glebie w strefie korzeniowej (0–28 cm, wg FAO-56)',
    question: 'Jak czytać retencję wody?',
    what: 'ile wody gleba zatrzymuje w strefie korzeni (7–28 cm) oraz na ile dni wystarczy zapasu wody łatwo dostępnej przed początkiem stresu wodnego.',
    how: 'wilgotność objętościową gleby z modelu DWD ICON / ERA5-Land odnosimy do pojemności polowej (PP) i punktu trwałego więdnięcia (PTW) dla rodzaju gleby na działce oraz prognozy opadów i parowania (ET₀).',
    read: 'niebieski – nadmiar wody (powyżej PP, ryzyko podtopień i ugniatania gleby); zielony – optymalna retencja (50–100% wody użytecznej); żółty – niedobór (poniżej 50%, początek stresu); brązowy – woda niedostępna dla roślin (poniżej PTW).',
    formula: 'Woda użyteczna = (θ − θPTW) / (θPP − θPTW); zapas w dniach liczony bilansem wodnym FAO-56 (warstwa 0–28 cm).',
    source: 'Dane: DWD ICON / ERA5-Land (Open-Meteo, CC BY 4.0) oraz progi uziarnienia gleby wg FAO-56.',
  },
  ndvi: {
    name: 'NDVI',
    full: 'znormalizowany różnicowy wskaźnik wegetacji',
    what: 'ile na polu jest zielonej, żywej masy roślin – czyli jak gęsty i zdrowy jest łan.',
    how: 'zdrowe liście zużywają światło czerwone do fotosyntezy (pochłaniają je), a mocno odbijają bliską podczerwień, '
      + 'której oko nie widzi. Satelita mierzy oba rodzaje światła; im większa różnica między nimi, tym więcej zieleni.',
    read: 'skala od −1 do 1. Poniżej 0,25 – goła gleba, ściernisko lub woda; 0,25–0,5 – rośliny rzadkie albo osłabione; '
      + 'powyżej 0,5 – dobry łan; powyżej 0,8 – bardzo gęsty. Zimą i po zbiorach wartości są naturalnie niskie.',
    formula: 'NDVI = (NIR − czerwień) / (NIR + czerwień); kanały Sentinel-2: B8 i B4.',
  },
  ndmi: {
    name: 'NDMI',
    full: 'znormalizowany różnicowy wskaźnik wilgotności',
    what: 'ile wody jest w liściach roślin – czy rośliny cierpią z powodu suszy (stres wodny).',
    how: 'woda w liściach pochłania światło krótkofalowej podczerwieni (SWIR), a bliską podczerwień liście odbijają niezależnie '
      + 'od tego, ile mają wody. Im mniej odbitego SWIR w porównaniu z bliską podczerwienią, tym więcej wody w roślinach.',
    read: 'poniżej 0 – rośliny mają za mało wody albo pole jest odkryte; 0–0,2 – umiarkowanie; powyżej 0,2 – rośliny dobrze '
      + 'uwodnione. To nie jest wilgotność gleby w % – NDMI widzi wodę w roślinach i na samej powierzchni.',
    formula: 'NDMI = (NIR − SWIR) / (NIR + SWIR); kanały Sentinel-2: B8A (Historia: B8) i B11.',
  },
  ndwi: {
    name: 'NDWI',
    full: 'znormalizowany różnicowy wskaźnik wody',
    what: 'wodę stojącą na powierzchni pola – zastoiska, podtopienia, rozlewiska po ulewie lub roztopach.',
    how: 'woda odbija trochę światła zielonego, ale prawie całkowicie pochłania bliską podczerwień. Rośliny i gleba robią odwrotnie '
      + '– podczerwień odbijają mocno. Dlatego NDWI jest dodatni tylko tam, gdzie na polu stoi woda.',
    read: 'powyżej 0,2 – woda stojąca; 0–0,2 – podmokło, możliwe zastoiska; poniżej 0 – brak wody na powierzchni (normalny stan pola). '
      + 'NDWI nie mierzy wilgotności gleby pod powierzchnią ani wody w roślinach (do tego służy NDMI).',
    formula: 'NDWI = (zieleń − NIR) / (zieleń + NIR) (McFeeters, 1996); kanały Sentinel-2: B3 i B8.',
  },
  evi: {
    name: 'EVI',
    full: 'ulepszony wskaźnik wegetacji',
    what: 'zieleń roślin, podobnie jak NDVI, ale lepiej rozróżnia bardzo gęste łany.',
    how: 'oprócz światła czerwonego i bliskiej podczerwieni używa też niebieskiego, którym poprawia wpływ mgiełki w atmosferze '
      + 'i koloru gleby. Dzięki temu nie „nasyca się” przy gęstym łanie, gdy NDVI stoi już blisko 0,9.',
    read: 'zwykle od 0 do 1: poniżej 0,2 – gleba bez roślin; 0,2–0,5 – rośliny rzadkie lub umiarkowane; powyżej 0,5 – dobry łan.',
    formula: 'EVI = 2,5 · (NIR − czerwień) / (NIR + 6·czerwień − 7,5·niebieski + 1); kanały Sentinel-2: B8, B4, B2.',
  },
  photo: {
    name: 'Zdjęcie z satelity',
    full: 'obraz Sentinel-2 w barwach naturalnych',
    question: 'Co pokazuje zdjęcie?',
    what: 'pole tak, jak widać je z góry: zielone rośliny, brązową glebę, ściernisko, wodę, ślady kół i rozlewiska.',
    how: 'satelita robi zdjęcie w świetle czerwonym, zielonym i niebieskim; składamy je w zwykły kolorowy obraz. '
      + 'Bez wskaźników i bez liczenia – to, co widzi oko.',
    read: 'przydaje się, gdy wskaźnik pokazuje coś dziwnego: na zdjęciu łatwo sprawdzić, czy to chmura, cień, woda czy faktycznie słabsze rośliny. '
      + 'Szczegół: piksel 10 × 10 m, więc pojedynczych roślin nie widać.',
    formula: 'kanały Sentinel-2: B4 (czerwień), B3 (zieleń), B2 (niebieski); w Historii – mediana bezchmurnych zdjęć z miesiąca.',
  },
  ortho: {
    name: 'Ortofotomapa',
    full: 'zdjęcia lotnicze z państwowego zasobu GUGiK (Geoportal)',
    question: 'Co to jest ortofotomapa?',
    what: 'okolicę działek z samolotu, bardzo dokładnie (piksel 5–25 cm): drogi, rowy, budynki, granice upraw.',
    how: 'zdjęcia lotnicze wyprostowane i dopasowane do mapy. Każdy rejon jest fotografowany co kilka lat, więc obraz nie pokazuje stanu z dziś.',
    read: 'do orientacji w terenie i sprawdzenia granic działek. Aktualny stan roślin pokazują wskaźniki z satelity (NDVI, NDMI, NDWI), '
      + 'a zdjęcia z satelity miesiąc po miesiącu – zakładka Historia, przycisk „Samo zdjęcie”.',
    formula: 'usługa WMS GUGiK „ORTO StandardResolution” – najnowsza dostępna ortofotomapa dla danego miejsca.',
    source: 'Dane: Główny Urząd Geodezji i Kartografii (geoportal.gov.pl), państwowy zasób geodezyjny i kartograficzny.',
  },
  bare: {
    name: 'gleba odsłonięta',
    full: 'udział gleby bez roślin',
    what: 'jaka część działki nie jest pokryta roślinami – np. po orce, siewie, zbiorze albo gdzie rośliny wypadły.',
    how: 'piksel zdjęcia (10 × 10 m) liczymy jako glebę odsłoniętą, gdy jego NDVI jest niższe niż 0,25.',
    read: 'procent powierzchni działki. Wysoki jesienią i wiosną po uprawkach jest normalny; w środku sezonu może oznaczać wypadanie roślin.',
    formula: 'udział pikseli z NDVI < 0,25.',
  },
};

const SOURCE = 'Dane: zdjęcia satelity Sentinel-2 (program Copernicus, UE). Piksel 10 m (SWIR: 20 m), nowe zdjęcie co 2–5 dni, '
  + 'o ile nad polem nie ma chmur.';

function rows(key) {
  const x = INDEX_HELP[key];
  if (!x) return [];
  return [
    h('span', { class: 'mr-idxhelp__row' }, [h('strong', { text: 'Co mierzy: ' }), x.what]),
    h('span', { class: 'mr-idxhelp__row' }, [h('strong', { text: 'Jak: ' }), x.how]),
    h('span', { class: 'mr-idxhelp__row' }, [h('strong', { text: 'Jak czytać: ' }), x.read]),
    h('span', { class: 'mr-idxhelp__row mr-idxhelp__formula', text: x.formula }),
    h('span', { class: 'mr-idxhelp__row mr-idxhelp__src', text: x.source || SOURCE }),
  ];
}

/** Rozwijane „Co to jest NDVI?” (np. pod wyborem wskaźnika, w legendzie mapy). */
export function indexHelp(key, opts = {}) {
  const x = INDEX_HELP[key];
  if (!x) return null;
  return h('details', { class: `mr-idxhelp${opts.compact ? ' mr-idxhelp--compact' : ''}`, open: !!opts.open }, [
    h('summary', { class: 'mr-idxhelp__sum' }, [icon('info'), x.question || `Co to jest ${x.name}?`]),
    h('span', { class: 'mr-idxhelp__body' }, [
      h('span', { class: 'mr-idxhelp__row mr-idxhelp__full', text: `${x.name} – ${x.full}.` }),
      ...rows(key),
    ]),
  ]);
}

/** Ikona „i” przy nazwie wskaźnika – to samo wyjaśnienie w dymku. */
export function indexTip(key) {
  const x = INDEX_HELP[key];
  if (!x) return null;
  return disclosure(icon('info'), h('span', { class: 'mr-idxhelp__body' }, [
    h('span', { class: 'mr-idxhelp__row mr-idxhelp__full', text: `${x.name} – ${x.full}.` }), ...rows(key)]),
  { label: `Wyjaśnienie: ${x.name}`, buttonClass: 'mr-infobtn' });
}

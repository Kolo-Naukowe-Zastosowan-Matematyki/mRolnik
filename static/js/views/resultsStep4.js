// KROK 4: Prezentacja i eksport rekomendacji dotyczących retencji wody
// Zgodnie z wytycznymi KOWR: „Zatrzymaj wodę, chroń plony”
// oraz logiką opisaną w logika_rekomendacji.md.
//
// Struktura widoku:
// 1. Główna rekomendacja zbiorcza (dla całego gospodarstwa / wszystkich działek)
// 2. Sekcja „Szczegóły” (dla pojedynczych działek)
// 3. Eksport do PDF z zachowaniem tej samej hierarchii i polskimi znakami.

import { h, icon, replace } from '../dom.js';
import { state } from '../state.js';
import { generateRetentionReport } from '../recommendationEngine.js';

/**
 * Formatowanie liczb po polsku
 */
function fmtNum(n, decimals = 1) {
  if (n === null || n === undefined || !Number.isFinite(Number(n))) return '–';
  return Number(n).toFixed(decimals).replace('.', ',');
}

/**
 * Zwraca aktualną datę i godzinę w formacie polskim
 */
function getReportDateString() {
  const now = new Date();
  const day = String(now.getDate()).padStart(2, '0');
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const year = now.getFullYear();
  const hours = String(now.getHours()).padStart(2, '0');
  const mins = String(now.getMinutes()).padStart(2, '0');
  return `${day}.${month}.${year} r., godz. ${hours}:${mins}`;
}

/**
 * Obsługa eksportu i wydruku raportu do PDF
 */
export function exportRecommendationsPDF() {
  const dateEl = document.querySelector('.mr-print-report-date');
  if (dateEl) {
    dateEl.textContent = getReportDateString();
  }
  // Wywołanie natywnego wydruku przeglądarki (Drukuj / Zapisz jako PDF)
  // Gwarantuje 100% wsparcie polskich znaków diakrytycznych bez zniekształceń
  window.print();
}

/**
 * Buduje kafelki parametrów glebowo-wodnych dla pojedynczej działki
 */
function renderParcelParamTiles(params) {
  const tiles = [
    {
      label: 'Gleba i retencja',
      val: params.glebaKat,
      sub: params.glebaTekst,
    },
    {
      label: 'Spadek terenu (DEM)',
      val: params.spadekTekst,
      sub: params.spadekWartosc !== null && params.spadekWartosc >= 3 ? 'Woda spływa po stoku' : 'Teren płaski',
    },
    {
      label: 'Bilans KBW (14 dni)',
      val: params.kbwTekst,
      sub: 'Opad minus parowanie',
      isWarn: params.kbwWartosc !== null && params.kbwWartosc < 0,
    },
    {
      label: 'Zapas wody w glebie',
      val: params.zapasWodyTekst,
      sub: 'Dla strefy korzeniowej',
      isDanger: params.zapasWodyDni !== null && params.zapasWodyDni <= 3,
    },
    {
      label: 'Wilgotność satelitarna',
      val: `NDMI: ${params.ndmiTekst}`,
      sub: 'Sentinel-2 (stan roślin)',
    },
  ];

  return h('div', { class: 'mr-pmetrics-grid' }, tiles.map((t) => {
    let valCls = 'mr-pmetric__val';
    if (t.isDanger) valCls += ' mr-text--danger';
    else if (t.isWarn) valCls += ' mr-text--warn';

    return h('div', { class: 'mr-pmetric' }, [
      h('span', { class: 'mr-pmetric__label', text: t.label }),
      h('strong', { class: valCls, text: t.val }),
      h('span', { class: 'mr-pmetric__sub', text: t.sub }),
    ]);
  }));
}

/**
 * Buduje pojedynczą kartę rekomendacji dla działki
 */
function renderParcelRecCard(rec) {
  const pCls = `mr-rec-priority--${rec.priorytet || 'sredni'}`;
  const pLabel = {
    krytyczny: 'PILNE',
    wysoki: 'WAŻNE',
    sredni: 'ZALECANE',
    informacyjny: 'WSKAZÓWKA',
  }[rec.priorytet] || 'ZALECANE';

  const badgePriority = h('span', { class: `mr-badge mr-rec-badge ${pCls}`, text: pLabel });
  const badgeCategory = h('span', { class: 'mr-tag mr-tag--category', text: rec.kategoria || 'Retencja' });

  const header = h('div', { class: 'mr-rec-item__header' }, [
    h('div', { class: 'mr-rec-item__badges' }, [badgePriority, badgeCategory]),
  ]);

  const title = h('h5', { class: 'mr-rec-item__title', text: rec.tytul });
  const desc = h('p', { class: 'mr-rec-item__desc', text: rec.opis });

  const details = [];

  if (rec.uzasadnienie) {
    details.push(
      h('div', { class: 'mr-rec-detail mr-rec-detail--justification' }, [
        h('strong', { class: 'mr-rec-detail__label', text: 'Dlaczego: ' }),
        h('span', { class: 'mr-rec-detail__val', text: rec.uzasadnienie }),
      ])
    );
  }

  if (rec.zyskWodny) {
    details.push(
      h('div', { class: 'mr-rec-detail mr-rec-detail--gain' }, [
        icon('drop'),
        h('strong', { class: 'mr-rec-detail__label', text: ' Zysk wodny: ' }),
        h('span', { class: 'mr-rec-detail__val', text: rec.zyskWodny }),
      ])
    );
  }

  if (rec.ekoschemat) {
    details.push(
      h('div', { class: 'mr-rec-detail mr-rec-detail--subsidy' }, [
        icon('check'),
        h('strong', { class: 'mr-rec-detail__label', text: ' Możliwe wsparcie: ' }),
        h('span', { class: 'mr-rec-detail__val', text: rec.ekoschemat }),
      ])
    );
  }

  return h('div', { class: `mr-rec-item ${pCls}` }, [header, title, desc, ...details]);
}

/**
 * Renderuje Krok 4:
 * 1. Główna rekomendacja zbiorcza na samej górze
 * 2. Sekcja „Szczegóły” poniżej z rozbiciem na działki
 */
export function renderResultsStep4(container) {
  if (!container) return;

  const rawParcels = state.customParcels || [];
  const analysisParcels = (state.analysis && Array.isArray(state.analysis.parcels)) ? state.analysis.parcels : [];

  if (rawParcels.length === 0) {
    replace(container, [
      h('div', { class: 'mr-card mr-empty-card' }, [
        icon('info'),
        h('h3', { text: 'Brak wybranych działek' }),
        h('p', { class: 'mr-muted', text: 'Wróć do Kroku 1 i wybierz przynajmniej jedną działkę, aby przygotować rekomendacje retencyjne.' }),
      ]),
    ]);
    return;
  }

  // Połącz dane z formularza z wynikami analizy API
  const mergedParcels = rawParcels.map((p, idx) => {
    const analyzed = analysisParcels[idx] || {};
    return {
      ...p,
      ...analyzed,
      crop: p.crop || analyzed.crop || '',
      cropId: p.cropId || analyzed.cropId || null,
      area_ha: p.area_ha || analyzed.area_ha || 0,
      parcel_id: p.parcel_id || analyzed.parcel_id || '',
      id_tag: p.id_tag || analyzed.id_tag || `Działka ${idx + 1}`,
    };
  });

  // Generuj ustrukturyzowany raport
  const report = generateRetentionReport(mergedParcels);
  const mainRec = report.glownaRekomendacja;
  const details = report.szczegoly;

  // -------------------------------------------------------------------------
  // 1. GŁÓWNA REKOMENDACJA ZBIORCZA (Hero Card na górze ekranu)
  // -------------------------------------------------------------------------
  const heroCard = h('section', {
    class: 'mr-card mr-main-recommendation-hero',
    'aria-label': 'Główna rekomendacja zbiorcza dla gospodarstwa',
  }, [
    // Nagłówek raportu do wydruku PDF
    h('div', { class: 'mr-print-only-header' }, [
      h('h1', { class: 'mr-print-main-title', text: 'RAPORT GOSPODAROWANIA WODĄ I RETENCJI' }),
      h('p', { class: 'mr-print-sub-title', text: 'System mRolnik · KOWR: „Zatrzymaj wodę, chroń plony”' }),
      h('p', { class: 'mr-print-meta' }, [
        h('span', { text: 'Data i godzina wygenerowania: ' }),
        h('strong', { class: 'mr-print-report-date', text: getReportDateString() }),
      ]),
    ]),

    // Pasek tytułowy
    h('div', { class: 'mr-main-rec__header' }, [
      h('div', { class: 'mr-main-rec__title-group' }, [
        h('span', { class: 'mr-badge mr-badge--section-tag', text: 'GŁÓWNA REKOMENDACJA DLA GOSPODARSTWA' }),
        h('h2', { class: 'mr-main-rec__title', text: mainRec.tytul }),
        h('div', { class: 'mr-main-rec__meta-badges' }, [
          h('span', { class: 'mr-badge mr-badge--stat', text: `Działek: ${mainRec.liczbaDzialek}` }),
          h('span', { class: 'mr-badge mr-badge--stat', text: `Areał: ${mainRec.lacznaPowierzchniaHa}` }),
          h('span', { class: `mr-badge mr-badge--threat mr-badge--${mainRec.priorytet}`, text: mainRec.statusZagrozenia }),
        ]),
      ]),
      h('div', { class: 'mr-main-rec__actions' }, [
        h('button', {
          type: 'button',
          class: 'mr-btn mr-btn--primary mr-btn--pdf',
          id: 'mr-btn-print-pdf-hero',
          onclick: exportRecommendationsPDF,
        }, [
          icon('download'),
          h('span', { text: 'Pobierz raport PDF' }),
        ]),
      ]),
    ]),

    // Treść syntetyczna (jasne, proste zdania)
    h('div', { class: 'mr-main-rec__narrative' }, [
      h('h3', { class: 'mr-main-rec__subheading', text: 'Podsumowanie stanu wód na Twoich polach:' }),
      h('p', { class: 'mr-main-rec__lead-text', text: mainRec.syntetycznaTresc }),
    ]),

    // Kluczowe działania krok po kroku
    h('div', { class: 'mr-main-rec__action-plan' }, [
      h('h3', { class: 'mr-main-rec__subheading', text: 'Najważniejsze działania retencyjne – zrób to w pierwszej kolejności:' }),
      h('ol', { class: 'mr-main-rec__steps-list' }, mainRec.glowneZaleceniaKrokPoKroku.map((krok) => {
        return h('li', { class: 'mr-main-rec__step-item' }, [
          h('span', { class: 'mr-main-rec__step-text', text: krok }),
        ]);
      })),
    ]),

    // Pasek korzyści i wsparcia finansowego
    h('div', { class: 'mr-main-rec__benefits-grid' }, [
      h('div', { class: 'mr-benefit-box mr-benefit-box--gain' }, [
        icon('drop'),
        h('div', { class: 'mr-benefit-box__content' }, [
          h('strong', { class: 'mr-benefit-box__title', text: 'Oczekiwany zysk wodny:' }),
          h('p', { class: 'mr-benefit-box__desc', text: mainRec.zyskWodnyGospodarstwa }),
        ]),
      ]),
      h('div', { class: 'mr-benefit-box mr-benefit-box--subsidies' }, [
        icon('check'),
        h('div', { class: 'mr-benefit-box__content' }, [
          h('strong', { class: 'mr-benefit-box__title', text: 'Dostępne wsparcie finansowe (Ekoschematy WPR):' }),
          h('ul', { class: 'mr-benefit-box__list' }, mainRec.ekoschematyWPR.map((eko) => h('li', { text: eko }))),
        ]),
      ]),
    ]),
  ]);

  // -------------------------------------------------------------------------
  // 2. SEKCJA „SZCZEGÓŁY” (Dla pojedynczych działek)
  // -------------------------------------------------------------------------
  const detailsHeader = h('div', { class: 'mr-details-section-header', id: 'sekcja-szczegoly' }, [
    h('div', { class: 'mr-details-section-header__inner' }, [
      h('h3', { class: 'mr-details-section-title', text: 'Szczegóły' }),
      h('p', { class: 'mr-details-section-sub', text: `Dokładne parametry glebowo-wodne i konkretne zalecenia rozpisane dla każdej z ${details.liczbaDzialek} działek.` }),
    ]),
  ]);

  const parcelCards = details.dzialki.map((d) => {
    // Nagłówek działki
    const pHeader = h('div', { class: 'mr-pcard-header' }, [
      h('div', { class: 'mr-pcard-header__left' }, [
        h('div', { class: 'mr-pcard-header__title-row' }, [
          h('h4', { class: 'mr-pcard-title', text: d.tytul }),
          h('span', { class: 'mr-badge mr-badge--area', text: d.powierzchniaStr }),
          d.teryt ? h('span', { class: 'mr-badge mr-badge--teryt', text: d.teryt }) : null,
        ].filter(Boolean)),
        d.lokalizacjaStr ? h('p', { class: 'mr-pcard-meta', text: d.lokalizacjaStr }) : null,
      ]),
      h('div', { class: 'mr-pcard-header__right' }, [
        h('div', { class: 'mr-crop-retention-pill' }, [
          h('span', { class: 'mr-crop-name', text: d.uprawa }),
          h('span', { class: `mr-badge mr-ret-badge ${d.grupaRetencyjnaBadge}`, text: d.grupaRetencyjnaKrotka }),
        ]),
      ]),
    ]);

    // Kafelki parametrów
    const paramTiles = renderParcelParamTiles(d.parametryWodne);

    // Lista rekomendacji dla tej działki
    const recsList = h('div', { class: 'mr-recs-list' }, [
      h('h5', { class: 'mr-recs-list__heading', text: `Zalecenia dla tej działki (${d.rekomendacje.length}):` }),
      ...d.rekomendacje.map(renderParcelRecCard),
    ]);

    return h('article', { class: 'mr-card mr-parcel-retention-card', 'aria-label': `Szczegóły dla działki ${d.tytul}` }, [
      pHeader,
      paramTiles,
      recsList,
    ]);
  });

  const detailsSection = h('section', { class: 'mr-details-section', 'aria-labelledby': 'sekcja-szczegoly' }, [
    detailsHeader,
    h('div', { class: 'mr-parcels-retention-section' }, parcelCards),
  ]);

  // Stopka raportu
  const reportFooter = h('footer', { class: 'mr-report-footer' }, [
    h('p', { class: 'mr-muted', text: 'Zasada naczelna KOWR: „Zatrzymaj wodę tam, gdzie spadła”. Podstawą jest budowa próchnicowej gąbki glebowej, mulczowanie oraz retencja w rowach melioracyjnych.' }),
    h('p', { class: 'mr-muted', text: 'Źródła danych: Satelita Sentinel-2 L2A (CDSE / AWS), IMGW-PIB (dane meteorologiczne), GUGiK (ULDK, Numeryczny Model Terenu DEM), ERA5-Land (wilgotność profilu glebowego).' }),
  ]);

  replace(container, [
    heroCard,
    detailsSection,
    reportFooter,
  ]);
}

// KROK 4: Prezentacja i eksport rekomendacji dotyczących retencji wody
// Zgodnie z wytycznymi KOWR: „Zatrzymaj wodę, chroń plony”
// oraz logiką opisaną w logika_rekomendacji.md.
//
// Widok prezentuje WYŁĄCZNIE rekomendacje i parametry związane z gospodarką wodną
// i retencją dla zebranych działek użytkownika.

import { h, icon, replace } from '../dom.js';
import { state } from '../state.js';
import { generateFarmRetentionSummary, generateRecommendations } from '../recommendationEngine.js';
import { getCropRetentionGroup, findCrop } from '../crops_retention.js';

/**
 * Formatowanie liczb po polsku
 */
function fmtNum(n, decimals = 1) {
  if (n === null || n === undefined || !Number.isFinite(Number(n))) return '–';
  return Number(n).toFixed(decimals).replace('.', ',');
}

/**
 * Zwraca datę i godzinę w czytelnym formacie polskim
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
 * Obsługa eksportu / wydruku raportu PDF
 */
export function exportRecommendationsPDF() {
  // Aktualizacja dynamicznej daty raportu przed otwarciem okna druku
  const dateEl = document.querySelector('.mr-print-report-date');
  if (dateEl) {
    dateEl.textContent = getReportDateString();
  }

  // Wywołanie natywnego wydruku przeglądarki (Drukuj do PDF)
  // Zapewnia 100% wsparcie polskich znaków diakrytycznych bez zniekształceń
  window.print();
}

/**
 * Buduje pojedynczą kartę rekomendacji
 */
function renderRecommendationItem(rec) {
  const pCls = `mr-rec-priority--${rec.priorytet || 'sredni'}`;
  const pLabel = {
    krytyczny: 'KRYTYCZNY',
    wysoki: 'WYSOKI',
    sredni: 'ŚREDNI',
    informacyjny: 'INFORMACYJNY',
  }[rec.priorytet] || 'ZALECANY';

  const badgePriority = h('span', { class: `mr-badge mr-rec-badge ${pCls}`, text: pLabel });
  const badgeCategory = h('span', { class: 'mr-tag mr-tag--category', text: rec.kategoria || 'Retencja' });
  const badgeId = rec.id ? h('span', { class: 'mr-badge mr-badge--rule-id', text: `[${rec.id}]` }) : null;

  const header = h('div', { class: 'mr-rec-item__header' }, [
    h('div', { class: 'mr-rec-item__badges' }, [badgePriority, badgeCategory, badgeId].filter(Boolean)),
  ]);

  const title = h('h4', { class: 'mr-rec-item__title', text: rec.tytul });
  const desc = h('p', { class: 'mr-rec-item__desc', text: rec.opis });

  // Pasek szczegółów uzasadnienia, zysku wodnego i ekoschematów
  const details = [];

  if (rec.uzasadnienie) {
    details.push(
      h('div', { class: 'mr-rec-detail mr-rec-detail--justification' }, [
        h('strong', { class: 'mr-rec-detail__label', text: 'Uzasadnienie danymi działki: ' }),
        h('span', { class: 'mr-rec-detail__val', text: rec.uzasadnienie }),
      ])
    );
  }

  if (rec.zyskWodny) {
    details.push(
      h('div', { class: 'mr-rec-detail mr-rec-detail--gain' }, [
        icon('drop'),
        h('strong', { class: 'mr-rec-detail__label', text: ' Oczekiwany zysk wodny: ' }),
        h('span', { class: 'mr-rec-detail__val', text: rec.zyskWodny }),
      ])
    );
  }

  if (rec.ekoschemat) {
    details.push(
      h('div', { class: 'mr-rec-detail mr-rec-detail--subsidy' }, [
        icon('check'),
        h('strong', { class: 'mr-rec-detail__label', text: ' Wsparcie / Finansowanie: ' }),
        h('span', { class: 'mr-rec-detail__val', text: rec.ekoschemat }),
      ])
    );
  }

  if (rec.poziomNiepewnosci) {
    details.push(
      h('div', { class: 'mr-rec-detail mr-rec-detail--uncertainty' }, [
        h('span', { class: 'mr-muted', text: `Poziom niepewności wyniku: ${rec.poziomNiepewnosci}` }),
      ])
    );
  }

  return h('div', { class: 'mr-rec-item' }, [header, title, desc, ...details]);
}

/**
 * Buduje kafelki kluczowych wskaźników decyzyjnych działki
 */
function renderParcelMetrics(plot) {
  const metrics = [];

  // 1. Kategoria gleby / podatność na suszę
  const soilVal = plot.soil || 'Gleba lekka/średnia';
  metrics.push(
    h('div', { class: 'mr-pmetric' }, [
      h('span', { class: 'mr-pmetric__label', text: 'Gleba i retencja' }),
      h('strong', { class: 'mr-pmetric__val', text: soilVal }),
      h('span', { class: 'mr-pmetric__sub', text: 'Podatność IUNG' }),
    ])
  );

  // 2. Nachylenie terenu (NMT DEM)
  const slopeVal = plot.slope !== undefined && plot.slope !== null ? `${fmtNum(plot.slope, 1)}%` : 'Spadek < 2%';
  metrics.push(
    h('div', { class: 'mr-pmetric' }, [
      h('span', { class: 'mr-pmetric__label', text: 'Spadek terenu (DEM)' }),
      h('strong', { class: 'mr-pmetric__val', text: slopeVal }),
      h('span', { class: 'mr-pmetric__sub', text: Number(plot.slope) >= 3 ? 'Ryzyko spływu wody' : 'Teren nizinny' }),
    ])
  );

  // 3. Klimatyczny Bilans Wodny (KBW)
  const kbwNum = plot.kbw !== undefined && plot.kbw !== null
    ? plot.kbw
    : (plot.weather_local && plot.weather_local.climatic_water_balance_14d_mm !== undefined
        ? plot.weather_local.climatic_water_balance_14d_mm
        : null);
  const kbwText = kbwNum !== null ? `${fmtNum(kbwNum, 1)} mm` : 'Deficyt';
  metrics.push(
    h('div', { class: 'mr-pmetric' }, [
      h('span', { class: 'mr-pmetric__label', text: 'Bilans KBW (14 dni)' }),
      h('strong', { class: `mr-pmetric__val${Number(kbwNum) < 0 ? ' mr-text--warn' : ''}`, text: kbwText }),
      h('span', { class: 'mr-pmetric__sub', text: 'Opad minus ewapotranspiracja' }),
    ])
  );

  // 4. Zapas wody dla roślin (dni)
  const days = plot.water_reserve_days;
  const daysText = days !== null && days !== undefined ? `${days} dni` : 'Brak danych';
  metrics.push(
    h('div', { class: 'mr-pmetric' }, [
      h('span', { class: 'mr-pmetric__label', text: 'Zapas wody w glebie' }),
      h('strong', { class: `mr-pmetric__val${Number(days) <= 3 ? ' mr-text--danger' : ''}`, text: daysText }),
      h('span', { class: 'mr-pmetric__sub', text: 'Do progu stresu wodnego' }),
    ])
  );

  // 5. Wskaźnik satelitarny wilgotności (NDMI / NDVI)
  const ndmi = plot.ndmi || (plot.layers && plot.layers.ndmi);
  const ndmiText = ndmi !== null && ndmi !== undefined ? fmtNum(ndmi, 2) : 'Bieżący';
  metrics.push(
    h('div', { class: 'mr-pmetric' }, [
      h('span', { class: 'mr-pmetric__label', text: 'Satelita Sentinel-2' }),
      h('strong', { class: 'mr-pmetric__val', text: `NDMI: ${ndmiText}` }),
      h('span', { class: 'mr-pmetric__sub', text: 'Wskaźnik uwodnienia roślin' }),
    ])
  );

  return h('div', { class: 'mr-pmetrics-grid' }, metrics);
}

/**
 * Główna funkcja renderująca Krok 4: Rekomendacje Retencji Wody
 *
 * @param {HTMLElement} container Element kontenera do wyrenderowania
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

  // Zbuduj połączoną listę działek z danymi wejściowymi i analitycznymi
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

  // Generuj podsumowanie retencyjne
  const summary = generateFarmRetentionSummary(mergedParcels);

  // 1. Karta nagłówka i podsumowania retencyjnego gospodarstwa
  const summaryCard = h('section', { class: 'mr-card mr-retention-summary-card', 'aria-label': 'Podsumowanie retencyjne gospodarstwa' }, [
    // Nagłówek raportu drukowanego (widoczny tylko na wydruku PDF)
    h('div', { class: 'mr-print-only-header' }, [
      h('h1', { class: 'mr-print-main-title', text: 'RAPORT GOSPODAROWANIA WODĄ I RETENCJI' }),
      h('p', { class: 'mr-print-sub-title', text: 'System mRolnik · KOWR: „Zatrzymaj wodę, chroń plony”' }),
      h('p', { class: 'mr-print-meta' }, [
        h('span', { text: 'Data i godzina wygenerowania: ' }),
        h('strong', { class: 'mr-print-report-date', text: getReportDateString() }),
      ]),
    ]),

    // Pasek tytułowy widoku w aplikacji
    h('div', { class: 'mr-summary-header' }, [
      h('div', { class: 'mr-summary-header__text' }, [
        h('h2', { class: 'mr-summary-header__title' }, [
          icon('drop'),
          'Plan Retencji i Ochrony Wody w Gospodarstwie',
        ]),
        h('p', { class: 'mr-summary-header__sub', text: 'Analiza oparta na danych Sentinel-2, IMGW-PIB, GUGiK ULDK oraz modelu hydrologicznym ERA5-Land.' }),
      ]),
      h('div', { class: 'mr-summary-header__actions' }, [
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

    // Pasek kluczowych statystyk gospodarstwa
    h('div', { class: 'mr-summary-stats' }, [
      h('div', { class: 'mr-stat-box' }, [
        h('span', { class: 'mr-stat-box__label', text: 'Liczba działek' }),
        h('strong', { class: 'mr-stat-box__val', text: `${summary.totalParcels}` }),
        h('span', { class: 'mr-stat-box__sub', text: `Łącznie: ${fmtNum(summary.totalAreaHa, 2)} ha` }),
      ]),
      h('div', { class: 'mr-stat-box' }, [
        h('span', { class: 'mr-stat-box__label', text: 'Status zagrożenia' }),
        h('strong', { class: `mr-stat-box__val mr-stat-box__val--${summary.statusClass}`, text: summary.overallStatus }),
        h('span', { class: 'mr-stat-box__sub', text: 'Ocena bilansu wodnego' }),
      ]),
      h('div', { class: 'mr-stat-box' }, [
        h('span', { class: 'mr-stat-box__label', text: 'Zalecenia retencyjne' }),
        h('div', { class: 'mr-stat-box__badges' }, [
          h('span', { class: 'mr-badge mr-badge--crit', text: `Krytyczne: ${summary.priorityCounts.krytyczny}` }),
          h('span', { class: 'mr-badge mr-badge--warn', text: `Wysokie: ${summary.priorityCounts.wysoki}` }),
          h('span', { class: 'mr-badge mr-badge--mid', text: `Średnie: ${summary.priorityCounts.sredni}` }),
        ]),
        h('span', { class: 'mr-stat-box__sub', text: 'Hierarchia działań KOWR' }),
      ]),
    ]),

    // Ramka z najważniejszym natychmiastowym działaniem
    h('div', { class: 'mr-top-action-callout' }, [
      icon('alert'),
      h('div', { class: 'mr-top-action-callout__content' }, [
        h('strong', { text: 'Kluczowe działanie retencyjne dla gospodarstwa: ' }),
        h('span', { text: summary.topAction }),
      ]),
    ]),
  ]);

  // 2. Karty poszczególnych działek z przypisanymi uprawami i rekomendacjami
  const parcelCards = summary.parcelsWithRecs.map(({ parcel, index, recommendations }) => {
    const pTitle = parcel.id_tag || (parcel.parcel_id ? `Dz. ${parcel.parcel_id.split('.').pop()}` : `Działka ${index + 1}`);
    const areaStr = parcel.area_ha ? `${fmtNum(parcel.area_ha, 2)} ha` : '';

    const cropObj = findCrop(parcel.cropId || parcel.crop);
    const retGroup = getCropRetentionGroup(parcel.cropId || parcel.crop) || {
      name: 'Standardowa uprawa polowa',
      short_label: 'Uprawa polowa',
      badge_class: 'mr-badge--ret-generic',
    };

    const locationParts = [parcel.commune ? `gm. ${parcel.commune}` : null, parcel.county ? `pow. ${parcel.county}` : null].filter(Boolean);

    // Nagłówek karty działki
    const pHeader = h('div', { class: 'mr-pcard-header' }, [
      h('div', { class: 'mr-pcard-header__left' }, [
        h('div', { class: 'mr-pcard-header__title-row' }, [
          h('h3', { class: 'mr-pcard-title', text: pTitle }),
          areaStr ? h('span', { class: 'mr-badge mr-badge--area', text: areaStr }) : null,
          parcel.parcel_id ? h('span', { class: 'mr-badge mr-badge--teryt', text: parcel.parcel_id }) : null,
        ].filter(Boolean)),
        locationParts.length ? h('p', { class: 'mr-pcard-meta', text: locationParts.join(' · ') }) : null,
      ]),
      h('div', { class: 'mr-pcard-header__right' }, [
        h('div', { class: 'mr-crop-retention-pill' }, [
          h('span', { class: 'mr-crop-name', text: parcel.crop || 'Uprawa nieokreślona' }),
          h('span', { class: `mr-badge mr-ret-badge ${retGroup.badge_class || ''}`, text: retGroup.short_label || retGroup.name }),
        ]),
      ]),
    ]);

    // Kafelki wskaźników fizjograficzno-wodnych
    const metricsBlock = renderParcelMetrics(parcel);

    // Lista wygenerowanych rekomendacji retencyjnych
    const recsList = h('div', { class: 'mr-recs-list' }, [
      h('h4', { class: 'mr-recs-list__heading', text: `Dedykowane działania retencyjne (${recommendations.length}):` }),
      ...recommendations.map(renderRecommendationItem),
    ]);

    return h('article', { class: 'mr-card mr-parcel-retention-card', 'aria-label': `Rekomendacje dla działki ${pTitle}` }, [
      pHeader,
      metricsBlock,
      recsList,
    ]);
  });

  // Złożenie całego widoku
  replace(container, [
    summaryCard,
    h('div', { class: 'mr-parcels-retention-section' }, parcelCards),
    // Stopka prawno-metodyczna raportu
    h('footer', { class: 'mr-report-footer' }, [
      h('p', { class: 'mr-muted', text: 'Zgodnie z zasadą „Zatrzymaj wodę tam, gdzie spadła” priorytetem jest budowa próchnicowej gąbki glebowej, mulczowanie oraz retencja w korytach rowów melioracyjnych. Sztuczne nawadnianie zalecane jest wyłącznie w sytuacjach skrajnych dla upraw wysokomarżowych.' }),
      h('p', { class: 'mr-muted', text: 'Źródła danych: Sentinel-2 L2A (CDSE / AWS), IMGW-PIB (dane meteorologiczne i synoptyczne), GUGiK (ULDK, Numeryczny Model Terenu DEM), ERA5-Land (wilgotność profilu glebowego).' }),
    ]),
  ]);
}

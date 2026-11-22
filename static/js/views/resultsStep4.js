// KROK 4: Prezentacja i eksport rekomendacji dotyczących retencji wody
// Zgodnie z wytycznymi KOWR: „Zatrzymaj wodę, chroń plony”
// oraz logiką opisaną w logika_rekomendacji.md.
//
// Widok prezentuje WYŁĄCZNIE rekomendacje i parametry związane z gospodarką wodną
// i retencją dla zebranych działek użytkownika – w postaci klikalnej listy działek,
// z których każda po kliknięciu rozwija pełne okno parametrów i zaleceń.

import { h, icon, replace, nextId } from '../dom.js';
import { state, emit, announce } from '../state.js';
import { highlight, focusParcel } from '../map.js';
import { generateFarmRetentionSummary, normalizePriority } from '../recommendationEngine.js';
import { getCropRetentionGroup } from '../crops_retention.js';

// Zbiór rozwiniętych działek (klucz: parcel_id lub indeks), zachowywany między odświeżeniami
const expandedParcelKeys = new Set();

function parcelKey(parcel, index) {
  return parcel && parcel.parcel_id ? `pid:${parcel.parcel_id}` : `idx:${index}`;
}

function toNum(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === 'string' && v.trim() === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

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
 * Odmiana słowa „zalecenie”.
 */
function plZalen(n) {
  if (n === 1) return 'zalecenie';
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return 'zalecenia';
  return 'zaleceń';
}

/**
 * Formatowanie liczb po polsku
 */
function fmtNum(n, decimals = 1) {
  const num = toNum(n);
  if (num === null) return '–';
  return num.toFixed(decimals).replace('.', ',');
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
  const dateEl = document.querySelector('.mr-print-report-date');
  if (dateEl) {
    dateEl.textContent = getReportDateString();
  }
  // Natywny wydruk przeglądarki (Drukuj do PDF) – w @media print wszystkie okna działek są rozwinięte
  window.print();
}

/**
 * Rozwija i przewija do okna konkretnej działki w Kroku 4 (np. po kliknięciu działki na mapie).
 */
export function expandParcelInStep4(index) {
  const container = state.root && state.root.querySelector('[data-ref="results-water-container"]');
  if (!container) return;
  const item = container.querySelector(`.mr-parcel-retention-card[data-parcel-index="${index}"]`);
  if (!item) return;

  const key = item.dataset.parcelKey;
  const btn = item.querySelector('.mr-pcard-toggle');
  const body = item.querySelector('.mr-pcard-body');
  if (key) expandedParcelKeys.add(key);
  if (btn) btn.setAttribute('aria-expanded', 'true');
  if (body) body.hidden = false;
  item.classList.add('mr-parcel-retention-card--open');

  // Zaktualizuj etykietę przycisku „Rozwiń/Zwiń wszystkie”
  syncToggleAllButton(container);

  item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function syncToggleAllButton(container) {
  const toggleAllBtn = container.querySelector('[data-ref="step4-toggle-all"]');
  if (!toggleAllBtn) return;
  const items = Array.from(container.querySelectorAll('.mr-parcel-retention-card'));
  const allOpen = items.length > 0 && items.every((el) => el.classList.contains('mr-parcel-retention-card--open'));
  toggleAllBtn.textContent = allOpen ? 'Zwiń wszystkie' : 'Rozwiń wszystkie';
  toggleAllBtn.setAttribute('aria-pressed', String(allOpen));
}

/**
 * Buduje pojedynczą kartę rekomendacji
 */
function renderRecommendationItem(rec) {
  const priority = normalizePriority(rec.priorytet);
  const pCls = `mr-rec-priority--${priority}`;
  const pLabel = {
    krytyczny: 'KRYTYCZNY',
    wysoki: 'WYSOKI',
    sredni: 'ŚREDNI',
    informacyjny: 'INFORMACYJNY',
  }[priority] || 'ZALECANY';

  const badgePriority = h('span', { class: `mr-badge mr-rec-badge ${pCls}`, text: pLabel });
  const badgeCategory = h('span', { class: 'mr-tag mr-tag--category', text: rec.kategoria || 'Retencja' });
  const badgeId = rec.id ? h('span', { class: 'mr-badge mr-badge--rule-id', text: `[${rec.id}]` }) : null;

  const header = h('div', { class: 'mr-rec-item__header' }, [
    h('div', { class: 'mr-rec-item__badges' }, [badgePriority, badgeCategory, badgeId].filter(Boolean)),
  ]);

  const title = h('h4', { class: 'mr-rec-item__title', text: rec.tytul });
  const desc = h('p', { class: 'mr-rec-item__desc', text: rec.opis });

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
        h('strong', { class: 'mr-rec-detail__label', text: 'Oczekiwany zysk wodny: ' }),
        h('span', { class: 'mr-rec-detail__val', text: rec.zyskWodny }),
      ])
    );
  }

  if (rec.ekoschemat) {
    details.push(
      h('div', { class: 'mr-rec-detail mr-rec-detail--subsidy' }, [
        icon('check'),
        h('strong', { class: 'mr-rec-detail__label', text: 'Wsparcie / Finansowanie: ' }),
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

  return h('div', { class: `mr-rec-item ${pCls}` }, [header, title, desc, ...details]);
}

/**
 * Buduje kafelki kluczowych wskaźników decyzyjnych działki (bez zmyślania brakujących danych)
 */
function renderParcelMetrics(plot) {
  const metrics = [];
  const sm = plot.soil_moisture || {};

  // 1. Kategoria gleby / podatność na suszę
  let soilVal = 'Brak danych';
  let soilSub = 'Nie określono kategorii gleby';
  if (plot.soil) {
    soilVal = plot.soil;
    soilSub = 'Kategoria gleby działki';
  } else if (sm.soil_class) {
    soilVal = sm.soil_class;
    soilSub = 'Założenie modelu (brak danych IUNG)';
  }
  metrics.push(
    h('div', { class: 'mr-pmetric' }, [
      h('span', { class: 'mr-pmetric__label', text: 'Gleba i retencja' }),
      h('strong', { class: 'mr-pmetric__val', text: soilVal }),
      h('span', { class: 'mr-pmetric__sub', text: soilSub }),
    ])
  );

  // 2. Nachylenie terenu (NMT DEM)
  const slopeNum = toNum(plot.slope) ?? toNum(plot.slope_pct) ?? toNum(plot.dem_slope);
  const slopeVal = slopeNum !== null ? `${fmtNum(slopeNum, 1)}%` : 'Brak danych';
  const slopeSub = slopeNum === null
    ? 'Brak odczytu NMT (DEM)'
    : (slopeNum >= 3 ? 'Ryzyko spływu powierzchniowego' : 'Teren łagodny / nizinny');
  metrics.push(
    h('div', { class: 'mr-pmetric' }, [
      h('span', { class: 'mr-pmetric__label', text: 'Spadek terenu (DEM)' }),
      h('strong', { class: `mr-pmetric__val${slopeNum !== null && slopeNum >= 3 ? ' mr-text--warn' : ''}`, text: slopeVal }),
      h('span', { class: 'mr-pmetric__sub', text: slopeSub }),
    ])
  );

  // 3. Klimatyczny Bilans Wodny (KBW)
  const kbwNum = toNum(plot.kbw) ?? (plot.weather_local ? toNum(plot.weather_local.climatic_water_balance_14d_mm) : null);
  const kbwText = kbwNum !== null ? `${kbwNum > 0 ? '+' : ''}${fmtNum(kbwNum, 1)} mm` : 'Brak danych';
  const kbwCls = kbwNum === null ? '' : (kbwNum < -20 ? ' mr-text--danger' : (kbwNum < 0 ? ' mr-text--warn' : ''));
  metrics.push(
    h('div', { class: 'mr-pmetric' }, [
      h('span', { class: 'mr-pmetric__label', text: 'Bilans KBW (14 dni)' }),
      h('strong', { class: `mr-pmetric__val${kbwCls}`, text: kbwText }),
      h('span', { class: 'mr-pmetric__sub', text: kbwNum !== null ? 'Opad minus ewapotranspiracja' : 'Brak danych meteorologicznych' }),
    ])
  );

  // 4. Zapas wody dla roślin (dni)
  const daysNum = toNum(plot.water_reserve_days);
  const awpNum = toNum(sm.available_water_pct);
  const daysText = daysNum !== null ? `${daysNum} dni` : 'Brak danych';
  const daysCls = daysNum === null ? '' : (daysNum <= 3 ? ' mr-text--danger' : (daysNum <= 7 ? ' mr-text--warn' : ''));
  const daysSub = daysNum !== null
    ? (awpNum !== null ? `Woda użyteczna: ${fmtNum(awpNum, 0)}%` : 'Do progu stresu wodnego')
    : 'Brak danych wilgotności gleby';
  metrics.push(
    h('div', { class: 'mr-pmetric' }, [
      h('span', { class: 'mr-pmetric__label', text: 'Zapas wody w glebie' }),
      h('strong', { class: `mr-pmetric__val${daysCls}`, text: daysText }),
      h('span', { class: 'mr-pmetric__sub', text: daysSub }),
    ])
  );

  // 5. Wskaźnik satelitarny wilgotności (NDMI / NDVI)
  const layers = plot.layers || {};
  const ndmiNum = extractIndexMean(plot.ndmi, layers.ndmi);
  const ndviNum = extractIndexMean(plot.ndvi, layers.ndvi);
  let satVal = 'Brak danych';
  let satSub = 'Brak aktualnego zobrazowania';
  if (ndmiNum !== null) {
    satVal = `NDMI: ${fmtNum(ndmiNum, 2)}`;
    satSub = ndviNum !== null ? `NDVI: ${fmtNum(ndviNum, 2)} · uwodnienie roślin` : 'Wskaźnik uwodnienia roślin';
  } else if (ndviNum !== null) {
    satVal = `NDVI: ${fmtNum(ndviNum, 2)}`;
    satSub = 'NDMI: brak danych';
  }
  metrics.push(
    h('div', { class: 'mr-pmetric' }, [
      h('span', { class: 'mr-pmetric__label', text: 'Satelita Sentinel-2' }),
      h('strong', { class: 'mr-pmetric__val', text: satVal }),
      h('span', { class: 'mr-pmetric__sub', text: satSub }),
    ])
  );

  return h('div', { class: 'mr-pmetrics-grid' }, metrics);
}

/**
 * Łączy dane wejściowe działek użytkownika z wynikami z /api/analyze bez nadpisywania wartości nullem.
 */
function mergeParcelData(rawParcels, analysisParcels) {
  const byPid = new Map();
  analysisParcels.forEach((ap) => {
    if (ap && ap.parcel_id) byPid.set(ap.parcel_id, ap);
  });

  return rawParcels.map((p, idx) => {
    const analyzed = (p.parcel_id && byPid.get(p.parcel_id)) || analysisParcels[idx] || {};
    return {
      ...p,
      ...analyzed,
      crop: p.crop || analyzed.crop || '',
      cropId: p.cropId || analyzed.cropId || null,
      soil: p.soil || analyzed.soil || null,
      slope: analyzed.slope ?? p.slope ?? p.slope_pct ?? p.dem_slope ?? null,
      area_ha: analyzed.area_ha ?? p.area_ha ?? 0,
      commune: analyzed.commune || p.commune || null,
      county: analyzed.county || p.county || null,
      voivodeship: analyzed.voivodeship || p.voivodeship || null,
      parcel_id: p.parcel_id || analyzed.parcel_id || '',
      id_tag: p.id_tag || analyzed.id_tag || `Działka ${idx + 1}`,
    };
  });
}

/**
 * Zlicza priorytety zaleceń dla pojedynczej działki (do podglądu na zwiniętym wierszu listy).
 */
function countParcelPriorities(recommendations) {
  const counts = { krytyczny: 0, wysoki: 0, sredni: 0, informacyjny: 0 };
  recommendations.forEach((r) => {
    const k = normalizePriority(r.priorytet);
    if (counts[k] !== undefined) counts[k]++;
  });
  return counts;
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
    expandedParcelKeys.clear();
    replace(container, [
      h('div', { class: 'mr-card mr-empty-card' }, [
        icon('info'),
        h('h3', { text: 'Brak wybranych działek' }),
        h('p', { class: 'mr-muted', text: 'Wróć do Kroku 1 i wybierz przynajmniej jedną działkę, aby przygotować rekomendacje retencyjne.' }),
      ]),
    ]);
    return;
  }

  const mergedParcels = mergeParcelData(rawParcels, analysisParcels);

  // Usuń nieaktualne klucze ze zbioru rozwiniętych działek
  const activeKeys = new Set(mergedParcels.map((p, idx) => parcelKey(p, idx)));
  Array.from(expandedParcelKeys).forEach((k) => {
    if (!activeKeys.has(k)) expandedParcelKeys.delete(k);
  });

  const summary = generateFarmRetentionSummary(mergedParcels);

  // Komunikat błędu analizy (nieblokujący – zalecenia bazowe dla upraw nadal widoczne)
  const errorBanner = state.analysisError
    ? h('div', { class: 'mr-rec-error-callout', role: 'alert' }, [
        icon('warn'),
        h('div', { class: 'mr-rec-error-callout__text' }, [
          h('strong', { text: 'Nie udało się pobrać części danych pomiarowych: ' }),
          h('span', { text: state.analysisError.message || 'Usługi zewnętrzne nie odpowiedziały. Rekomendacje wygenerowano z podwyższoną niepewnością na podstawie danych o uprawach.' }),
        ]),
        h('button', {
          type: 'button',
          class: 'mr-btn mr-btn--secondary mr-btn--small',
          onclick: () => emit('analysis:retry'),
        }, [icon('refresh'), 'Spróbuj ponownie']),
      ])
    : null;

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
          'data-action': 'export-recommendations-pdf',
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
          summary.priorityCounts.informacyjny > 0
            ? h('span', { class: 'mr-badge mr-badge--info-count', text: `Info: ${summary.priorityCounts.informacyjny}` })
            : null,
        ].filter(Boolean)),
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

  // 2. Klikalna lista działek – każde kliknięcie rozwija/zwija pełne okno parametrów i rekomendacji
  const parcelItems = summary.parcelsWithRecs.map(({ parcel, index, recommendations }) => {
    const key = parcelKey(parcel, index);
    const isExpanded = expandedParcelKeys.has(key);
    const bodyId = nextId('mr-pcard-body');

    const pTitle = parcel.id_tag || (parcel.parcel_id ? `Dz. ${parcel.parcel_id.split('.').pop()}` : `Działka ${index + 1}`);
    const areaNum = toNum(parcel.area_ha);
    const areaStr = areaNum !== null && areaNum > 0 ? `${fmtNum(areaNum, 2)} ha` : '';

    const retGroup = getCropRetentionGroup(parcel.cropId || parcel.crop) || {
      name: 'Standardowa uprawa polowa',
      short_label: 'Uprawa polowa',
      badge_class: 'mr-badge--ret-generic',
    };

    const locationParts = [
      parcel.commune ? `gm. ${parcel.commune}` : null,
      parcel.county ? `pow. ${parcel.county}` : null,
    ].filter(Boolean);

    const pCounts = countParcelPriorities(recommendations);
    const summaryBadges = [];
    if (pCounts.krytyczny > 0) {
      summaryBadges.push(h('span', { class: 'mr-badge mr-badge--crit', text: `${pCounts.krytyczny} kryt.` }));
    }
    if (pCounts.wysoki > 0) {
      summaryBadges.push(h('span', { class: 'mr-badge mr-badge--warn', text: `${pCounts.wysoki} wys.` }));
    }
    summaryBadges.push(
      h('span', {
        class: 'mr-badge mr-badge--rec-count',
        text: `${recommendations.length} ${plZalen(recommendations.length)}`,
      })
    );

    const bodyEl = h('div', { class: 'mr-pcard-body', id: bodyId, hidden: !isExpanded }, [
      renderParcelMetrics(parcel),
      h('div', { class: 'mr-recs-list' }, [
        h('h4', { class: 'mr-recs-list__heading', text: `Dedykowane działania retencyjne (${recommendations.length}):` }),
        ...recommendations.map(renderRecommendationItem),
      ]),
    ]);

    const itemEl = h('li', {
      class: `mr-card mr-parcel-retention-card${isExpanded ? ' mr-parcel-retention-card--open' : ''}`,
      dataset: { parcelIndex: String(index), parcelKey: key },
    });

    const toggleBtn = h('button', {
      type: 'button',
      class: 'mr-pcard-toggle',
      'aria-expanded': String(isExpanded),
      'aria-controls': bodyId,
      onclick: () => {
        const nowOpen = !expandedParcelKeys.has(key);
        if (nowOpen) {
          expandedParcelKeys.add(key);
          state.selected = index;
          highlight(index);
          focusParcel(index);
          announce(`Rozwinięto rekomendacje dla działki ${pTitle}.`);
        } else {
          expandedParcelKeys.delete(key);
          announce(`Zwinięto rekomendacje dla działki ${pTitle}.`);
        }
        toggleBtn.setAttribute('aria-expanded', String(nowOpen));
        bodyEl.hidden = !nowOpen;
        itemEl.classList.toggle('mr-parcel-retention-card--open', nowOpen);
        syncToggleAllButton(container);
      },
    }, [
      h('div', { class: 'mr-pcard-header__left' }, [
        h('div', { class: 'mr-pcard-header__title-row' }, [
          h('span', { class: 'mr-pcard-title', text: pTitle }),
          areaStr ? h('span', { class: 'mr-badge mr-badge--area', text: areaStr }) : null,
          parcel.parcel_id ? h('span', { class: 'mr-badge mr-badge--teryt', text: parcel.parcel_id }) : null,
        ].filter(Boolean)),
        locationParts.length ? h('span', { class: 'mr-pcard-meta', text: locationParts.join(' · ') }) : null,
      ]),
      h('div', { class: 'mr-pcard-header__right' }, [
        h('div', { class: 'mr-crop-retention-pill' }, [
          h('span', { class: 'mr-crop-name', text: parcel.crop || 'Uprawa nieokreślona' }),
          h('span', { class: `mr-badge mr-ret-badge ${retGroup.badge_class || ''}`, text: retGroup.short_label || retGroup.name }),
        ]),
        h('div', { class: 'mr-pcard-summary-badges' }, summaryBadges),
        h('span', { class: 'mr-pcard-chevron-wrap', 'aria-hidden': 'true' }, [
          icon('chevronDown', 'mr-pcard-chevron'),
        ]),
      ]),
    ]);

    itemEl.append(toggleBtn, bodyEl);
    return itemEl;
  });

  const allInitiallyOpen = mergedParcels.length > 0 && mergedParcels.every((p, idx) => expandedParcelKeys.has(parcelKey(p, idx)));

  const parcelsSection = h('section', { class: 'mr-parcels-retention-section', 'aria-labelledby': 'mr-step4-parcels-heading' }, [
    h('div', { class: 'mr-parcels-list-header' }, [
      h('div', { class: 'mr-parcels-list-header__text' }, [
        h('h3', { class: 'mr-parcels-list-header__title', id: 'mr-step4-parcels-heading', text: `Lista działek i rekomendacji (${mergedParcels.length})` }),
        h('p', { class: 'mr-pcard-hint', text: 'Kliknij działkę na liście, aby rozwinąć okno wskaźników i szczegółowych zaleceń retencyjnych.' }),
      ]),
      h('div', { class: 'mr-parcels-list-actions' }, [
        h('button', {
          type: 'button',
          class: 'mr-btn mr-btn--ghost mr-btn--small',
          'data-ref': 'step4-toggle-all',
          'aria-pressed': String(allInitiallyOpen),
          text: allInitiallyOpen ? 'Zwiń wszystkie' : 'Rozwiń wszystkie',
          onclick: () => {
            const items = Array.from(container.querySelectorAll('.mr-parcel-retention-card'));
            const currentlyAllOpen = items.length > 0 && items.every((el) => el.classList.contains('mr-parcel-retention-card--open'));
            const nextOpen = !currentlyAllOpen;
            items.forEach((el) => {
              const k = el.dataset.parcelKey;
              const btn = el.querySelector('.mr-pcard-toggle');
              const body = el.querySelector('.mr-pcard-body');
              if (k) {
                if (nextOpen) expandedParcelKeys.add(k);
                else expandedParcelKeys.delete(k);
              }
              if (btn) btn.setAttribute('aria-expanded', String(nextOpen));
              if (body) body.hidden = !nextOpen;
              el.classList.toggle('mr-parcel-retention-card--open', nextOpen);
            });
            syncToggleAllButton(container);
            announce(nextOpen ? 'Rozwinięto okna wszystkich działek.' : 'Zwinięto okna wszystkich działek.');
          },
        }),
      ]),
    ]),
    h('ul', { class: 'mr-parcels-retention-list', 'aria-label': 'Lista działek z rozwijanymi rekomendacjami retencyjnymi' }, parcelItems),
  ]);

  // Złożenie całego widoku
  replace(container, [
    errorBanner,
    summaryCard,
    parcelsSection,
    // Stopka prawno-metodyczna raportu
    h('footer', { class: 'mr-report-footer' }, [
      h('p', { class: 'mr-muted', text: 'Zgodnie z zasadą „Zatrzymaj wodę tam, gdzie spadła” priorytetem jest budowa próchnicowej gąbki glebowej, mulczowanie oraz retencja w korytach rowów melioracyjnych. Sztuczne nawadnianie zalecane jest wyłącznie w sytuacjach skrajnych dla upraw wysokomarżowych.' }),
      h('p', { class: 'mr-muted', text: 'Źródła danych: Sentinel-2 L2A (CDSE / AWS), IMGW-PIB (dane meteorologiczne i synoptyczne), GUGiK (ULDK, Numeryczny Model Terenu DEM), ERA5-Land (wilgotność profilu glebowego).' }),
    ]),
  ].filter(Boolean));
}

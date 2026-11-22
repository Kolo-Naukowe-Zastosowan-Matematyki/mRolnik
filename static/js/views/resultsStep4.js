// KROK 4: Prezentacja i eksport rekomendacji dotyczących retencji wody
// Zgodnie z wytycznymi KOWR: „Zatrzymaj wodę, chroń plony”
// oraz logiką opisaną w logika_rekomendacji.md.
//
// Struktura prezentacji:
// 1. Główna rekomendacja zbiorcza (dla całego gospodarstwa / wszystkich działek) – na samej górze
// 2. Sekcja „Szczegóły” (dla pojedynczych działek) – wyraźnie oddzielona poniżej
// 3. Eksport do PDF odzwierciedlający ten sam układ.

import { h, icon, replace, nextId } from '../dom.js';
import { state, emit, announce } from '../state.js';
import { highlight, focusParcel } from '../map.js';
import { generateRetentionReport, normalizePriority } from '../recommendationEngine.js';

// Zbiór rozwiniętych działek (klucz: parcel_id lub indeks)
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
  // Wywołanie natywnego wydruku przeglądarki (Drukuj do PDF)
  // W @media print wszystkie okna działek są rozwinięte i widoczne w sekcji „Szczegóły”
  window.print();
}

/**
 * Rozwija i przewija do okna konkretnej działki w Kroku 4
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
 * Łączy dane wejściowe działek użytkownika z wynikami z /api/analyze
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
      isWarn: params.spadekWartosc !== null && params.spadekWartosc >= 3,
    },
    {
      label: 'Bilans KBW (14 dni)',
      val: params.kbwTekst,
      sub: 'Opad minus parowanie',
      isDanger: params.kbwWartosc !== null && params.kbwWartosc < -20,
      isWarn: params.kbwWartosc !== null && params.kbwWartosc < 0 && params.kbwWartosc >= -20,
    },
    {
      label: 'Zapas wody w glebie',
      val: params.zapasWodyTekst,
      sub: 'Dla strefy korzeniowej',
      isDanger: params.zapasWodyDni !== null && params.zapasWodyDni <= 3,
      isWarn: params.zapasWodyDni !== null && params.zapasWodyDni > 3 && params.zapasWodyDni <= 7,
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
 * Buduje pojedynczą kartę rekomendacji dla działki (zwięzły, jasny styl)
 */
function renderParcelRecCard(rec) {
  const priority = normalizePriority(rec.priorytet);
  const pCls = `mr-rec-priority--${priority}`;
  const pLabel = {
    krytyczny: 'PILNE',
    wysoki: 'WAŻNE',
    sredni: 'ZALECANE',
    informacyjny: 'WSKAZÓWKA',
  }[priority] || 'ZALECANE';

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
        h('strong', { class: 'mr-rec-detail__label', text: 'Zysk wodny: ' }),
        h('span', { class: 'mr-rec-detail__val', text: rec.zyskWodny }),
      ])
    );
  }

  if (rec.ekoschemat) {
    details.push(
      h('div', { class: 'mr-rec-detail mr-rec-detail--subsidy' }, [
        icon('check'),
        h('strong', { class: 'mr-rec-detail__label', text: 'Możliwe wsparcie: ' }),
        h('span', { class: 'mr-rec-detail__val', text: rec.ekoschemat }),
      ])
    );
  }

  return h('div', { class: `mr-rec-item ${pCls}` }, [header, title, desc, ...details]);
}

/**
 * Główna funkcja renderująca Krok 4:
 * 1. Główna rekomendacja zbiorcza (na górze jako najważniejszy element)
 * 2. Sekcja zatytułowana dokładnie „Szczegóły” (poniżej)
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

  // Usuń nieaktualne klucze
  const activeKeys = new Set(mergedParcels.map((p, idx) => parcelKey(p, idx)));
  Array.from(expandedParcelKeys).forEach((k) => {
    if (!activeKeys.has(k)) expandedParcelKeys.delete(k);
  });

  // Generuj ustrukturyzowany raport (główna rekomendacja + sekcja szczegóły)
  const report = generateRetentionReport(mergedParcels);
  const mainRec = report.glownaRekomendacja;
  const details = report.szczegoly;

  // Komunikat błędu analizy (jeśli wystąpił)
  const errorBanner = state.analysisError
    ? h('div', { class: 'mr-rec-error-callout', role: 'alert' }, [
        icon('warn'),
        h('div', { class: 'mr-rec-error-callout__text' }, [
          h('strong', { text: 'Część danych meteorologicznych jest niedostępna: ' }),
          h('span', { text: state.analysisError.message || 'Rekomendacje wygenerowano na podstawie danych o uprawach i glebie.' }),
        ]),
        h('button', {
          type: 'button',
          class: 'mr-btn mr-btn--secondary mr-btn--small',
          onclick: () => emit('analysis:retry'),
        }, [icon('refresh'), 'Spróbuj ponownie']),
      ])
    : null;

  // -------------------------------------------------------------------------
  // 1. GŁÓWNA REKOMENDACJA ZBIORCZA DLA GOSPODARSTWA (Wyeksponowana na górze)
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

    // Pasek tytułowy rekomendacji głównej
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
          'data-action': 'export-recommendations-pdf',
          onclick: exportRecommendationsPDF,
        }, [
          icon('download'),
          h('span', { text: 'Pobierz raport PDF' }),
        ]),
      ]),
    ]),

    // Treść syntetyczna w prostych, krótkich zdaniach
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

    // Zysk wodny i ekoschematy
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
  // 2. SEKCJA ZATYTUŁOWANA DOKŁADNIE: „Szczegóły” (Dla pojedynczych działek)
  // -------------------------------------------------------------------------
  const detailsHeader = h('div', { class: 'mr-details-section-header', id: 'sekcja-szczegoly' }, [
    h('div', { class: 'mr-details-section-header__inner' }, [
      h('h3', { class: 'mr-details-section-title', text: 'Szczegóły' }),
      h('p', { class: 'mr-details-section-sub', text: `Szczegółowe parametry wodne i zalecenia rozpisane dla każdej z ${details.liczbaDzialek} działek.` }),
    ]),
    h('div', { class: 'mr-details-section-actions' }, [
      h('button', {
        type: 'button',
        class: 'mr-btn mr-btn--ghost mr-btn--small',
        'data-ref': 'step4-toggle-all',
        text: 'Zwiń wszystkie',
        onclick: () => {
          const items = Array.from(container.querySelectorAll('.mr-parcel-retention-card'));
          const allOpen = items.length > 0 && items.every((el) => el.classList.contains('mr-parcel-retention-card--open'));
          const nextOpen = !allOpen;
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
          announce(nextOpen ? 'Rozwinięto szczegóły wszystkich działek.' : 'Zwinięto szczegóły wszystkich działek.');
        },
      }),
    ]),
  ]);

  // Domyślnie na starcie rozwiń wszystkie działki, aby rolnik od razu widział szczegóły
  if (expandedParcelKeys.size === 0) {
    details.dzialki.forEach((d) => {
      expandedParcelKeys.add(parcelKey(d.daneDzialki, d.indeks));
    });
  }

  const parcelCards = details.dzialki.map((d) => {
    const key = parcelKey(d.daneDzialki, d.indeks);
    const isExpanded = expandedParcelKeys.has(key);
    const bodyId = nextId('mr-pcard-body');

    // Nagłówek działki z przyciskiem zwijania/rozwijania
    const bodyEl = h('div', { class: 'mr-pcard-body', id: bodyId, hidden: !isExpanded }, [
      renderParcelParamTiles(d.parametryWodne),
      h('div', { class: 'mr-recs-list' }, [
        h('h5', { class: 'mr-recs-list__heading', text: `Zalecenia dla tej działki (${d.rekomendacje.length}):` }),
        ...d.rekomendacje.map(renderParcelRecCard),
      ]),
    ]);

    const itemEl = h('li', {
      class: `mr-card mr-parcel-retention-card${isExpanded ? ' mr-parcel-retention-card--open' : ''}`,
      dataset: { parcelIndex: String(d.indeks), parcelKey: key },
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
          state.selected = d.indeks;
          highlight(d.indeks);
          focusParcel(d.indeks);
          announce(`Rozwinięto szczegóły dla działki ${d.tytul}.`);
        } else {
          expandedParcelKeys.delete(key);
          announce(`Zwinięto szczegóły dla działki ${d.tytul}.`);
        }
        toggleBtn.setAttribute('aria-expanded', String(nowOpen));
        bodyEl.hidden = !nowOpen;
        itemEl.classList.toggle('mr-parcel-retention-card--open', nowOpen);
        syncToggleAllButton(container);
      },
    }, [
      h('div', { class: 'mr-pcard-header__left' }, [
        h('div', { class: 'mr-pcard-header__title-row' }, [
          h('span', { class: 'mr-pcard-title', text: d.tytul }),
          d.powierzchniaStr ? h('span', { class: 'mr-badge mr-badge--area', text: d.powierzchniaStr }) : null,
          d.teryt ? h('span', { class: 'mr-badge mr-badge--teryt', text: d.teryt }) : null,
        ].filter(Boolean)),
        d.lokalizacjaStr ? h('span', { class: 'mr-pcard-meta', text: d.lokalizacjaStr }) : null,
      ]),
      h('div', { class: 'mr-pcard-header__right' }, [
        h('div', { class: 'mr-crop-retention-pill' }, [
          h('span', { class: 'mr-crop-name', text: d.uprawa }),
          h('span', { class: `mr-badge mr-ret-badge ${d.grupaRetencyjnaBadge}`, text: d.grupaRetencyjnaKrotka }),
        ]),
        h('span', { class: 'mr-pcard-chevron-wrap', 'aria-hidden': 'true' }, [
          icon('chevronDown', 'mr-pcard-chevron'),
        ]),
      ]),
    ]);

    itemEl.append(toggleBtn, bodyEl);
    return itemEl;
  });

  const detailsSection = h('section', { class: 'mr-details-section', 'aria-labelledby': 'sekcja-szczegoly' }, [
    detailsHeader,
    h('ul', { class: 'mr-parcels-retention-list', 'aria-label': 'Szczegóły działek' }, parcelCards),
  ]);

  // Stopka raportu
  const reportFooter = h('footer', { class: 'mr-report-footer' }, [
    h('p', { class: 'mr-muted', text: 'Zasada naczelna KOWR: „Zatrzymaj wodę tam, gdzie spadła”. Podstawą jest budowa próchnicowej gąbki glebowej, mulczowanie oraz retencja w rowach melioracyjnych.' }),
    h('p', { class: 'mr-muted', text: 'Źródła danych: Satelita Sentinel-2 L2A (CDSE / AWS), IMGW-PIB (dane meteorologiczne), GUGiK (ULDK, Numeryczny Model Terenu DEM), ERA5-Land (wilgotność profilu glebowego).' }),
  ]);

  replace(container, [
    errorBanner,
    heroCard,
    detailsSection,
    reportFooter,
  ].filter(Boolean));
}

// KROK 5: Pobierz raport – wybór formatu zapisania i wydruku raportu gospodarowania wodą i retencji.
import { h, icon, replace } from '../dom.js';
import { state, emit, announce } from '../state.js';
import { exportCsv, downloadText } from '../csv.js';
import { generateRetentionReport, normalizePriority } from '../recommendationEngine.js';
import { mergeParcelData, getReportDateString, exportRecommendationsPDF } from './resultsStep4.js';

const DATA_SOURCES_TEXT = 'Satelita Sentinel-2 L2A (CDSE / AWS), IMGW-PIB (dane meteorologiczne), GUGiK (ULDK, Numeryczny Model Terenu DEM), ERA5-Land / NASA POWER (wilgotność profilu glebowego i opady).';
const LIMITATIONS_TEXT = 'Wyniki mają charakter doradczy i bazują na otwartych danych przestrzennych, satelitarnych oraz reanalizach meteorologicznych. Lokalne warunki (np. niedrożność drenów, zaskorupienie gleby czy lokalne opady burzowe) warto zweryfikować bezpośrednio na polu.';

const PRIORITY_LABELS = {
  krytyczny: 'PILNE',
  wysoki: 'WAŻNE',
  sredni: 'ZALECANE',
  informacyjny: 'WSKAZÓWKA',
};

function fileDateStamp() {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function escapeHtml(val) {
  return String(val ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function buildReportContext() {
  const rawParcels = state.customParcels || [];
  const analysisParcels = (state.analysis && Array.isArray(state.analysis.parcels)) ? state.analysis.parcels : [];
  const mergedParcels = mergeParcelData(rawParcels, analysisParcels);
  const report = generateRetentionReport(mergedParcels);
  return { mergedParcels, report };
}

/**
 * Tworzy samodzielny, bezpiecznie wyeskejpowany plik HTML raportu do otwarcia offline lub wydruku.
 */
function buildStandaloneHtml(report, generatedAt) {
  const main = report.glownaRekomendacja;
  const details = report.szczegoly;

  const stepsHtml = (main.glowneZaleceniaKrokPoKroku || [])
    .map((krok) => `<li>${escapeHtml(krok)}</li>`)
    .join('');

  const ecoHtml = (main.ekoschematyWPR || [])
    .map((eko) => `<li>${escapeHtml(eko)}</li>`)
    .join('');

  const parcelsHtml = (details.dzialki || []).map((d) => {
    const p = d.parametryWodne || {};
    const recsHtml = (d.rekomendacje || []).map((r) => {
      const pKey = normalizePriority(r.priorytet);
      const pLabel = PRIORITY_LABELS[pKey] || 'ZALECANE';
      return `<article class="rec rec--${escapeHtml(pKey)}">
        <div class="rec__meta">
          <span class="badge badge--${escapeHtml(pKey)}">${escapeHtml(pLabel)}</span>
          <span class="tag">${escapeHtml(r.kategoria || 'Retencja')}</span>
        </div>
        <h4>${escapeHtml(r.tytul)}</h4>
        <p>${escapeHtml(r.opis)}</p>
        ${r.uzasadnienie ? `<p class="small"><strong>Dlaczego:</strong> ${escapeHtml(r.uzasadnienie)}</p>` : ''}
        ${r.zyskWodny ? `<p class="small"><strong>Zysk wodny:</strong> ${escapeHtml(r.zyskWodny)}</p>` : ''}
        ${r.ekoschemat ? `<p class="small"><strong>Możliwe wsparcie:</strong> ${escapeHtml(r.ekoschemat)}</p>` : ''}
      </article>`;
    }).join('');

    return `<section class="parcel">
      <div class="parcel__head">
        <div>
          <h3>${escapeHtml(d.tytul)} ${d.powierzchniaStr ? `<span class="muted">(${escapeHtml(d.powierzchniaStr)})</span>` : ''}</h3>
          ${d.teryt || d.lokalizacjaStr ? `<p class="small muted">${escapeHtml([d.teryt, d.lokalizacjaStr].filter(Boolean).join(' · '))}</p>` : ''}
        </div>
        <div class="parcel__crop">
          <strong>${escapeHtml(d.uprawa)}</strong>
          <span class="tag">${escapeHtml(d.grupaRetencyjnaKrotka)}</span>
        </div>
      </div>
      <div class="metrics">
        <div class="metric"><span>Gleba i retencja</span><strong>${escapeHtml(p.glebaKat)}</strong><small>${escapeHtml(p.glebaTekst)}</small></div>
        <div class="metric"><span>Spadek terenu (DEM)</span><strong>${escapeHtml(p.spadekTekst)}</strong></div>
        <div class="metric"><span>Bilans KBW (14 dni)</span><strong>${escapeHtml(p.kbwTekst)}</strong></div>
        <div class="metric"><span>Zapas wody w glebie</span><strong>${escapeHtml(p.zapasWodyTekst)}</strong></div>
        <div class="metric"><span>Wilgotność satelitarna</span><strong>NDMI: ${escapeHtml(p.ndmiTekst)}</strong></div>
      </div>
      <div class="recs">${recsHtml}</div>
    </section>`;
  }).join('');

  return `<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Raport gospodarowania wodą i retencji – mRolnik</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; color: #0f172a; background: #f8fafc; margin: 0; padding: 24px; line-height: 1.5; }
    .wrap { max-width: 960px; margin: 0 auto; background: #fff; border: 1px solid #cbd5e1; border-radius: 12px; padding: 32px; }
    header { border-bottom: 2px solid #0f172a; padding-bottom: 16px; margin-bottom: 24px; }
    h1 { margin: 0 0 4px; font-size: 24px; }
    h2 { margin: 24px 0 12px; font-size: 20px; }
    h3 { margin: 0 0 4px; font-size: 17px; }
    h4 { margin: 6px 0; font-size: 15px; }
    .muted { color: #475569; }
    .small { font-size: 13px; margin: 4px 0; }
    .hero { border: 2px solid #0052a5; border-radius: 10px; padding: 20px; margin-bottom: 24px; background: #f8fbff; }
    .badges { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 14px; }
    .badge, .tag { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; border: 1px solid #cbd5e1; background: #f1f5f9; }
    .badge--krytyczny { background: #fdecee; color: #a3122a; border-color: #f5c2c7; }
    .badge--wysoki { background: #fff4e0; color: #8a4b00; border-color: #f3d19e; }
    .badge--sredni { background: #e6eef8; color: #0052a5; border-color: #b6d0f2; }
    .badge--informacyjny { background: #e8f3eb; color: #1d6b34; border-color: #badbcc; }
    .parcel { border: 1px solid #cbd5e1; border-radius: 10px; padding: 18px; margin-bottom: 18px; page-break-inside: avoid; }
    .parcel__head { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; border-bottom: 1px solid #e2e8f0; padding-bottom: 10px; margin-bottom: 12px; }
    .parcel__crop { display: flex; align-items: center; gap: 8px; }
    .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 14px; }
    .metric { border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px 10px; background: #f8fafc; display: flex; flex-direction: column; gap: 2px; font-size: 12px; }
    .metric strong { font-size: 13px; color: #0f172a; }
    .rec { border: 1px solid #e2e8f0; border-left: 4px solid #0052a5; border-radius: 8px; padding: 12px; margin-bottom: 10px; page-break-inside: avoid; }
    .rec--krytyczny { border-left-color: #a3122a; }
    .rec--wysoki { border-left-color: #8a4b00; }
    .rec--informacyjny { border-left-color: #1d6b34; }
    .rec__meta { display: flex; gap: 6px; margin-bottom: 4px; }
    footer { border-top: 1px solid #cbd5e1; margin-top: 28px; padding-top: 14px; font-size: 12px; color: #475569; }
    @media print { body { background: #fff; padding: 0; } .wrap { border: none; padding: 0; } }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>RAPORT GOSPODAROWANIA WODĄ I RETENCJI</h1>
      <p class="muted small">System mRolnik · KOWR: „Zatrzymaj wodę, chroń plony”</p>
      <p class="small">Data i godzina wygenerowania: <strong>${escapeHtml(generatedAt)}</strong></p>
    </header>

    <section class="hero">
      <h2>${escapeHtml(main.tytul)}</h2>
      <div class="badges">
        <span class="badge">Działek: ${escapeHtml(main.liczbaDzialek)}</span>
        <span class="badge">Areał: ${escapeHtml(main.lacznaPowierzchniaHa)}</span>
        <span class="badge badge--${escapeHtml(main.priorytet)}">${escapeHtml(main.statusZagrozenia)}</span>
      </div>
      <p>${escapeHtml(main.syntetycznaTresc)}</p>
      ${stepsHtml ? `<h3>Najważniejsze działania retencyjne:</h3><ol>${stepsHtml}</ol>` : ''}
      <p><strong>Oczekiwany zysk wodny:</strong> ${escapeHtml(main.zyskWodnyGospodarstwa)}</p>
      ${ecoHtml ? `<h3>Dostępne wsparcie finansowe (Ekoschematy WPR):</h3><ul>${ecoHtml}</ul>` : ''}
    </section>

    <h2>Szczegóły działek (${escapeHtml(details.liczbaDzialek)})</h2>
    ${parcelsHtml}

    <footer>
      <p><strong>Źródła danych:</strong> ${escapeHtml(DATA_SOURCES_TEXT)}</p>
      <p><strong>Ograniczenia raportu:</strong> ${escapeHtml(LIMITATIONS_TEXT)}</p>
    </footer>
  </div>
</body>
</html>`;
}

/**
 * Zapisuje raport jako plik CSV (zgodny z polskim Excelem – separator ; i UTF-8 BOM).
 */
function downloadReportCsv(report, generatedAt) {
  const columns = [
    { label: 'Data raportu', get: () => generatedAt },
    { label: 'Działka', key: 'tytul' },
    { label: 'TERYT', key: 'teryt' },
    { label: 'Lokalizacja', key: 'lokalizacjaStr' },
    { label: 'Powierzchnia [ha]', get: (d) => d.powierzchniaHa },
    { label: 'Uprawa', key: 'uprawa' },
    { label: 'Grupa retencyjna', key: 'grupaRetencyjnaNazwa' },
    { label: 'Gleba i retencja', get: (d) => `${d.parametryWodne.glebaKat} (${d.parametryWodne.glebaTekst})` },
    { label: 'Spadek terenu (DEM)', get: (d) => d.parametryWodne.spadekTekst },
    { label: 'Bilans KBW (14 dni)', get: (d) => d.parametryWodne.kbwTekst },
    { label: 'Zapas wody w glebie', get: (d) => d.parametryWodne.zapasWodyTekst },
    { label: 'Wilgotność satelitarna (NDMI)', get: (d) => d.parametryWodne.ndmiTekst },
    {
      label: 'Rekomendacje',
      get: (d) => (d.rekomendacje || [])
        .map((r, i) => `${i + 1}. [${PRIORITY_LABELS[normalizePriority(r.priorytet)] || 'ZALECANE'}] ${r.tytul}: ${r.opis}`)
        .join(' | '),
    },
  ];
  const csv = exportCsv(columns, report.szczegoly.dzialki || []);
  downloadText(`raport-retencji-${fileDateStamp()}.csv`, csv, 'text/csv;charset=utf-8');
}

/**
 * Zapisuje pełne dane raportu w formacie JSON.
 */
function downloadReportJson(report, generatedAt) {
  const payload = {
    meta: {
      system: 'mRolnik – KOWR „Zatrzymaj wodę, chroń plony”',
      wygenerowano: generatedAt,
      wygenerowano_iso: new Date().toISOString(),
      zrodla_danych: DATA_SOURCES_TEXT,
      ograniczenia: LIMITATIONS_TEXT,
    },
    glownaRekomendacja: report.glownaRekomendacja,
    szczegoly: {
      liczbaDzialek: report.szczegoly.liczbaDzialek,
      lacznaPowierzchniaHa: report.szczegoly.lacznaPowierzchniaHa,
      dzialki: (report.szczegoly.dzialki || []).map((d) => ({
        tytul: d.tytul,
        teryt: d.teryt,
        powierzchniaHa: d.powierzchniaHa,
        lokalizacja: d.lokalizacjaStr,
        uprawa: d.uprawa,
        grupaRetencyjna: d.grupaRetencyjnaNazwa,
        parametryWodne: d.parametryWodne,
        rekomendacje: (d.rekomendacje || []).map((r) => ({
          priorytet: normalizePriority(r.priorytet),
          kategoria: r.kategoria || 'Retencja',
          tytul: r.tytul,
          opis: r.opis,
          uzasadnienie: r.uzasadnienie || null,
          zyskWodny: r.zyskWodny || null,
          ekoschemat: r.ekoschemat || null,
        })),
      })),
    },
  };
  downloadText(
    `raport-retencji-${fileDateStamp()}.json`,
    `${JSON.stringify(payload, null, 2)}\n`,
    'application/json;charset=utf-8'
  );
}

/**
 * Zapisuje samodzielny plik HTML raportu.
 */
function downloadReportHtml(report, generatedAt) {
  const html = buildStandaloneHtml(report, generatedAt);
  downloadText(`raport-retencji-${fileDateStamp()}.html`, html, 'text/html;charset=utf-8');
}

/**
 * Główna funkcja renderująca Krok 5: Pobierz raport.
 *
 * @param {HTMLElement} container Element kontenera do wyrenderowania
 */
export function renderReportStep5(container) {
  if (!container) return;

  const { mergedParcels, report } = buildReportContext();

  if (mergedParcels.length === 0) {
    replace(container, [
      h('div', { class: 'mr-card mr-empty-card' }, [
        icon('info'),
        h('h3', { text: 'Brak wybranych działek' }),
        h('p', { class: 'mr-muted', text: 'Wróć do Kroku 1 i wybierz przynajmniej jedną działkę, aby przygotować i zapisać raport.' }),
      ]),
    ]);
    return;
  }

  const mainRec = report.glownaRekomendacja;
  const details = report.szczegoly;
  const generatedAt = getReportDateString();
  const totalRecs = (details.dzialki || []).reduce((sum, d) => sum + ((d.rekomendacje && d.rekomendacje.length) || 0), 0);

  const statusNote = h('p', { class: 'mr-report-status', role: 'status', 'aria-live': 'polite', hidden: true });
  const showStatus = (msg) => {
    statusNote.textContent = msg;
    statusNote.hidden = false;
    announce(msg);
  };

  const errorBanner = state.analysisError
    ? h('div', { class: 'mr-rec-error-callout', role: 'alert' }, [
        icon('warn'),
        h('div', { class: 'mr-rec-error-callout__text' }, [
          h('strong', { text: 'Część danych meteorologicznych jest niedostępna: ' }),
          h('span', { text: state.analysisError.message || 'Raport wygenerowano na podstawie danych o uprawach i glebie.' }),
        ]),
        h('button', {
          type: 'button',
          class: 'mr-btn mr-btn--secondary mr-btn--small',
          onclick: () => emit('analysis:retry'),
        }, [icon('refresh'), 'Spróbuj ponownie']),
      ])
    : null;

  // Karta podsumowania zawartości raportu
  const summaryCard = h('section', {
    class: 'mr-card mr-report-summary-card',
    'aria-label': 'Podsumowanie gotowego raportu',
  }, [
    h('div', { class: 'mr-report-summary__head' }, [
      h('div', { class: 'mr-report-summary__title-group' }, [
        h('span', { class: 'mr-badge mr-badge--section-tag', text: 'RAPORT GOTOWY DO ZAPISANIA' }),
        h('h3', { class: 'mr-report-summary__title', text: mainRec.tytul }),
      ]),
      h('span', { class: `mr-badge mr-badge--threat mr-badge--${mainRec.priorytet}`, text: mainRec.statusZagrozenia }),
    ]),
    h('div', { class: 'mr-report-summary__stats' }, [
      h('div', { class: 'mr-report-stat' }, [
        h('span', { class: 'mr-report-stat__label', text: 'Liczba działek' }),
        h('strong', { class: 'mr-report-stat__val', text: String(mainRec.liczbaDzialek) }),
      ]),
      h('div', { class: 'mr-report-stat' }, [
        h('span', { class: 'mr-report-stat__label', text: 'Łączny areał' }),
        h('strong', { class: 'mr-report-stat__val', text: mainRec.lacznaPowierzchniaHa }),
      ]),
      h('div', { class: 'mr-report-stat' }, [
        h('span', { class: 'mr-report-stat__label', text: 'Zaleceń w raporcie' }),
        h('strong', { class: 'mr-report-stat__val', text: String(totalRecs) }),
      ]),
      h('div', { class: 'mr-report-stat' }, [
        h('span', { class: 'mr-report-stat__label', text: 'Data wygenerowania' }),
        h('strong', { class: 'mr-report-stat__val', text: generatedAt }),
      ]),
    ]),
    h('div', { class: 'mr-report-includes' }, [
      h('p', { class: 'mr-report-includes__label', text: 'Raport zawiera:' }),
      h('ul', { class: 'mr-report-includes__list' }, [
        h('li', {}, [icon('check'), h('span', { text: 'Podsumowanie sytuacji wodnej i priorytetów dla całego gospodarstwa' })]),
        h('li', {}, [icon('check'), h('span', { text: 'Parametry glebowe, spadki terenu (DEM), bilans KBW i wskaźniki satelitarne dla każdej działki' })]),
        h('li', {}, [icon('check'), h('span', { text: 'Rekomendacje retencyjne, agrotechniczne i możliwe wsparcie z Ekoschematów WPR' })]),
        h('li', {}, [icon('check'), h('span', { text: 'Źródła danych, datę opracowania oraz informacje o ograniczeniach analizy' })]),
      ]),
    ]),
  ]);

  // Opcje zapisania raportu
  const options = [
    {
      id: 'mr-btn-report-pdf',
      featured: true,
      badge: 'ZALECANY',
      title: 'Zapisz jako PDF lub wydrukuj',
      desc: 'Otwiera przejrzysty, sformatowany raport do druku z rozwiniętymi szczegółami wszystkich działek. W oknie drukowania wybierz „Zapisz jako PDF” lub drukarkę.',
      btnLabel: 'Zapisz jako PDF / Drukuj',
      btnClass: 'mr-btn mr-btn--primary mr-btn--block',
      ico: 'download',
      onClick: () => {
        showStatus('Otwieranie podglądu wydruku i zapisu do PDF…');
        exportRecommendationsPDF();
      },
    },
    {
      id: 'mr-btn-report-html',
      featured: false,
      badge: 'DO PRZEGLĄDARKI',
      title: 'Pobierz plik HTML (offline)',
      desc: 'Samodzielny plik z całym raportem. Możesz otworzyć go później na komputerze lub telefonie bez dostępu do internetu albo przekazać doradcy.',
      btnLabel: 'Pobierz raport HTML',
      btnClass: 'mr-btn mr-btn--secondary mr-btn--block',
      ico: 'download',
      onClick: () => {
        downloadReportHtml(report, getReportDateString());
        showStatus('Pobrano raport w formacie HTML.');
      },
    },
    {
      id: 'mr-btn-report-csv',
      featured: false,
      badge: 'ARKUSZ KALKULACYJNY',
      title: 'Pobierz tabelę CSV (Excel)',
      desc: 'Zestawienie wszystkich działek, parametrów wodno-glebowych oraz zaleceń w pliku gotowym do otwarcia w programie Excel lub LibreOffice Calc.',
      btnLabel: 'Pobierz tabelę CSV',
      btnClass: 'mr-btn mr-btn--secondary mr-btn--block',
      ico: 'download',
      onClick: () => {
        downloadReportCsv(report, getReportDateString());
        showStatus('Pobrano zestawienie działek i zaleceń w formacie CSV.');
      },
    },
    {
      id: 'mr-btn-report-json',
      featured: false,
      badge: 'DANE STRUKTURALNE',
      title: 'Pobierz dane raportu (JSON)',
      desc: 'Kompletna kopia danych analitycznych i rekomendacji w formacie JSON do archiwizacji lub dalszego przetwarzania.',
      btnLabel: 'Pobierz plik JSON',
      btnClass: 'mr-btn mr-btn--ghost mr-btn--block',
      ico: 'download',
      onClick: () => {
        downloadReportJson(report, getReportDateString());
        showStatus('Pobrano dane raportu w formacie JSON.');
      },
    },
  ];

  const optionsSection = h('section', {
    class: 'mr-report-options-section',
    'aria-labelledby': 'mr-report-options-heading',
  }, [
    h('div', { class: 'mr-report-options__header' }, [
      h('h3', { id: 'mr-report-options-heading', class: 'mr-report-options__title', text: 'Wybierz sposób zapisania raportu' }),
      h('p', { class: 'mr-muted', text: 'Raport możesz zapisać w jednym lub kilku formatach – bez rejestracji i bez dodatkowego oprogramowania.' }),
    ]),
    statusNote,
    h('div', { class: 'mr-report-options-grid' }, options.map((opt) => h('article', {
      class: `mr-card mr-report-option-card${opt.featured ? ' mr-report-option-card--featured' : ''}`,
    }, [
      h('div', { class: 'mr-report-option-card__top' }, [
        h('span', { class: `mr-badge ${opt.featured ? 'mr-badge--section-tag' : 'mr-badge--stat'}`, text: opt.badge }),
        h('h4', { class: 'mr-report-option-card__title', text: opt.title }),
        h('p', { class: 'mr-report-option-card__desc', text: opt.desc }),
      ]),
      h('div', { class: 'mr-report-option-card__actions' }, [
        h('button', {
          type: 'button',
          id: opt.id,
          class: opt.btnClass,
          onclick: opt.onClick,
        }, [
          icon(opt.ico),
          h('span', { text: opt.btnLabel }),
        ]),
      ]),
    ]))),
  ]);

  const reportFooter = h('footer', { class: 'mr-report-footer' }, [
    h('p', { class: 'mr-muted', text: `Źródła danych: ${DATA_SOURCES_TEXT}` }),
    h('p', { class: 'mr-muted', text: `Ograniczenia: ${LIMITATIONS_TEXT}` }),
  ]);

  replace(container, [
    errorBanner,
    summaryCard,
    optionsSection,
    reportFooter,
  ].filter(Boolean));
}

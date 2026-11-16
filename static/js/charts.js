// Wykresy: Chart.js ładowany leniwie z static/vendor (tylko gdy potrzebny),
// każdy wykres ma tabelę danych (dla czytników ekranu ukrytą, dla wszystkich – pod przyciskiem).
import { h, icon, nextId } from './dom.js';

let chartPromise = null;
const charts = new Set();

function chartSrc() {
  const root = document.getElementById('mrolnik-root');
  const custom = root && root.dataset.chartSrc;
  return custom || new URL('../vendor/chartjs/chart.umd.js', import.meta.url).href;
}

export function loadChartJs() {
  if (window.Chart) return Promise.resolve(window.Chart);
  if (!chartPromise) {
    chartPromise = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = chartSrc();
      s.async = true;
      s.onload = () => (window.Chart ? resolve(window.Chart) : reject(new Error('Chart.js nie załadował się')));
      s.onerror = () => { chartPromise = null; reject(new Error('Nie udało się wczytać biblioteki wykresów.')); };
      document.head.appendChild(s);
    });
  }
  return chartPromise;
}

/** Kolory z tokenów CSS (działa w motywie jasnym i ciemnym). */
export function tokens(el) {
  const cs = getComputedStyle(el.closest('.mr-app') || document.documentElement);
  const g = (n, d) => (cs.getPropertyValue(n) || '').trim() || d;
  return {
    text: g('--mr-text', '#1b1b1b'),
    muted: g('--mr-muted', '#4d4d4d'),
    grid: g('--mr-grid', '#d9d9d9'),
    rain: g('--mr-chart-rain', '#0052a5'),
    rainForecast: g('--mr-chart-rain-fc', '#7fa9d6'),
    et0: g('--mr-chart-et0', '#b35c00'),
    tmax: g('--mr-chart-tmax', '#b81b33'),
    tmin: g('--mr-chart-tmin', '#0052a5'),
    balance: g('--mr-chart-balance', '#1d6b34'),
    ref: g('--mr-chart-ref', '#4d4d4d'),
    ndviRef: g('--mr-chart-ndvi-ref', '#6b4fa0'),
    warn: g('--mr-chart-et0', '#b35c00'),
    font: g('--mr-font', 'system-ui, sans-serif'),
  };
}

/**
 * Wykres + tabela alternatywna.
 * spec: {title, summary, columns:[{label}], rows:[[...]], build:(Chart, canvas, t) => config}
 */
export function chartFigure(spec) {
  const tableId = nextId('mr-tbl');
  const canvas = h('canvas', { role: 'img', 'aria-label': `${spec.title}. ${spec.summary || ''} Dane w tabeli poniżej.` });
  const table = h('table', { class: 'mr-table', id: tableId }, [
    h('caption', { text: spec.title }),
    h('thead', null, h('tr', null, spec.columns.map((c) => h('th', { scope: 'col', text: c.label })))),
    h('tbody', null, spec.rows.map((r) => h('tr', null, r.map((v, i) => (i === 0 ? h('th', { scope: 'row', text: v }) : h('td', { text: v })))))),
  ]);
  const tableWrap = h('div', { class: 'mr-tablewrap mr-visually-hidden', role: 'region', 'aria-label': `Tabela: ${spec.title}` }, table);
  const toggle = h('button', {
    type: 'button', class: 'mr-linkbtn mr-chart__toggle', 'aria-expanded': 'false', 'aria-controls': tableId,
    onclick: () => {
      const show = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', String(show));
      tableWrap.classList.toggle('mr-visually-hidden', !show);
      if (show) tableWrap.tabIndex = 0; else tableWrap.removeAttribute('tabindex');
      toggle.lastChild.textContent = show ? 'Ukryj tabelę danych' : 'Pokaż dane w tabeli';
    },
  }, [icon('layers'), h('span', { text: 'Pokaż dane w tabeli' })]);
  const box = h('div', { class: 'mr-chart' }, canvas);
  const status = h('p', { class: 'mr-chart__status', text: 'Wczytywanie wykresu…' });
  const fig = h('figure', { class: 'mr-figure' }, [
    h('figcaption', { class: 'mr-figure__cap' }, [h('span', { text: spec.title }),
      Array.isArray(spec.chip) ? h('div', { class: 'mr-chips mr-chips--cap' }, spec.chip.filter(Boolean)) : (spec.chip || null)]),
    spec.note ? h('p', { class: 'mr-hint mr-figure__note', text: spec.note }) : null,
    box, status, toggle, tableWrap,
  ]);

  const draw = (Chart) => {
    status.remove();
    const cfg = spec.build(Chart, canvas, tokens(fig));
    cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: matchMedia('(prefers-reduced-motion: reduce)').matches ? false : { duration: 300 } }, cfg.options || {});
    const chart = new Chart(canvas, cfg);
    chart.$mrRedraw = () => { chart.destroy(); charts.delete(chart); draw(Chart); };
    charts.add(chart);
  };
  loadChartJs().then(draw).catch((err) => {
    status.textContent = `${err.message} Dane są dostępne w tabeli.`;
    tableWrap.classList.remove('mr-visually-hidden');
    tableWrap.tabIndex = 0;
    toggle.hidden = true;
    box.hidden = true;
  });
  return fig;
}

/** Po zmianie motywu przerysuj wykresy z nowymi kolorami. Usuwa wykresy odłączone od DOM. */
export function redrawCharts() {
  Array.from(charts).forEach((c) => {
    if (!c.canvas || !c.canvas.isConnected) { c.destroy(); charts.delete(c); return; }
    c.$mrRedraw();
  });
}

export function pruneCharts() {
  Array.from(charts).forEach((c) => {
    if (!c.canvas || !c.canvas.isConnected) { c.destroy(); charts.delete(c); }
  });
}

export function baseScales(t, yTitle) {
  return {
    x: { ticks: { color: t.muted, font: { family: t.font, size: 12 }, maxRotation: 0, autoSkip: true }, grid: { display: false } },
    y: { ticks: { color: t.muted, font: { family: t.font, size: 12 } }, grid: { color: t.grid }, title: yTitle ? { display: true, text: yTitle, color: t.muted, font: { family: t.font, size: 12 } } : undefined },
  };
}

/**
 * Linia referencyjna (przerywana, bez punktów) – normy, progi, średnie wieloletnie.
 * W legendzie rysowana jako odcinek; etykieta powinna nazywać źródło (np. „norma 1991–2020, IMGW-PIB”).
 */
export function refDataset(label, data, color, extra = {}) {
  return Object.assign({
    type: 'line', label, data, borderColor: color, backgroundColor: color, borderDash: [7, 5], borderWidth: 2,
    pointRadius: 0, pointHoverRadius: 3, pointHitRadius: 6, pointStyle: 'line', fill: false, tension: 0, spanGaps: true, order: 0,
  }, extra);
}

/** Stała wartość (próg) na całej długości osi X. */
export function constLine(n, v) {
  return Array.from({ length: n }, () => (v === null || v === undefined ? null : Number(v)));
}

export function baseLegend(t) {
  return { labels: { color: t.text, font: { family: t.font, size: 13 }, boxWidth: 14, boxHeight: 14, usePointStyle: true }, position: 'bottom' };
}

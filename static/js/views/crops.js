// Zakładka „Plony”: stan upraw na działkach (Sentinel-2, porównanie rok do roku i ze średnią wieloletnią),
// plony z 1 ha w województwie na tle średniej krajowej (GUS – Bank Danych Lokalnych) + warunki pogodowe dla upraw.
import { h, icon, replace } from '../dom.js';
import { state, emit } from '../state.js';
import { val, num, fmtDate, provChip, errorView, emptyView } from '../format.js';
import { chartFigure, baseScales, baseLegend, refDataset } from '../charts.js';
import { ndviReference, ndviByMonth, yieldReference } from '../reference.js';
import { sectionize } from '../sections.js';
import { rulesCard } from './rules.js';
import { skeleton } from './field.js';

let renderToken = 0;

function isNum(v) { return v !== null && v !== undefined && Number.isFinite(Number(v)); }

function lastMonth(p) {
  const rows = p.history && Array.isArray(p.history.ndvi_monthly) ? p.history.ndvi_monthly : [];
  return rows.length ? rows[rows.length - 1] : null;
}

function sameMonthLastYear(p) {
  const rows = p.history && Array.isArray(p.history.ndvi_monthly) ? p.history.ndvi_monthly : [];
  const last = lastMonth(p);
  if (!last) return null;
  const [y, m] = String(last.month || '').split('-');
  const prev = rows.find((r) => r.month === `${Number(y) - 1}-${m}`);
  return prev && isNum(prev.mean) ? Number(prev.mean) : null;
}

function signed(d, digits = 2) { return d === null ? '–' : `${d >= 0 ? '+' : ''}${num(d, digits)}`; }

/** Komórka „vs średnia 2019–2025”: wypełniana po pobraniu średniej wieloletniej NDVI z serwera. */
function ndviRefCell(p) {
  const td = h('td', { text: '…' });
  const nd = p.ndvi || {};
  const pv = (p.provenance && p.provenance.ndvi) || {};
  if (!isNum(nd.mean) || !p.parcel_id) { td.textContent = '–'; return td; }
  const when = pv.observed_at ? new Date(pv.observed_at) : new Date();
  const month = Number.isNaN(when.getTime()) ? new Date().getMonth() + 1 : when.getMonth() + 1;
  ndviReference(p).then((ref) => {
    const m = ndviByMonth(ref);
    td.textContent = m.has(month) ? `${num(m.get(month), 2)} (${signed(Number(nd.mean) - m.get(month))})` : 'brak średniej';
  }).catch(() => { td.textContent = 'brak danych'; });
  return td;
}

export function renderCrops(panel) {
  const token = ++renderToken;
  // pogoda jest na Głównej – w Plonach tylko uprawy, plony GUS, klimat pola i przepisy
  if (state.loading) {
    replace(panel, h('section', { class: 'mr-card mr-wide', 'aria-busy': 'true' }, [
      h('h2', { class: 'mr-card__title' }, [icon('leaf'), 'Plony']), h('p', { class: 'mr-muted', role: 'status', text: 'Pobieranie danych działek…' }), skeleton(4)]));
    return;
  }
  if (state.analysisError) { replace(panel, errorView(state.analysisError, () => emit('analysis:retry'), 'Nie udało się pobrać danych upraw')); return; }
  const a = state.analysis;
  if (!a || !Array.isArray(a.parcels) || !a.parcels.length) {
    replace(panel, emptyView('Dodaj własne pole albo wybierz gospodarstwo z sekcji „Przykłady (demo)”.'));
    return;
  }
  replace(panel, []);
  const rows = a.parcels.map((p, i) => {
    const nd = p.ndvi || {};
    const prev = sameMonthLastYear(p);
    const pv = (p.provenance && p.provenance.ndvi) || {};
    const diff = isNum(nd.mean) && isNum(prev) ? Number(nd.mean) - prev : null;
    return h('tr', null, [
      h('th', { scope: 'row', text: p.id_tag || `Działka ${i + 1}` }),
      h('td', { text: p.crop || 'nieznana' }),
      h('td', { text: isNum(p.area_ha) ? val(p.area_ha, 'ha', 2) : '–' }),
      h('td', { text: isNum(nd.mean) ? num(nd.mean, 2) : 'brak danych' }),
      h('td', { text: p.history_status === 'pending' && !p.history ? 'wczytywanie…' : signed(diff) }),
      ndviRefCell(p),
      h('td', { text: pv.observed_at ? fmtDate(pv.observed_at) : '–' }),
      h('td', { text: p.status_label || '–' }),
    ]);
  });
  const card = h('section', { class: 'mr-card mr-wide', 'aria-labelledby': 'mr-crops-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-crops-h' }, [icon('leaf'), 'Stan upraw na działkach']),
    h('div', { class: 'mr-tablewrap', role: 'region', 'aria-labelledby': 'mr-crops-h', tabindex: '0' }, h('table', { class: 'mr-table' }, [
      h('thead', null, h('tr', null, ['Działka', 'Uprawa', 'Powierzchnia', 'NDVI teraz', 'Zmiana r/r', 'Średnia 2019–2025 (różnica)', 'Zdjęcie z dnia', 'Ocena']
        .map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, rows),
    ])),
    h('p', { class: 'mr-muted', text: 'NDVI z Copernicus Sentinel-2 (CDSE); „zmiana r/r” porównuje ostatni miesiąc z tym samym miesiącem rok wcześniej, '
      + '„średnia 2019–2025” – średnie NDVI tej działki w tym samym miesiącu kalendarzowym z lat 2019–2025. '
      + 'Prognozy plonu dla działki (t/ha) nie pokazujemy: nie ma publicznego, wiarygodnego źródła na poziomie działki – poniżej plony urzędowe GUS dla województwa.' }),
  ]);
  panel.prepend(card);
  const yieldCards = yieldSections(a.parcels, token);
  // przepisy i progi zabiegów – dla działki wybranej na mapie (albo pierwszej)
  const sel = a.parcels[Math.max(0, Math.min(state.selected < 0 ? 0 : state.selected, a.parcels.length - 1))];
  const isCur = (t) => t === renderToken;
  card.after(...yieldCards, rulesCard(sel, token, isCur));   // klimat pola jest w zakładce Historia
  sectionize(panel, [
    { id: 'mr-crops-h', label: 'Stan upraw' }, { id: 'mr-yield-h', label: 'Plony GUS' },
    { id: 'mr-rules-h', label: 'Przepisy i progi' },
  ], 'Sekcje zakładki Plony');
}

// --- plony GUS BDL: galeria małych wykresów (słupki = województwo, linia przerywana = średnia Polski) -------------
function distKm(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b)) return Infinity;
  const r = Math.PI / 180;
  const dLat = (b[0] - a[0]) * r;
  const dLon = (b[1] - a[1]) * r;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
}

/**
 * Województwo działki, gdy ewidencja (ULDK) go nie zwróciła: z najbliższej działki gospodarstwa (do 15 km)
 * albo z nazwy przykładowego gospodarstwa („Śląskie • Racibórz …”). Zwraca { name, how } albo null.
 */
function voivFallback(p, parcels) {
  const near = parcels
    .filter((q) => q !== p && q.voivodeship && Array.isArray(q.centroid))
    .map((q) => ({ q, d: distKm(p.centroid, q.centroid) }))
    .filter((x) => x.d <= 15)
    .sort((a, b) => a.d - b.d)[0];
  if (near) return { name: near.q.voivodeship, how: `jak sąsiednia działka ${near.q.id_tag || ''}`.trim() };
  const m = /^\s*([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż-]+)\s+•/.exec(String(state.farmName || ''));
  if (m) return { name: m[1].toLowerCase(), how: 'z opisu przykładowego gospodarstwa' };
  return null;
}

/** Uprawy na działkach (unikalne pary uprawa + województwo). */
export function cropGroups(parcels) {
  const groups = new Map();
  parcels.forEach((p) => {
    if (!p.crop) return;
    const fb = p.voivodeship ? null : voivFallback(p, parcels);
    const voiv = p.voivodeship || (fb && fb.name) || null;
    const key = `${p.crop.trim().toLowerCase()}|${(voiv || '').toLowerCase()}`;
    if (!groups.has(key)) groups.set(key, { crop: p.crop.trim(), voiv, voivHow: fb ? fb.how : null, centroid: p.centroid, parcels: [] });
    groups.get(key).parcels.push(p.id_tag || p.parcel_id || 'działka');
  });
  return groups;
}

/** Komunikat zamiast surowego błędu walidacji („Sprawdź wprowadzone dane”). */
function yieldError(g, e) {
  if (!g.voiv && e && e.status === 422) {
    return 'Nie znamy województwa tej działki – ewidencja gruntów GUGiK (ULDK) nie zwróciła danych dla tego punktu, '
      + 'więc nie da się dobrać plonów GUS. Wskaż działkę dokładniej albo podaj jej numer ewidencyjny.';
  }
  return `Nie udało się pobrać plonów z GUS: ${e && e.message ? e.message : 'błąd połączenia'}`;
}

function yieldSections(parcels, token) {
  const groups = cropGroups(parcels);
  const head = h('h2', { class: 'mr-card__title', id: 'mr-yield-h' }, [icon('leaf'), 'Plony z 1 ha w województwie na tle Polski (GUS)']);
  if (!groups.size) {
    return [h('section', { class: 'mr-card mr-wide', 'aria-labelledby': 'mr-yield-h' }, [head,
      h('p', { class: 'mr-muted', text: 'Podaj uprawę na działkach, aby porównać plony z danymi GUS.' })])];
  }
  const items = Array.from(groups.values()).map((g) => {
    const body = h('div', { 'aria-busy': 'true' }, [h('p', { class: 'mr-muted', role: 'status', text: 'Pobieranie plonów z GUS…' }), skeleton(3)]);
    const li = h('li', { class: 'mr-gallery__item' }, [
      h('h3', { class: 'mr-gallery__title', text: g.crop }),
      h('p', { class: 'mr-gallery__sub', text: `${g.voiv ? `woj. ${g.voiv}${g.voivHow ? ` (${g.voivHow})` : ''} · ` : ''}działki: ${g.parcels.join(', ')}` }),
      body,
    ]);
    const done = (content) => { if (token !== renderToken) return; body.removeAttribute('aria-busy'); replace(body, content); };
    yieldReference(g.crop, g.voiv, g.centroid).then((d) => done(yieldChart(g, d)))
      .catch((e) => done([h('p', { class: 'mr-callout mr-callout--info mr-gallery__msg' }, [icon('info'), yieldError(g, e)])]));
    return li;
  });
  return [h('section', { class: 'mr-card mr-wide', 'aria-labelledby': 'mr-yield-h' }, [
    head,
    h('p', { class: 'mr-hint', text: 'Słupki – plon przeciętny w województwie działki; linia przerywana – średnia Polski. '
      + 'Dane: GUS, Bank Danych Lokalnych (plony z 1 ha, wszystkie gospodarstwa), ostatnie 10 lat. 1 dt/ha = 0,1 t/ha.' }),
    h('ul', { class: 'mr-gallery' }, items),
  ])];
}

function yieldChart(g, d) {
  if (!d || !d.available || !Array.isArray(d.years) || !d.years.length) {
    return [h('p', { class: 'mr-callout mr-callout--info mr-gallery__msg' }, [icon('info'), d && d.note ? d.note : 'Brak danych GUS dla tej uprawy.']),
      d && d.provenance ? provChip(d.provenance) : null];
  }
  const ys = d.years;
  const vname = (d.voivodeship && d.voivodeship.name) || g.voiv || 'województwo';
  const lastRow = ys[ys.length - 1];
  const vs = isNum(lastRow.voivodeship) && isNum(lastRow.poland) ? Number(lastRow.voivodeship) - Number(lastRow.poland) : null;
  const summary = `${d.gus_crop}, ${lastRow.year}: woj. ${vname} ${val(lastRow.voivodeship, 'dt/ha', 1)}, Polska ${val(lastRow.poland, 'dt/ha', 1)}`
    + `${vs === null ? '' : ` (${signed(vs, 1)} dt/ha)`}. Średnia ${ys[0].year}–${lastRow.year}: województwo ${val(d.voivodeship_mean_dt_ha, 'dt/ha', 1)}, Polska ${val(d.poland_mean_dt_ha, 'dt/ha', 1)}.`;
  return [
    h('p', { class: 'mr-gallery__kpi' }, [
      h('strong', { text: val(lastRow.voivodeship, 'dt/ha', 1) }),
      ` w ${lastRow.year} r.`,
      vs === null ? null : h('span', { class: `mr-gallery__delta mr-gallery__delta--${vs >= 0 ? 'up' : 'down'}`, text: ` ${signed(vs, 1)} vs Polska` }),
    ]),
    chartFigure({
      title: `${d.gus_crop} (GUS)`,
      summary,
      chip: [provChip(d.provenance)],
      note: d.note || null,
      columns: [{ label: 'Rok' }, { label: `Woj. ${vname} [dt/ha]` }, { label: 'Polska – średnia GUS [dt/ha]' }],
      rows: ys.map((r) => [String(r.year), val(r.voivodeship, '', 1), val(r.poland, '', 1)]),
      build: (Chart, canvas, t) => ({
        type: 'bar',
        data: {
          labels: ys.map((r) => String(r.year).slice(2)),
          datasets: [
            { type: 'bar', label: `Woj. ${vname}`, data: ys.map((r) => r.voivodeship), backgroundColor: t.balance, borderRadius: 2, order: 2 },
            refDataset('Polska (GUS)', ys.map((r) => r.poland), t.ref, { pointRadius: 2, pointStyle: 'line' }),
          ],
        },
        options: {
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: Object.assign(baseLegend(t), { labels: Object.assign(baseLegend(t).labels, { font: { family: t.font, size: 12 }, boxWidth: 10 }) }),
            tooltip: { callbacks: { title: (it) => `${ys[it[0].dataIndex].year}` } } },
          scales: Object.assign(baseScales(t, 'dt/ha'), { y: Object.assign(baseScales(t, 'dt/ha').y, { beginAtZero: true }) }),
        },
      }),
    }),
  ];
}

// Zakładka „Pogoda”: warunki bieżące (IMGW-PIB), bilans opadów, ostrzeżenia, prognoza 14 dni.
import { h, icon, replace, infoTip } from '../dom.js';
import { state, emit } from '../state.js';
import { val, num, provChip, errorView, emptyView, fmtDay, fmtDate } from '../format.js';
import { chartFigure, baseScales, baseLegend, refDataset } from '../charts.js';
import { skeleton } from './field.js';

export function renderWeather(panel) {
  if (state.loading) {
    replace(panel, [h('section', { class: 'mr-card', 'aria-busy': 'true' }, [h('h2', { class: 'mr-card__title' }, [icon('cloud'), 'Pogoda teraz']), h('p', { class: 'mr-muted', role: 'status', text: 'Pobieranie danych pogodowych…' }), skeleton(3)]), h('section', { class: 'mr-card', 'aria-hidden': 'true' }, skeleton(5))]);
    return;
  }
  if (state.analysisError) { replace(panel, errorView(state.analysisError, () => emit('analysis:retry'), 'Nie udało się pobrać pogody')); return; }
  const w = state.analysis && state.analysis.weather;
  if (!w) { replace(panel, emptyView('Brak danych pogodowych. Wybierz pole, aby zobaczyć pogodę.')); return; }
  const pv = w.provenance || {};
  const cur = w.current || {};
  replace(panel, [
    h('section', { class: 'mr-card', 'aria-labelledby': 'mr-wnow' }, [
      h('h2', { class: 'mr-card__title', id: 'mr-wnow' }, [icon('cloud'), 'Pogoda teraz']),
      h('dl', { class: 'mr-kpis mr-kpis--4' }, [
        kpi('thermo', 'Temperatura', val(cur.temp_c, '°C', 1)),
        kpi('drop', 'Wilgotność', val(cur.humidity_pct, '%', 0)),
        kpi('wind', 'Wiatr', val(cur.wind_ms, 'm/s', 1)),
        kpi('cloud', 'Opad', val(cur.precip_mm, 'mm', 1)),
      ]),
      provChip(pv.current, { station: cur.station ? `· ${cur.station.replace(/\s*\(IMGW-PIB\)\s*/, '')}` : '', time: true }),
    ]),
    warnings(w, pv),
    h('section', { class: 'mr-card', 'aria-labelledby': 'mr-wbal' }, [
      h('h2', { class: 'mr-card__title', id: 'mr-wbal' }, [icon('drop'), 'Woda z opadów']),
      h('dl', { class: 'mr-pairs' }, [
        h('dt', { text: 'Opad – ostatnie 7 dni' }), h('dd', { text: val(w.precip_7d_mm, 'mm') }),
        h('dt', { text: 'Opad – 14 dni' }), h('dd', { text: val(w.precip_14d_mm, 'mm') }),
        h('dt', null, ['Bilans wodny 14 dni ', infoTip('bilans wodny', 'Klimatyczny bilans wodny = opad minus parowanie z gleby i roślin. Wartość ujemna oznacza, że woda ubywa.')]),
        h('dd', { text: w.climatic_water_balance_14d_mm === null || w.climatic_water_balance_14d_mm === undefined ? 'Brak danych' : `${Number(w.climatic_water_balance_14d_mm) > 0 ? '+' : ''}${val(w.climatic_water_balance_14d_mm, 'mm')}` }),
        h('dt', { text: 'Temperatura gleby' }), h('dd', { text: val(w.soil_temp_c, '°C') }),
        ...normalPrecipPair(w),
      ]),
      h('div', { class: 'mr-chips' }, [provChip(pv.forecast), normals(w) ? provChip(normals(w).provenance) : null]),
    ]),
    forecast(w, pv),
  ]);
}

/** Normy IMGW-PIB 1991–2020 z odpowiedzi API (reference.climate_normals) albo null. */
function normals(w) {
  const n = w && w.reference && w.reference.climate_normals;
  return n && n.provenance ? n : null;
}

function normalPrecipPair(w) {
  const n = normals(w);
  const d = Array.isArray(w.daily) ? w.daily : [];
  if (!n || !Array.isArray(n.daily) || !d.length) return [];
  const byDay = new Map(n.daily.map((x) => [x.date, x.precip_normal_mm]));
  const vals = d.map((x) => byDay.get(x.date)).filter((v) => v !== null && v !== undefined);
  if (vals.length !== d.length) return [];
  const sum = vals.reduce((a, b) => a + Number(b), 0);
  return [h('dt', { text: `Norma opadu – ${d.length} dni (IMGW-PIB 1991–2020)` }), h('dd', { text: val(sum, 'mm') })];
}

function kpi(ico, label, value) {
  return h('div', { class: 'mr-kpi' }, [h('dt', null, [icon(ico), label]), h('dd', { text: value })]);
}

function warnings(w, pv) {
  const list = Array.isArray(w.warnings) ? w.warnings : [];
  return h('section', { class: 'mr-card', 'aria-labelledby': 'mr-wwarn' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-wwarn' }, [icon('warn'), 'Ostrzeżenia meteorologiczne']),
    list.length
      ? h('ul', { class: 'mr-warnlist' }, list.map((x) => h('li', null, [icon('warn'),
        h('span', null, [h('strong', { text: `${x.event || 'Zjawisko'}${x.level ? ` (stopień ${x.level})` : ''}` }),
          x.valid_from ? ` – od ${fmtDate(x.valid_from, true)}${x.valid_to ? ` do ${fmtDate(x.valid_to, true)}` : ''}` : ''])])))
      : h('p', { class: 'mr-muted', text: 'Brak aktualnych ostrzeżeń dla tego obszaru.' }),
    provChip(pv.warnings),
  ]);
}

function forecast(w, pv) {
  const d = Array.isArray(w.daily) ? w.daily : [];
  if (!d.length) return h('section', { class: 'mr-card' }, [h('h2', { class: 'mr-card__title', text: 'Prognoza 14 dni' }), h('p', { class: 'mr-muted', text: 'Brak danych prognozy.' }), provChip(pv.forecast)]);
  const labels = d.map((x) => fmtDay(x.date));
  const rainSum = d.reduce((s, x) => s + (Number(x.rain_mm) || 0), 0);
  const n = normals(w);
  const nd = new Map((n && Array.isArray(n.daily) ? n.daily : []).map((x) => [x.date, x]));
  const ref = (k) => d.map((x) => { const r = nd.get(x.date); return r && r[k] !== null && r[k] !== undefined ? Number(r[k]) : null; });
  const hasRef = !!n && ref('t_max_normal_c').some((v) => v !== null);
  const refName = hasRef ? `norma 1991–2020, IMGW-PIB ${n.station}` : '';
  return h('section', { class: 'mr-card', 'aria-labelledby': 'mr-wfc' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-wfc' }, [icon('sun'), 'Prognoza 14 dni']),
    chartFigure({
      title: 'Opad i temperatura – prognoza 14 dni',
      summary: `Suma opadów ${num(rainSum, 0)} mm.${hasRef ? ` Linie przerywane: ${refName}.` : ''}`,
      chip: [provChip(pv.forecast), n ? provChip(n.provenance) : null],
      note: hasRef ? `Linie przerywane – normy klimatyczne 1991–2020 ze stacji IMGW-PIB ${n.station} (${num(n.distance_km, 0)} km): `
        + 'średnia temperatura maksymalna i minimalna miesiąca oraz norma opadu na dobę (suma miesięczna / liczba dni).'
        : (n ? `Brak normy klimatycznej: ${n.provenance.note || 'brak stacji z danymi 1991–2020'}` : null),
      columns: [{ label: 'Dzień' }, { label: 'Opad [mm]' }, { label: 'Temp. maks. [°C]' }, { label: 'Temp. min. [°C]' },
        ...(hasRef ? [{ label: 'Norma opadu/dobę 1991–2020 [mm]' }, { label: 'Norma temp. maks. [°C]' }, { label: 'Norma temp. min. [°C]' }] : [])],
      rows: d.map((x, i) => [labels[i], val(x.rain_mm, '', 1), val(x.t_max_c, '', 1), val(x.t_min_c, '', 1),
        ...(hasRef ? [val(ref('precip_normal_mm')[i], '', 1), val(ref('t_max_normal_c')[i], '', 1), val(ref('t_min_normal_c')[i], '', 1)] : [])]),
      build: (Chart, canvas, t) => ({
        type: 'bar',
        data: {
          labels,
          datasets: [
            { type: 'bar', label: 'Opad [mm]', data: d.map((x) => x.rain_mm), backgroundColor: t.rain, yAxisID: 'y', borderRadius: 3, order: 2 },
            { type: 'line', label: 'Temp. maks. [°C]', data: d.map((x) => x.t_max_c), borderColor: t.tmax, backgroundColor: t.tmax, yAxisID: 'y1', tension: 0.3, pointRadius: 2, order: 1 },
            { type: 'line', label: 'Temp. min. [°C]', data: d.map((x) => x.t_min_c), borderColor: t.tmin, backgroundColor: t.tmin, yAxisID: 'y1', tension: 0.3, pointRadius: 2, order: 1 },
            ...(hasRef ? [
              refDataset('Norma opadu/dobę 1991–2020 (IMGW-PIB) [mm]', ref('precip_normal_mm'), t.rain, { yAxisID: 'y', stepped: 'middle', borderDash: [3, 3] }),
              refDataset('Norma temp. maks. 1991–2020 (IMGW-PIB) [°C]', ref('t_max_normal_c'), t.tmax, { yAxisID: 'y1' }),
              refDataset('Norma temp. min. 1991–2020 (IMGW-PIB) [°C]', ref('t_min_normal_c'), t.tmin, { yAxisID: 'y1' }),
            ] : []),
          ],
        },
        options: {
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: baseLegend(t) },
          scales: {
            ...baseScales(t, 'mm'),
            y1: { position: 'right', ticks: { color: t.muted }, grid: { display: false }, title: { display: true, text: '°C', color: t.muted } },
          },
        },
      }),
    }),
  ]);
}

// Zakładka „Plony” – sekcja „Klimat pola: co się zmienia”: wykresy przydatne w obliczu zmian klimatu (ERA5 od 1991 r.).
// Każdy wykres ma linię trendu (przerywana) i średnią 1991–2020 (kropkowana) jako odniesienie; dane i źródła – z serwera.
import { h, icon, replace } from '../dom.js';
import { api } from '../api.js';
import { num, provChip } from '../format.js';
import { chartFigure, baseScales, baseLegend, refDataset, constLine } from '../charts.js';
import { yieldReference } from '../reference.js';
import { skeleton } from './field.js';

const cache = new Map();
const MONTHS = ['sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru'];

function isNum(v) { return v !== null && v !== undefined && Number.isFinite(Number(v)); }
function signed(v, d = 1) { return `${Number(v) >= 0 ? '+' : ''}${num(v, d)}`; }
function doyLabel(doy) {
  if (!isNum(doy)) return '–';
  const d = new Date(2023, 0, 1); d.setDate(Math.round(doy));
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

function load(lat, lon) {
  const key = `${Number(lat).toFixed(2)},${Number(lon).toFixed(2)}`;
  if (!cache.has(key)) cache.set(key, api(`/api/reference/climate?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`, { timeout: 45000 })
    .catch((e) => { cache.delete(key); throw e; }));
  return cache.get(key);
}

/** Linia trendu (od – do) i średnia 1991–2020 dla serii rocznej. */
function refs(d, key, n, t) {
  const out = [];
  const tr = d.trends && d.trends[key];
  if (tr) {
    out.push(refDataset(`Trend (${signed(tr.per_decade, Math.abs(tr.per_decade) < 10 ? 1 : 0)} na 10 lat)`,
      Array.from({ length: n }, (_, i) => tr.start + ((tr.end - tr.start) * i) / Math.max(1, n - 1)), t.tmax, { borderWidth: 2 }));
  }
  const nv = d.normals && d.normals[key];
  if (isNum(nv)) out.push(refDataset(`Średnia ${d.normal_period}`, constLine(n, nv), t.ref, { borderDash: [2, 3] }));
  return out;
}

function yearChart(d, spec) {
  const ys = d.years;
  const labels = ys.map((r) => String(r.year));
  const vals = ys.map((r) => r[spec.key]);
  const negBelow = spec.negBelow === 'normal' ? ((d.normals && d.normals[spec.key]) ?? 0) : (spec.negBelow ?? 0);
  const tr = d.trends && d.trends[spec.key];
  const nv = d.normals && d.normals[spec.key];
  const last = ys[ys.length - 1];
  const summary = `${spec.title}: ${last.year} – ${spec.fmt(last[spec.key])}; średnia ${d.normal_period} – ${spec.fmt(nv)}`
    + `${tr ? `; trend ${signed(tr.per_decade, spec.td || 1)} ${spec.unit} na 10 lat` : ''}.`;
  return h('li', { class: 'mr-gallery__item' }, [
    h('h3', { class: 'mr-gallery__title', text: spec.title }),
    h('p', { class: 'mr-gallery__kpi' }, [
      tr ? h('strong', { class: `mr-gallery__delta--${(spec.goodUp ? tr.per_decade >= 0 : tr.per_decade <= 0) ? 'up' : 'down'}`, text: `${signed(tr.per_decade, spec.td || 1)} ${spec.unit}` }) : null,
      tr ? ' na 10 lat' : null,
    ]),
    h('p', { class: 'mr-gallery__sub', text: spec.why }),
    chartFigure({
      title: spec.title,
      summary,
      chip: [provChip(d.provenance)],
      columns: [{ label: 'Rok' }, { label: `${spec.title} [${spec.unit}]` }],
      rows: ys.map((r) => [String(r.year), spec.fmt(r[spec.key])]),
      build: (Chart, canvas, t) => ({
        type: spec.type || 'bar',
        data: {
          labels,
          datasets: [
            spec.type === 'line'
              ? { type: 'line', label: spec.short || spec.title, data: vals, borderColor: t[spec.color || 'balance'], backgroundColor: t[spec.color || 'balance'], pointRadius: 2, borderWidth: 2, tension: 0.2, order: 2 }
              : { type: 'bar', label: spec.short || spec.title, data: vals, borderRadius: 2, order: 2,
                backgroundColor: vals.map((v) => (spec.negColor && isNum(v) && v < negBelow ? t.tmax : t[spec.color || 'balance'])) },
            ...refs(d, spec.key, ys.length, t),
          ],
        },
        options: {
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: Object.assign(baseLegend(t), { labels: Object.assign(baseLegend(t).labels, { font: { family: t.font, size: 11 }, boxWidth: 10 }) }),
            tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${spec.fmt(c.parsed.y)}` } } },
          scales: Object.assign(baseScales(t, spec.unit), {
            x: Object.assign(baseScales(t).x, { ticks: { color: t.muted, maxTicksLimit: 8, maxRotation: 0 } }),
            y: Object.assign(baseScales(t, spec.unit).y, spec.y || {}, spec.doy ? { ticks: { color: t.muted, callback: (v) => doyLabel(v) } } : {}),
          }),
        },
      }),
    }),
  ]);
}

const SPECS = [
  { key: 't_mean_c', title: 'Średnia temperatura roczna', unit: '°C', type: 'line', color: 'tmax', td: 2, fmt: (v) => (isNum(v) ? `${num(v, 1)}°C` : '–'),
    why: 'Cieplejsze lata = szybszy rozwój roślin, więcej szkodników i chorób, większe parowanie.', y: { beginAtZero: false } },
  { key: 'precip_mm', title: 'Roczna suma opadów', unit: 'mm', color: 'rain', td: 0, goodUp: true, fmt: (v) => (isNum(v) ? `${num(v, 0)} mm` : '–'),
    why: 'Sama suma opadów to nie wszystko – ważne, kiedy pada (patrz bilans wodny).' },
  { key: 'season_days', title: 'Długość okresu wegetacyjnego', unit: 'dni', color: 'balance', td: 1, goodUp: true, fmt: (v) => (isNum(v) ? `${num(v, 0)} dni` : '–'),
    why: 'Dni od wiosennego do jesiennego przejścia Tśr przez 5°C – dłuższy sezon daje szansę na międzyplony i późniejsze odmiany.', y: { beginAtZero: false } },
  { key: 'hot_days', title: 'Dni upalne (Tmax > 30°C)', unit: 'dni', color: 'tmax', td: 1, fmt: (v) => (isNum(v) ? `${num(v, 0)} dni` : '–'),
    why: 'Upał w czasie kwitnienia i nalewania ziarna obniża plon zbóż i rzepaku.' },
  { key: 'kbw_apr_sep_mm', title: 'Bilans wodny IV–IX (opad − parowanie)', unit: 'mm', color: 'rain', td: 0, goodUp: true, negColor: true, negBelow: 'normal',
    fmt: (v) => (isNum(v) ? `${signed(v, 0)} mm` : '–'),
    why: 'Wskaźnik suszy rolniczej: im niżej, tym bardziej roślinom brakuje wody. Czerwone słupki – sezony suchsze niż średnia 1991–2020.' },
  { key: 'gdd10_apr_sep', title: 'Ciepło dla upraw – stopniodni > 10°C (IV–IX)', unit: '°C·dni', color: 'et0', td: 0, goodUp: true, fmt: (v) => (isNum(v) ? `${num(v, 0)}` : '–'),
    why: 'Suma ciepła decyduje o dojrzewaniu kukurydzy, słonecznika, soi – rosnące GDD pozwala na późniejsze odmiany (wyższe FAO).' },
];

function frostChart(d) {
  const ys = d.years;
  const labels = ys.map((r) => String(r.year));
  const tS = d.trends.last_spring_frost_doy; const tA = d.trends.first_autumn_frost_doy;
  const last = ys[ys.length - 1];
  const summary = `Ostatni wiosenny przymrozek ${last.year}: ${last.last_spring_frost || 'brak'}; pierwszy jesienny: ${last.first_autumn_frost || 'brak'}. `
    + `Średnio (${d.normal_period}): ${doyLabel(d.normals.last_spring_frost_doy)} i ${doyLabel(d.normals.first_autumn_frost_doy)}.`;
  const trendLine = (tr) => (tr ? Array.from({ length: ys.length }, (_, i) => tr.start + ((tr.end - tr.start) * i) / Math.max(1, ys.length - 1)) : []);
  return h('li', { class: 'mr-gallery__item' }, [
    h('h3', { class: 'mr-gallery__title', text: 'Przymrozki: ostatni wiosenny i pierwszy jesienny' }),
    h('p', { class: 'mr-gallery__kpi' }, [h('strong', { text: `${doyLabel(d.normals.last_spring_frost_doy)} – ${doyLabel(d.normals.first_autumn_frost_doy)}` }), ` okres bez przymrozków (śr. ${d.normal_period})`]),
    h('p', { class: 'mr-gallery__sub', text: 'Tmin ≤ 0°C na 2 m. Późne przymrozki groźne dla kwitnącego rzepaku i sadów, wczesne – dla kukurydzy przed dojrzałością.' }),
    chartFigure({
      title: 'Daty przymrozków', summary, chip: [provChip(d.provenance)],
      columns: [{ label: 'Rok' }, { label: 'Ostatni wiosenny' }, { label: 'Pierwszy jesienny' }],
      rows: ys.map((r) => [String(r.year), r.last_spring_frost || '–', r.first_autumn_frost || '–']),
      build: (Chart, canvas, t) => ({
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: 'Ostatni wiosenny', data: ys.map((r) => r.last_spring_frost_doy), borderColor: t.tmin, backgroundColor: t.tmin, pointRadius: 2, borderWidth: 2, order: 2 },
            { label: 'Pierwszy jesienny', data: ys.map((r) => r.first_autumn_frost_doy), borderColor: t.et0, backgroundColor: t.et0, pointRadius: 2, borderWidth: 2, order: 2 },
            ...(tS ? [refDataset(`Trend wiosna (${signed(tS.per_decade, 1)} dnia / 10 lat)`, trendLine(tS), t.tmin)] : []),
            ...(tA ? [refDataset(`Trend jesień (${signed(tA.per_decade, 1)} dnia / 10 lat)`, trendLine(tA), t.et0)] : []),
          ],
        },
        options: {
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: Object.assign(baseLegend(t), { labels: Object.assign(baseLegend(t).labels, { font: { family: t.font, size: 11 }, boxWidth: 10 }) }),
            tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${doyLabel(c.parsed.y)}` } } },
          scales: Object.assign(baseScales(t), {
            x: Object.assign(baseScales(t).x, { ticks: { color: t.muted, maxTicksLimit: 8, maxRotation: 0 } }),
            y: Object.assign(baseScales(t).y, { ticks: { color: t.muted, callback: (v) => doyLabel(v) } }),
          }),
        },
      }),
    }),
  ]);
}

// --- bieżący sezon (od 1 kwietnia) vs średnia 1991–2020 i rok poprzedni -----------------------------------------------
function seasonChart(d, key) {
  const s = d.season;
  if (!s || !s.current || !s.normal || !s.normal[key] || !s.normal[key].length) return null;
  const cur = s.current[key] || [];
  const prev = (s.previous && s.previous[key]) || [];
  const norm = s.normal[key];
  const n = Math.max(cur.length, 1) - 1;
  const isG = key === 'gdd';
  const unit = isG ? '°C·dni' : 'mm';
  const diff = cur.length ? cur[n] - norm[n] : null;
  const labels = s.days_since_apr1.map((md) => { const [m, dd] = md.split('-'); return `${Number(dd)} ${MONTHS[Number(m) - 1]}`; });
  const when = n >= 182 ? 'cały sezon IV–IX' : `stan na ${labels[n]}`;
  const summary = cur.length
    ? `${s.year}, ${when}: ${num(cur[n], 0)} ${unit} wobec średnio ${num(norm[n], 0)} ${unit} (${signed(diff, 0)} ${unit}).`
    : 'Sezon jeszcze się nie zaczął (liczymy od 1 kwietnia).';
  return h('section', { class: 'mr-climate__wide' }, [
    h('h3', { class: 'mr-gallery__title', text: isG ? `Ciepło w sezonie ${s.year} – stopniodni > 10°C narastająco od 1 kwietnia` : `Woda w sezonie ${s.year} – bilans wodny narastająco od 1 kwietnia` }),
    h('p', { class: `mr-callout mr-callout--${isG ? 'info' : (diff !== null && diff < -50 ? 'warn' : 'info')}` }, [icon(isG ? 'sun' : 'drop'), summary]),
    chartFigure({
      title: isG ? 'Stopniodni > 10°C narastająco' : 'Klimatyczny bilans wodny narastająco',
      summary, chip: [provChip(d.provenance)],
      note: isG ? `Linia przerywana – średnia ${d.normal_period}. Wyżej niż średnia = sezon cieplejszy, rośliny rozwijają się szybciej.`
        : `Linia przerywana – średnia ${d.normal_period}. Niżej niż średnia = większy niedobór wody niż zwykle (ryzyko suszy).`,
      columns: [{ label: 'Dzień' }, { label: String(s.year) }, { label: String(s.previous_year) }, { label: `Średnia ${d.normal_period}` }],
      rows: labels.filter((_, i) => i % 7 === 0).map((l, k) => { const i = k * 7; return [l, isNum(cur[i]) ? num(cur[i], 0) : '–', isNum(prev[i]) ? num(prev[i], 0) : '–', num(norm[i], 0)]; }),
      build: (Chart, canvas, t) => ({
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: String(s.year), data: cur, borderColor: isG ? t.tmax : t.rain, backgroundColor: isG ? t.tmax : t.rain, pointRadius: 0, borderWidth: 3, order: 1 },
            { label: String(s.previous_year), data: prev, borderColor: t.muted, backgroundColor: t.muted, pointRadius: 0, borderWidth: 1.5, order: 2 },
            refDataset(`Średnia ${d.normal_period}`, norm, t.ref),
          ],
        },
        options: {
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: baseLegend(t) },
          scales: Object.assign(baseScales(t, unit), { x: Object.assign(baseScales(t).x, { ticks: { color: t.muted, maxTicksLimit: 7, maxRotation: 0 } }) }),
        },
      }),
    }),
  ]);
}

// --- plony GUS vs susza (bilans wodny IV–VII) ---------------------------------------------------------------------------
function pearson(pts) {
  if (pts.length < 4) return null;
  const n = pts.length; const mx = pts.reduce((a, p) => a + p.x, 0) / n; const my = pts.reduce((a, p) => a + p.y, 0) / n;
  let sxy = 0; let sxx = 0; let syy = 0;
  pts.forEach((p) => { sxy += (p.x - mx) * (p.y - my); sxx += (p.x - mx) ** 2; syy += (p.y - my) ** 2; });
  return sxx && syy ? sxy / Math.sqrt(sxx * syy) : null;
}

function yieldDroughtItem(g, d, y) {
  const kbw = new Map(d.years.map((r) => [r.year, r.kbw_apr_jul_mm]));
  const pts = (y.years || []).filter((r) => isNum(r.voivodeship) && isNum(kbw.get(r.year))).map((r) => ({ x: kbw.get(r.year), y: r.voivodeship, year: r.year }));
  if (pts.length < 4) return h('li', { class: 'mr-gallery__item' }, [h('h3', { class: 'mr-gallery__title', text: `${g.crop}: plon a susza` }), h('p', { class: 'mr-muted', text: 'Za mało lat z danymi.' })]);
  const r = pearson(pts);
  const verdict = r === null ? '' : r > 0.5 ? 'silna zależność – w suchych latach plon wyraźnie spada' : r > 0.25 ? 'umiarkowana zależność od wody' : 'słaba zależność w tych latach';
  const vname = (y.voivodeship && y.voivodeship.name) || g.voiv || 'województwo';
  return h('li', { class: 'mr-gallery__item' }, [
    h('h3', { class: 'mr-gallery__title', text: `${g.crop}: plon a susza` }),
    h('p', { class: 'mr-gallery__kpi' }, [h('strong', { text: r === null ? '–' : `r = ${num(r, 2)}` }), ` ${verdict}`]),
    h('p', { class: 'mr-gallery__sub', text: `Każdy punkt to rok: plon w woj. ${vname} (GUS) wobec bilansu wodnego IV–VII w miejscu pola (ERA5).` }),
    chartFigure({
      title: `${y.gus_crop}: plon (GUS) a bilans wodny IV–VII`,
      summary: `Korelacja r = ${r === null ? 'brak' : num(r, 2)} dla ${pts.length} lat.`,
      chip: [provChip(y.provenance), provChip(d.provenance)],
      columns: [{ label: 'Rok' }, { label: 'Bilans IV–VII [mm]' }, { label: 'Plon [dt/ha]' }],
      rows: pts.map((p) => [String(p.year), num(p.x, 0), num(p.y, 1)]),
      build: (Chart, canvas, t) => ({
        type: 'scatter',
        data: {
          datasets: [
            { label: 'Rok (plon vs bilans)', data: pts, backgroundColor: t.balance, borderColor: t.balance, pointRadius: 5 },
            ...(isNum(d.normals.kbw_apr_jul_mm) ? [{ type: 'line', label: `Średni bilans ${d.normal_period}`, data: [{ x: d.normals.kbw_apr_jul_mm, y: Math.min(...pts.map((p) => p.y)) }, { x: d.normals.kbw_apr_jul_mm, y: Math.max(...pts.map((p) => p.y)) }],
              borderColor: t.ref, borderDash: [7, 5], pointRadius: 0, borderWidth: 2, pointStyle: 'line' }] : []),
          ],
        },
        options: {
          plugins: { legend: Object.assign(baseLegend(t), { labels: Object.assign(baseLegend(t).labels, { font: { family: t.font, size: 11 }, boxWidth: 10 }) }),
            tooltip: { callbacks: { label: (c) => (c.raw && c.raw.year ? `${c.raw.year}: bilans ${num(c.raw.x, 0)} mm, plon ${num(c.raw.y, 1)} dt/ha` : c.dataset.label) } } },
          scales: {
            x: { type: 'linear', title: { display: true, text: 'bilans wodny IV–VII [mm] (← susza)', color: t.muted }, ticks: { color: t.muted }, grid: { color: t.grid } },
            y: { title: { display: true, text: 'plon [dt/ha]', color: t.muted }, ticks: { color: t.muted }, grid: { color: t.grid } },
          },
        },
      }),
    }),
  ]);
}

/** Sekcja klimatyczna dla wybranej działki. groups – uprawy na działkach (do wykresów plon vs susza). */
export function climateSection(p, groups, token, isCurrent) {
  const body = h('div', { 'aria-busy': 'true' }, [h('p', { class: 'mr-muted', role: 'status', text: 'Liczenie wskaźników klimatycznych od 1991 r.…' }), skeleton(4)]);
  const card = h('section', { class: 'mr-card mr-wide', 'aria-labelledby': 'mr-clim-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-clim-h' }, [icon('sun'), `Klimat pola: co się zmienia (1991 – dziś) – ${p.id_tag || 'działka'}`]),
    body,
  ]);
  const c = Array.isArray(p.centroid) ? p.centroid : null;
  if (!c) { replace(body, h('p', { class: 'mr-muted', text: 'Brak położenia działki.' })); return card; }
  load(c[0], c[1]).then((d) => {
    if (!isCurrent(token)) return;
    body.removeAttribute('aria-busy');
    if (!Array.isArray(d.years) || !d.years.length) {
      replace(body, [h('p', { class: 'mr-muted', text: (d.provenance && d.provenance.note) || 'Brak danych klimatycznych.' }), provChip(d.provenance)]);
      return;
    }
    const tr = d.trends || {};
    const facts = [
      tr.t_mean_c ? `temperatura ${signed(tr.t_mean_c.per_decade, 2)}°C na 10 lat` : null,
      tr.hot_days ? `dni upalnych ${signed(tr.hot_days.per_decade, 1)} na 10 lat` : null,
      tr.kbw_apr_sep_mm ? `bilans wodny IV–IX ${signed(tr.kbw_apr_sep_mm.per_decade, 0)} mm na 10 lat` : null,
      tr.season_days ? `okres wegetacji ${signed(tr.season_days.per_decade, 1)} dnia na 10 lat` : null,
    ].filter(Boolean);
    const droughtList = h('ul', { class: 'mr-gallery' });
    replace(body, [
      h('p', { class: 'mr-callout mr-callout--info' }, [icon('info'), `W miejscu pola od ${d.years[0].year} r.: ${facts.join(', ')}. `
        + 'Linie przerywane: trend (czerwona) i średnia 1991–2020 (szara).']),
      h('div', { class: 'mr-climate__seasons' }, [seasonChart(d, 'kbw'), seasonChart(d, 'gdd')]),
      h('h3', { class: 'mr-metric__title', text: 'Jak zmienia się klimat w miejscu pola' }),
      h('ul', { class: 'mr-gallery' }, [...SPECS.map((sp) => yearChart(d, sp)), frostChart(d)]),
      groups.length ? h('h3', { class: 'mr-metric__title', text: 'Plony a susza – które uprawy najbardziej cierpią w suchych latach' }) : null,
      groups.length ? droughtList : null,
      h('p', { class: 'mr-hint', text: 'Dane pogodowe: reanaliza ERA5 (model z pomiarami, siatka ~25 km) – trendy i różnice między latami są wiarygodne, lokalne przymrozki w obniżeniach terenu mogą być silniejsze. Plony: GUS BDL (województwo).' }),
      provChip(d.provenance),
    ]);
    groups.forEach((g) => {
      const li = h('li', { class: 'mr-gallery__item' }, [h('p', { class: 'mr-muted', text: `${g.crop}: wczytywanie plonów GUS…` })]);
      droughtList.appendChild(li);
      yieldReference(g.crop, g.voiv, g.centroid).then((y) => {
        if (!isCurrent(token)) return;
        if (!y || !y.available) { replace(li, [h('h3', { class: 'mr-gallery__title', text: `${g.crop}: plon a susza` }), h('p', { class: 'mr-muted', text: (y && y.note) || 'Brak danych GUS.' })]); return; }
        li.replaceWith(yieldDroughtItem(g, d, y));
      }).catch((e) => replace(li, h('p', { class: 'mr-muted', text: `${g.crop}: ${e.message}` })));
    });
  }).catch((e) => {
    if (!isCurrent(token)) return;
    body.removeAttribute('aria-busy');
    replace(body, h('p', { class: 'mr-muted', text: `Nie udało się pobrać danych klimatycznych: ${e.message}` }));
  });
  return card;
}

// Karta „Przepisy i progi zabiegów” (zakładka Plony): progi z aktów prawnych zaznaczone na wykresach prognozy.
// - opryski: dopuszczalna prędkość wiatru (§ 3 rozp. MRiRW, Dz.U. 2014 poz. 516 w brzmieniu Dz.U. 2026 poz. 353),
// - nawożenie azotem: terminy i progi temperatury z programu azotanowego (Dz.U. 2023 poz. 244).
import { h, icon, replace } from '../dom.js';
import { api } from '../api.js';
import { num, provChip, fmtDay } from '../format.js';
import { chartFigure, baseScales, baseLegend, refDataset, constLine } from '../charts.js';
import { skeleton } from './field.js';

const cache = new Map();
const MONTHS = ['sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru'];

function load(p) {
  const c = Array.isArray(p.centroid) ? p.centroid : null;
  if (!c) return Promise.reject(new Error('Brak położenia działki.'));
  const qs = new URLSearchParams({ lat: String(c[0]), lon: String(c[1]) });
  if (p.parcel_id) qs.set('parcel_id', p.parcel_id);
  if (p.crop) qs.set('crop', p.crop);
  const key = qs.toString();
  if (!cache.has(key)) cache.set(key, api(`/api/reference/rules?${key}`, { timeout: 30000 }).catch((e) => { cache.delete(key); throw e; }));
  return cache.get(key);
}

function isNum(v) { return v !== null && v !== undefined && Number.isFinite(Number(v)); }
function dayOfYear(iso) { const d = new Date(`${iso}T12:00:00`); const s = new Date(d.getFullYear(), 0, 1); return (d - s) / 864e5; }
function fmtHour(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : new Intl.DateTimeFormat('pl-PL', { weekday: 'short', day: 'numeric', hour: '2-digit' }).format(d);
}

/** Karta dla wybranej działki (pierwszej z uprawą, jeśli nie wskazano). */
export function rulesCard(p, token, isCurrent) {
  const body = h('div', { 'aria-busy': 'true' }, [h('p', { class: 'mr-muted', role: 'status', text: 'Wczytywanie przepisów i prognozy…' }), skeleton(3)]);
  const card = h('section', { class: 'mr-card mr-wide', 'aria-labelledby': 'mr-rules-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-rules-h' }, [icon('checkCircle'), `Przepisy i progi zabiegów – ${p.id_tag || 'działka'}${p.crop ? ` (${p.crop})` : ''}`]),
    h('p', { class: 'mr-hint', text: 'Linie przerywane na wykresach to progi z przepisów (Dziennik Ustaw). Wiążący jest tekst aktu i etykieta środka – to pomoc w planowaniu.' }),
    body,
  ]);
  load(p).then((d) => {
    if (!isCurrent(token)) return;
    body.removeAttribute('aria-busy');
    replace(body, [nitratesBlock(d.nitrates), h('div', { class: 'mr-rulesgrid' }, [windChart(d.spraying), tempChart(d.temperature, d.nitrates)]),
      sowingBlock(d.sowing, p.crop)]);
  }).catch((e) => {
    if (!isCurrent(token)) return;
    body.removeAttribute('aria-busy');
    replace(body, h('p', { class: 'mr-muted', text: `Nie udało się pobrać danych: ${e.message}` }));
  });
  return card;
}

// --- kalendarz nawożenia azotem (oś roku z oknami i znacznikiem „dziś”) ------------------------------------------
function nitratesBlock(n) {
  if (!n) return null;
  const cls = { dozwolone: 'ok', tylko_stale: 'warn', luty_warunkowo: 'warn', zakaz: 'bad' }[n.status] || 'info';
  const track = h('div', { class: 'mr-yearbar__track', 'aria-hidden': 'true' });
  (n.windows || []).forEach((w, i) => {
    const a = dayOfYear(w.from); const b = dayOfYear(w.to) + 1;
    const seg = h('span', { class: `mr-yearbar__win mr-yearbar__win--${i}${w.conditional ? ' mr-yearbar__win--cond' : ''}` });
    seg.style.left = `${(a / 365) * 100}%`;
    seg.style.width = `${((b - a) / 365) * 100}%`;
    track.appendChild(seg);
  });
  const today = h('span', { class: 'mr-yearbar__today' }, h('span', { text: 'dziś' }));
  today.style.left = `${(dayOfYear(n.today) / 365) * 100}%`;
  track.appendChild(today);
  return h('div', { class: 'mr-nitr' }, [
    h('h3', { class: 'mr-metric__title', text: 'Nawożenie azotem – terminy z programu azotanowego' }),
    h('p', { class: `mr-callout mr-callout--${cls === 'bad' ? 'warn' : cls === 'ok' ? 'ok' : 'info'}` }, [icon(cls === 'ok' ? 'checkCircle' : 'warn'), n.text]),
    h('div', { class: 'mr-yearbar' }, [
      track,
      h('div', { class: 'mr-yearbar__months', 'aria-hidden': 'true' }, MONTHS.map((m) => h('span', { text: m }))),
    ]),
    h('ul', { class: 'mr-yearbar__legend' }, (n.windows || []).map((w, i) => h('li', null, [
      h('span', { class: `mr-yearbar__key mr-yearbar__win--${i}${w.conditional ? ' mr-yearbar__win--cond' : ''}`, 'aria-hidden': 'true' }),
      `${w.label}: ${fmtShort(w.from)} – ${fmtShort(w.to)}`,
    ]))),
    h('p', { class: 'mr-hint', text: `${n.deadline_basis}. Zakaz stosowania nawozów na glebie zamarzniętej, zalanej, nasyconej wodą lub przykrytej śniegiem obowiązuje zawsze.` }),
    provChip(n.provenance),
  ]);
}

function fmtShort(iso) { const d = new Date(`${iso}T12:00:00`); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; }

// --- wiatr godzinowy vs dopuszczalna prędkość przy opryskach ----------------------------------------------------
function windChart(s) {
  const rows = Array.isArray(s && s.hourly) ? s.hourly.filter((r) => isNum(r.wind_ms)) : [];
  if (!rows.length) return h('div', null, [h('p', { class: 'mr-muted', text: 'Brak prognozy wiatru.' }), s ? provChip(s.forecast_provenance) : null]);
  const lim = s.wind_max_ms; const lim2 = s.wind_max_drift_reduction_ms;
  const day = rows.filter((r) => r.daylight);
  const okH = day.filter((r) => r.allowed === 'tak').length;
  const summary = `W ciągu 5 dni ${okH} z ${day.length} godzin dziennych (6–20) ma prognozowany wiatr do ${num(lim, 0)} m/s.`;
  return chartFigure({
    title: 'Wiatr a opryski – prognoza 5 dni',
    summary,
    chip: [provChip(s.provenance), provChip(s.forecast_provenance)],
    note: `${summary} Linie przerywane: ${num(lim, 0)} m/s – limit dla oprysku sprzętem naziemnym; ${num(lim2, 0)} m/s – przy technice ograniczającej znoszenie cieczy o ≥75% (§ 3 rozporządzenia MRiRW, Dz.U. 2014 poz. 516 w brzmieniu z 2026 r.).`,
    columns: [{ label: 'Godzina' }, { label: 'Wiatr 10 m [m/s]' }, { label: 'Porywy [m/s]' }, { label: 'Oprysk dozwolony?' }],
    rows: rows.map((r) => [fmtHour(r.time), num(r.wind_ms, 1), isNum(r.gust_ms) ? num(r.gust_ms, 1) : '–',
      r.allowed === 'tak' ? 'tak' : r.allowed === 'technika_75' ? `tylko z techniką ≥75% (do ${num(lim2, 0)} m/s)` : 'nie']),
    build: (Chart, canvas, t) => ({
      type: 'line',
      data: {
        labels: rows.map((r) => fmtHour(r.time)),
        datasets: [
          { label: 'Wiatr [m/s]', data: rows.map((r) => r.wind_ms), borderColor: t.rain, backgroundColor: t.rain, pointRadius: 0, borderWidth: 2, tension: 0.3, order: 1,
            segment: { borderColor: (c) => (c.p1.parsed.y > lim ? t.tmax : t.rain) } },
          { label: 'Porywy [m/s]', data: rows.map((r) => r.gust_ms), borderColor: t.rainForecast, backgroundColor: t.rainForecast, pointRadius: 0, borderWidth: 1, tension: 0.3, order: 2 },
          refDataset(`Limit oprysku ${num(lim, 0)} m/s (rozp. MRiRW)`, constLine(rows.length, lim), t.tmax),
          refDataset(`${num(lim2, 0)} m/s – technika ograniczająca znoszenie ≥75%`, constLine(rows.length, lim2), t.warn, { borderDash: [3, 4] }),
        ],
      },
      options: {
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: baseLegend(t) },
        scales: Object.assign(baseScales(t, 'm/s'), { x: Object.assign(baseScales(t).x, { ticks: { color: t.muted, maxTicksLimit: 6, maxRotation: 0 } }),
          y: Object.assign(baseScales(t, 'm/s').y, { beginAtZero: true }) }),
      },
    }),
  });
}

// --- średnia dobowa temperatura vs progi 3°C / 5°C programu azotanowego ---------------------------------------------
function tempChart(tm, n) {
  const rows = Array.isArray(tm && tm.daily) ? tm.daily.filter((r) => r && r.date) : [];
  if (!rows.length) return h('div', null, [h('p', { class: 'mr-muted', text: 'Brak danych o temperaturze.' }), tm ? provChip(tm.provenance) : null]);
  const thr = tm.threshold_c;
  const today = n ? n.today : null;
  const run = tm.first_5day_run_start;
  const feb = today && today.slice(5, 7) === '02';
  const summary = run
    ? `Pierwsza 5-dniowa seria ze średnią dobową powyżej ${num(thr, 0)}°C zaczyna się ${fmtDay(run)}.`
    : `W tym okresie nie ma 5 kolejnych dni ze średnią dobową powyżej ${num(thr, 0)}°C.`;
  return chartFigure({
    title: 'Średnia dobowa temperatura – progi programu azotanowego',
    summary,
    chip: [provChip(tm.provenance), n ? provChip(n.provenance) : null],
    note: `${summary} Progi: 3°C (ozime, uprawy trwałe i wieloletnie, TUZ) i 5°C (pozostałe uprawy) – przez 5 kolejnych dni pozwalają nawozić azotem od 1 lutego (pkt 4–6 programu azotanowego; datę dla powiatu podaje IMGW-PIB).`
      + (feb ? '' : ' Poza lutym te progi nie zmieniają terminów nawożenia – pokazujemy je informacyjnie.'),
    columns: [{ label: 'Dzień' }, { label: 'Średnia dobowa [°C]' }, { label: 'Minimalna [°C]' }],
    rows: rows.map((r) => [`${fmtDay(r.date)}${today && r.date > today ? ' (p)' : ''}`, isNum(r.t_mean_c) ? num(r.t_mean_c, 1) : '–', isNum(r.t_min_c) ? num(r.t_min_c, 1) : '–']),
    build: (Chart, canvas, t) => ({
      type: 'line',
      data: {
        labels: rows.map((r) => `${fmtDay(r.date)}${today && r.date > today ? ' (p)' : ''}`),
        datasets: [
          { label: 'Średnia dobowa [°C]', data: rows.map((r) => r.t_mean_c), borderColor: t.tmax, backgroundColor: t.tmax, pointRadius: 2, borderWidth: 2, tension: 0.3, order: 1 },
          { label: 'Minimalna [°C]', data: rows.map((r) => r.t_min_c), borderColor: t.tmin, backgroundColor: t.tmin, pointRadius: 0, borderWidth: 1, tension: 0.3, order: 2 },
          refDataset('Próg 3°C (ozime, TUZ) – program azotanowy', constLine(rows.length, 3), t.balance),
          refDataset('Próg 5°C (pozostałe uprawy) – program azotanowy', constLine(rows.length, 5), t.warn, { borderDash: [3, 4] }),
        ],
      },
      options: {
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: baseLegend(t) },
        scales: Object.assign(baseScales(t, '°C'), { x: Object.assign(baseScales(t).x, { ticks: { color: t.muted, maxTicksLimit: 7, maxRotation: 0 } }) }),
      },
    }),
  });
}

// --- temperatura gleby a siew: progi ZALECANE przez doradztwo rolnicze (ODR), każdy z nazwą źródła i linkiem ---------
function sowingBlock(sw, crop) {
  if (!sw) return null;
  const rows = Array.isArray(sw.daily) ? sw.daily : [];
  const th = Array.isArray(sw.thresholds) ? sw.thresholds : [];
  const mine = sw.crop_threshold;
  const colors = ['#b35c00', '#6b4fa0', '#1d6b34'];
  const sources = h('ul', { class: 'mr-sowsrc' }, th.map((t, i) => h('li', null, [
    h('span', { class: 'mr-sowsrc__key', 'aria-hidden': 'true' }),
    h('strong', { text: `${t.crop[0].toUpperCase()}${t.crop.slice(1)}: ${num(t.min_c, 0)}${t.max_c ? `–${num(t.max_c, 0)}` : ''}°C (${t.depth})` }),
    ` – ${t.text}. Źródło: `,
    h('a', { href: t.url, target: '_blank', rel: 'noopener noreferrer' }, [t.source, h('span', { class: 'mr-visually-hidden', text: ' (otwiera się w nowej karcie)' })]),
  ])));
  sources.querySelectorAll('.mr-sowsrc__key').forEach((k, i) => { k.style.borderTopColor = colors[i % colors.length]; });
  const summary = mine
    ? (sw.first_day_at_threshold ? `Dla uprawy „${crop}”: gleba osiąga ${num(mine.min_c, 0)}°C ${fmtDay(sw.first_day_at_threshold)} (prognoza).`
      : `Dla uprawy „${crop}”: w prognozie gleba nie osiąga zalecanych ${num(mine.min_c, 0)}°C – to nie jest jeszcze termin siewu.`)
    : `Dla uprawy „${crop || 'nieznana'}” nie mamy zweryfikowanego progu ODR – pokazujemy progi dla kukurydzy i soi.`;
  const chart = rows.length ? chartFigure({
    title: 'Temperatura gleby (6 cm) a zalecane progi siewu',
    summary,
    chip: [provChip(sw.soil_provenance), provChip(sw.provenance)],
    note: 'Linie przerywane – progi z zaleceń ośrodków doradztwa rolniczego (ODR), nie przepisy. Temperatura gleby z modelu dla komórki siatki – przed siewem zmierz ją na polu.',
    columns: [{ label: 'Dzień' }, { label: 'Temp. gleby 6 cm [°C]' }, ...th.map((t) => ({ label: `Próg ${t.crop} [°C]` }))],
    rows: rows.map((r) => [`${fmtDay(r.date)}${r.forecast ? ' (p)' : ''}`, num(r.soil_t_c, 1), ...th.map((t) => num(t.min_c, 0))]),
    build: (Chart, canvas, t) => ({
      type: 'line',
      data: {
        labels: rows.map((r) => `${fmtDay(r.date)}${r.forecast ? ' (p)' : ''}`),
        datasets: [
          { label: 'Temperatura gleby 6 cm [°C]', data: rows.map((r) => r.soil_t_c), borderColor: t.et0, backgroundColor: t.et0, pointRadius: 2, borderWidth: 2, tension: 0.3, order: 1 },
          ...th.flatMap((x, i) => {
            const own = mine && mine.crop === x.crop;
            const ds = [refDataset(`${x.crop}: min. ${num(x.min_c, 0)}°C – zalecenie ODR`, constLine(rows.length, x.min_c), colors[i % colors.length], { borderWidth: own ? 3 : 2 })];
            if (x.max_c) ds.push(refDataset(`${x.crop}: ${num(x.max_c, 0)}°C – górna granica zalecenia ODR`, constLine(rows.length, x.max_c), colors[i % colors.length], { borderDash: [3, 4], borderWidth: 1.5 }));
            return ds;
          }),
        ],
      },
      options: {
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: baseLegend(t) },
        scales: Object.assign(baseScales(t, '°C'), { x: Object.assign(baseScales(t).x, { ticks: { color: t.muted, maxTicksLimit: 7, maxRotation: 0 } }) }),
      },
    }),
  }) : h('p', { class: 'mr-muted', text: 'Brak prognozy temperatury gleby.' });
  return h('div', { class: 'mr-sow' }, [
    h('h3', { class: 'mr-metric__title' }, ['Siew – temperatura gleby ', h('span', { class: 'mr-badge mr-badge--advice', text: 'zalecenie doradztwa (ODR)' })]),
    h('p', { class: `mr-callout mr-callout--${mine && sw.first_day_at_threshold ? 'ok' : 'info'}` }, [icon('leaf'), summary]),
    chart,
    sources,
    h('p', { class: 'mr-hint', text: sw.not_found_note || '' }),
  ]);
}

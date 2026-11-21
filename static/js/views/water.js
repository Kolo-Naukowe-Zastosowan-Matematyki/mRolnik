// Zakładka „Woda”: zalecenie dla zastawek (infografika), bilans wodny (wykres), wilgotność gleby (wskaźnik),
// wodowskaz IMGW (poziom + trend), sieć melioracyjna (warstwa mapy) i ekoschemat (informacyjnie).
// Dane dla całego gospodarstwa: pogoda i wodowskaz dla środka gospodarstwa, wilgotność gleby osobno dla każdej działki.
// Każda sekcja ładuje się osobno, z limitem czasu i przyciskiem „Spróbuj ponownie”.
import { h, icon, replace, infoTip } from '../dom.js';
import { api } from '../api.js';
import { state, focusPoint } from '../state.js';
import { val, num, provChip, errorView, demoBadge, fmtDay, fmtDate } from '../format.js';
import { chartFigure, baseScales, baseLegend, refDataset, constLine } from '../charts.js';
import { renderNetwork } from '../map.js';
import { skeleton } from './field.js';
import { sectionize } from '../sections.js';

const TIMEOUT = 30000;
const cache = new Map();   // klucz punktu → {alerts, network, ecoscheme}
let renderToken = 0;

function fetchOnce(key, name, path) {
  const entry = cache.get(key) || {};
  if (!entry[name]) {
    entry[name] = api(path, { timeout: TIMEOUT }).catch((e) => { delete entry[name]; throw e; });
    cache.set(key, entry);
  }
  return entry[name];
}

/** Karta sekcji: szkielet od razu, potem treść albo błąd z ponowieniem. */
function asyncCard(id, title, ico, load, render, opts = {}) {
  const body = h('div', { class: 'mr-card__body', 'aria-busy': 'true' }, [h('p', { class: 'mr-muted', role: 'status', text: 'Wczytywanie…' }), skeleton(3)]);
  const card = h('section', { class: `mr-card${opts.wide ? ' mr-wide' : ''}`, 'aria-labelledby': id }, [
    h('h2', { class: 'mr-card__title', id }, [icon(ico), title, opts.badge || null]),
    body,
  ]);
  const run = () => {
    replace(body, [h('p', { class: 'mr-muted', role: 'status', text: 'Wczytywanie…' }), skeleton(3)]);
    body.setAttribute('aria-busy', 'true');
    load().then((data) => {
      body.removeAttribute('aria-busy');
      replace(body, render(data));
    }).catch((err) => {
      body.removeAttribute('aria-busy');
      replace(body, errorView(err, run, 'Brak danych'));
    });
  };
  run();
  return card;
}

const R_KM = 6371;
function distKm(a, b) {
  const r = (x) => (x * Math.PI) / 180;
  const dLat = r(b[0] - a[0]); const dLon = r(b[1] - a[1]);
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(r(a[0])) * Math.cos(r(b[0])) * Math.sin(dLon / 2) ** 2;
  return 2 * R_KM * Math.asin(Math.sqrt(x));
}
const FARM_SPREAD_KM = 30;   // dalej – działki w różnych regionach, wspólny „środek gospodarstwa” nie ma sensu

/** Punkt dla danych wspólnych (pogoda, bilans, wodowskaz, ekoschemat): działka najbliżej środka gospodarstwa
 *  (średnia ważona powierzchnią). Gdy działki leżą daleko od siebie – wybrana działka. */
function farmFocus() {
  const ps = (state.analysis && Array.isArray(state.analysis.parcels) ? state.analysis.parcels : [])
    .filter((p) => Array.isArray(p.centroid) && p.centroid.length === 2);
  const fp = focusPoint();
  if (ps.length < 2) return fp ? { ...fp, all: ps.length === 1, spread: false } : null;
  const w = (p) => (Number(p.area_ha) > 0 ? Number(p.area_ha) : 1);
  const W = ps.reduce((a, p) => a + w(p), 0);
  const c = [ps.reduce((a, p) => a + p.centroid[0] * w(p), 0) / W, ps.reduce((a, p) => a + p.centroid[1] * w(p), 0) / W];
  const far = ps.some((p) => distKm(p.centroid, c) > FARM_SPREAD_KM);
  if (far) return fp ? { ...fp, all: false, spread: true } : null;
  const near = ps.reduce((best, p) => (distKm(p.centroid, c) < distKm(best.centroid, c) ? p : best), ps[0]);
  return { lat: near.centroid[0], lon: near.centroid[1], parcel: near, all: true, spread: false };
}

export function renderWater(panel) {
  const fp = farmFocus();
  const token = ++renderToken;
  if (!fp) {
    replace(panel, h('div', { class: 'mr-state mr-state--empty' }, [icon('info', 'mr-state__ico'), h('p', { text: 'Wybierz lub dodaj pole, aby zobaczyć dane o wodzie.' })]));
    return;
  }
  const key = `${fp.lat.toFixed(4)},${fp.lon.toFixed(4)}`;
  const q = `lat=${encodeURIComponent(fp.lat)}&lon=${encodeURIComponent(fp.lon)}`;
  const parcel = fp.parcel;
  const pid = parcel && parcel.parcel_id ? `&parcel_id=${encodeURIComponent(parcel.parcel_id)}` : '';
  const alerts = () => fetchOnce(key, 'alerts', `/api/hydromelior/alerts?${q}`);
  const all = state.analysis && Array.isArray(state.analysis.parcels) ? state.analysis.parcels : [];
  const ctx = fp.all
    ? `Dane dla wszystkich działek (${all.length}): wilgotność gleby dla każdej działki, pogoda i wodowskaz dla środka gospodarstwa.`
    : fp.spread
      ? `Działki leżą daleko od siebie – pogoda i wodowskaz dla: ${parcel ? (parcel.id_tag || 'wybrana działka') : 'wybranego punktu'} (wybierz inną działkę na liście). Wilgotność gleby – dla każdej działki.`
      : `Dane dla: ${parcel ? (parcel.id_tag || 'wybrana działka') : `${num(fp.lat, 4)}, ${num(fp.lon, 4)}`}`;

  replace(panel, [
    h('p', { class: 'mr-context' }, [icon('pin'), ctx]),
    // rząd: zastawki | woda w glebie (szeroko), potem wykresy na pełną szerokość
    asyncCard('mr-gate-h', 'Zastawki – co zrobić teraz', 'drop', alerts, (d) => gateGraphic(d)),
    soilCard(all),
    asyncCard('mr-cwb-h', 'Bilans wodny', 'cloud', alerts, (d) => waterBalance(d), { wide: true }),
    asyncCard('mr-river-h', 'Najbliższy wodowskaz', 'layers', alerts, (d) => riverGauge(d && d.river_gauge), { wide: true }),
    asyncCard('mr-eco-h', 'Ekoschemat', 'checkCircle', () => fetchOnce(key + pid, 'eco', `/api/hydromelior/ecoscheme?${q}${pid}`), (d) => ecoscheme(d, `/api/hydromelior/ecoscheme-download?${q}${pid}`), { wide: true }),
  ]);
  sectionize(panel, [{ id: 'mr-cwb-h', label: 'Bilans wodny' }, { id: 'mr-river-h', label: 'Najbliższy wodowskaz' }, { id: 'mr-eco-h', label: 'Ekoschemat' }],
    'Sekcje zakładki Woda');
  // Sieć melioracyjna: bez osobnego kafelka – obiekty (np. przykładowe zastawki) trafiają tylko na warstwę mapy,
  // a cieki i rowy z BDOT10k włącza przycisk „Cieki i rowy” nad mapą.
  fetchOnce(key, 'network', `/api/hydromelior/network?${q}`).then((d) => { if (token === renderToken) renderNetwork(d); }).catch(() => {});
}

// --- zastawki: 3 kroki, aktualne zalecenie wyróżnione --------------------------------------
const GATES = [
  { key: 'zamknieta', label: 'Zamknięta', desc: 'Zatrzymuj wodę w rowie (susza, ujemny bilans).', level: 0.85 },
  { key: 'przymknieta', label: 'Przymknięta', desc: 'Utrzymuj stały poziom, przepuszczaj nadmiar.', level: 0.55 },
  { key: 'otwarta', label: 'Otwarta', desc: 'Odprowadzaj nadmiar wody (mokro, roztopy).', level: 0.25 },
];

function normGate(v) {
  const s = String(v || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace('ł', 'l');
  if (s.startsWith('zamk')) return 'zamknieta';
  if (s.startsWith('przym') || s.startsWith('pol')) return 'przymknieta';
  if (s.startsWith('otw')) return 'otwarta';
  return null;
}

function gateRecommendation(d) {
  const rec = d && (d.gate_recommendation || d.sluice_recommendation);
  if (rec && normGate(rec.position || rec.status)) {
    return { pos: normGate(rec.position || rec.status), reason: rec.reason || rec.text || '', prov: rec.provenance || null };
  }
  // Brak zalecenia z API: orientacyjny szacunek z klimatycznego bilansu wodnego (wyraźnie oznaczony).
  const w = state.analysis && state.analysis.weather;
  const cwb = w ? w.climatic_water_balance_14d_mm : null;
  if (cwb === null || cwb === undefined) return null;
  const pos = cwb < -15 ? 'zamknieta' : cwb > 15 ? 'otwarta' : 'przymknieta';
  return {
    pos,
    reason: `Bilans wodny 14 dni: ${cwb > 0 ? '+' : ''}${num(cwb, 0)} mm.`,
    prov: { source: 'Szacunek mRolnik (bilans wodny)', kind: 'szacunek', fetched_at: state.analysis.generated_at, note: 'Reguła: bilans < −15 mm → zamknij, > +15 mm → otwórz, pomiędzy → przymknij. Sprawdź stan wody w rowie przed zmianą.' },
  };
}

function gateSvg(level, open) {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 80 60');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', 'mr-gate__svg');
  const el = (tag, attrs) => { const e = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); svg.appendChild(e); return e; };
  el('path', { d: 'M4 8v46h72V8', class: 'mr-gate__bank' });
  el('rect', { x: 6, y: 54 - 44 * level, width: 30, height: 44 * level, class: 'mr-gate__water' });
  el('rect', { x: 44, y: 46, width: 30, height: 8, class: 'mr-gate__water' });
  const boardH = 44 * (1 - open);
  el('rect', { x: 36, y: 54 - boardH - 2, width: 8, height: boardH + 2, class: 'mr-gate__board' });
  el('rect', { x: 35, y: 4, width: 10, height: 4, class: 'mr-gate__board' });
  return svg;
}

function gateGraphic(d) {
  const rec = gateRecommendation(d);
  return [
    h('ol', { class: 'mr-gates' }, GATES.map((g, i) => {
      const on = rec && rec.pos === g.key;
      return h('li', { class: `mr-gate${on ? ' mr-gate--on' : ''}`, 'aria-current': on ? 'true' : null }, [
        gateSvg(g.level, [0, 0.45, 0.85][i]),
        h('p', { class: 'mr-gate__label' }, [on ? icon('check') : null, g.label]),
        on ? h('p', { class: 'mr-gate__tag', text: 'Zalecane teraz' }) : null,
        h('p', { class: 'mr-gate__desc', text: g.desc }),
      ]);
    })),
    rec ? h('p', { class: 'mr-gate__reason' }, [h('strong', { text: `Zalecenie: ${GATES.find((g) => g.key === rec.pos).label.toLowerCase()}. ` }), rec.reason]) :
      h('p', { class: 'mr-muted', text: 'Brak danych do zalecenia (brak bilansu wodnego dla tego miejsca).' }),
    rec && rec.prov ? provChip(rec.prov) : null,
  ];
}

// --- bilans wodny ---------------------------------------------------------------------------
function waterBalance(d) {
  let rows = Array.isArray(d && d.water_balance) ? d.water_balance : null;
  let prov = d && (d.water_balance_provenance || (d.provenance && d.provenance.water_balance));
  if (!rows || !rows.length) {
    const w = state.analysis && state.analysis.weather;
    rows = w && Array.isArray(w.daily) ? w.daily.map((x) => ({ date: x.date, precip_mm: x.rain_mm, et0_mm: x.et0_mm, kind: 'forecast' })) : [];
    prov = w && w.provenance ? w.provenance.forecast : null;
  }
  rows = rows.filter((r) => r && r.date);
  if (!rows.length) return h('p', { class: 'mr-muted', text: 'Brak danych o opadach i parowaniu dla tego miejsca.' });
  let cum = 0;
  const cumul = rows.map((r) => { cum += (Number(r.precip_mm) || 0) - (Number(r.et0_mm) || 0); return Math.round(cum * 10) / 10; });
  let cp = 0;
  const cumPrecip = rows.map((r) => { cp += Number(r.precip_mm) || 0; return Math.round(cp * 10) / 10; });
  const climate = d && d.reference && d.reference.climate_normals;
  const refRows = new Map((d && d.reference && Array.isArray(d.reference.water_balance) ? d.reference.water_balance : []).map((x) => [x.date, x]));
  const cumNormal = rows.map((r) => { const x = refRows.get(r.date); return x && x.cum_precip_normal_mm !== null && x.cum_precip_normal_mm !== undefined ? Number(x.cum_precip_normal_mm) : null; });
  const hasRef = cumNormal.some((v) => v !== null);
  const lastNormal = hasRef ? cumNormal[cumNormal.length - 1] : null;
  const labels = rows.map((r) => `${fmtDay(r.date)}${r.kind === 'obs' ? '' : ' (p)'}`);
  const obsCount = rows.filter((r) => r.kind === 'obs').length;
  const summary = (cum >= 0 ? `Woda przybywa: +${num(cum, 0)} mm w całym okresie.` : `Woda ubywa: ${num(cum, 0)} mm w całym okresie.`)
    + (lastNormal !== null ? ` Opad ${num(cp, 0)} mm wobec normy ${num(lastNormal, 0)} mm dla tych dni (${num(lastNormal ? (cp / lastNormal) * 100 : 0, 0)}% normy IMGW-PIB 1991–2020).` : '');
  return [
    h('p', { class: `mr-callout ${cum >= 0 ? 'mr-callout--ok' : 'mr-callout--warn'}` }, [icon(cum >= 0 ? 'drop' : 'warn'), summary]),
    chartFigure({
      title: `Opad i parowanie dzień po dniu${obsCount ? ` (${obsCount} dni wstecz + prognoza)` : ' (prognoza)'}`,
      summary,
      chip: [prov ? provChip(prov) : null, climate ? provChip(climate.provenance) : null],
      note: hasRef ? `Linia przerywana – opad narastająco według normy 1991–2020 ze stacji IMGW-PIB ${climate.station} (${num(climate.distance_km, 0)} km) dla tych samych dni; porównaj z linią „Opad narastająco”.` : null,
      columns: [{ label: 'Dzień' }, { label: 'Opad [mm]' }, { label: 'Parowanie ET₀ [mm]' }, { label: 'Bilans narastająco [mm]' },
        { label: 'Opad narastająco [mm]' }, ...(hasRef ? [{ label: 'Norma opadu narastająco 1991–2020 [mm]' }] : []), { label: 'Rodzaj' }],
      rows: rows.map((r, i) => [fmtDay(r.date), val(r.precip_mm, '', 1), val(r.et0_mm, '', 1), num(cumul[i], 1), num(cumPrecip[i], 1),
        ...(hasRef ? [val(cumNormal[i], '', 1)] : []), r.kind === 'obs' ? 'analiza modelu' : 'prognoza']),
      build: (Chart, canvas, t) => ({
        type: 'bar',
        data: {
          labels,
          datasets: [
            { label: 'Opad [mm]', data: rows.map((r) => r.precip_mm), backgroundColor: rows.map((r) => (r.kind === 'obs' ? t.rain : t.rainForecast)), borderRadius: 3, order: 2 },
            { label: 'Parowanie [mm]', data: rows.map((r) => (r.et0_mm === null || r.et0_mm === undefined ? null : -Number(r.et0_mm))), backgroundColor: t.et0, borderRadius: 3, order: 2 },
            { type: 'line', label: 'Bilans narastająco [mm]', data: cumul, borderColor: t.balance, backgroundColor: t.balance, tension: 0.25, pointRadius: 2, order: 1, yAxisID: 'y1', mrKind: 'balance' },
            { type: 'line', label: 'Opad narastająco [mm]', data: cumPrecip, borderColor: t.rain, backgroundColor: t.rain, tension: 0.25, pointRadius: 0, borderWidth: 2, order: 1, yAxisID: 'y1' },
            ...(hasRef ? [refDataset('Norma opadu narastająco 1991–2020 (IMGW-PIB) [mm]', cumNormal, t.ref, { yAxisID: 'y1' })] : []),
          ],
        },
        options: {
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: baseLegend(t), tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${num(Math.abs(c.parsed.y), 1)}${c.dataset.mrKind === 'balance' ? ` (${c.parsed.y >= 0 ? 'nadwyżka' : 'niedobór'})` : ''}` } } },
          scales: {
            ...baseScales(t, 'mm/dobę'),
            y1: { position: 'right', ticks: { color: t.muted }, grid: { display: false }, title: { display: true, text: 'mm narastająco', color: t.muted } },
          },
        },
      }),
    }),
    h('p', { class: 'mr-hint', text: '(p) – prognoza; jaśniejsze słupki opadu to prognoza. Słupki w dół to woda, która wyparuje z gleby i roślin. Oś prawa: zielona linia – bilans narastająco, niebieska – opad narastająco, szara przerywana – opad narastająco według normy wieloletniej.' }),
  ];
}

// --- wilgotność gleby: wszystkie działki, najświeższy stan z modelu ----------------------------------------------
const byName = new Intl.Collator('pl', { numeric: true, sensitivity: 'base' });
const SOIL_STATE = { dry: 'woda niedostępna', low: 'niedobór', ok: 'optymalnie', wet: 'nadmiar', none: 'brak danych' };

function soilZone(v, fc, wp) {
  if (v === null || fc === null || wp === null) return 'none';
  if (v <= wp) return 'dry';
  if (v < wp + (fc - wp) * 0.5) return 'low';
  if (v <= fc) return 'ok';
  return 'wet';
}

/** Mały pasek jednej działki: strefy (wg PTW i PP tej działki) i dwa znaczniki: 0–7 cm i 7–28 cm. */
function miniGauge(sm) {
  const fc = nn(sm.field_capacity_pct); const wp = nn(sm.wilting_point_pct);
  const top = nn(sm.surface_pct); const root = nn(sm.root_zone_pct);
  const max = Math.max(50, (fc || 0) + 10, (top || 0) + 5, (root || 0) + 5);
  const pct = (v) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;
  const track = h('div', { class: 'mr-gauge__track mr-soilrow__track', 'aria-hidden': 'true' });
  if (fc !== null && wp !== null && fc > wp) {
    const mid = wp + (fc - wp) * 0.5;
    [['dry', 0, wp], ['low', wp, mid], ['ok', mid, fc], ['wet', fc, max]].forEach(([k, a, b]) => {
      const z = h('span', { class: `mr-gauge__zone mr-gauge__zone--${k}` });
      z.style.left = pct(a); z.style.width = `${((b - a) / max) * 100}%`;
      track.appendChild(z);
    });
  } else {
    track.classList.add('mr-gauge__track--plain');
  }
  [[top, 'top'], [root, 'root']].forEach(([v, k]) => {
    if (v === null) return;
    const m = h('span', { class: `mr-gauge__marker mr-soilrow__marker mr-soilrow__marker--${k}` });
    m.style.left = pct(v);
    track.appendChild(m);
  });
  return track;
}

function soilCard(parcels) {
  const card = h('section', { class: 'mr-card mr-card--wide2', 'aria-labelledby': 'mr-soil-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-soil-h' }, [icon('drop'), 'Woda w glebie – wszystkie działki ',
      infoTip('wilgotność gleby', 'Wilgotność gleby z modelu pogodowego (siatka ok. 9 km), najświeższy stan. Pasek każdej działki: strefy wg pojemności polowej (PP) i punktu więdnięcia (PTW) dla jej gleby (wartości tabelaryczne FAO-56). Gruba kreska – wierzchnia warstwa 0–7 cm, cienka – strefa korzeni 7–28 cm.')]),
  ]);
  if (state.loading) { card.appendChild(skeleton(3)); return card; }
  const rows = parcels.map((p, i) => ({ p, name: p.id_tag || `Działka ${i + 1}` })).sort((a, b) => byName.compare(a.name, b.name));
  const withData = rows.filter(({ p }) => p.soil_moisture && nn(p.soil_moisture.surface_pct) !== null);
  if (!withData.length) {
    const prov = parcels.map((p) => p.provenance && p.provenance.soil_moisture).find(Boolean);
    card.append(...[h('p', { class: 'mr-muted', text: `Brak danych o wilgotności gleby${prov && prov.note ? ` – ${prov.note}` : ''}.` }), prov ? provChip(prov) : null].filter(Boolean));
    return card;
  }
  const dates = withData.map(({ p }) => p.provenance && p.provenance.soil_moisture && p.provenance.soil_moisture.observed_at).filter(Boolean).sort();
  const counts = {};
  withData.forEach(({ p }) => {
    const sm = p.soil_moisture;
    const z = soilZone(nn(sm.surface_pct), nn(sm.field_capacity_pct), nn(sm.wilting_point_pct));
    counts[z] = (counts[z] || 0) + 1;
  });
  const summary = ['wet', 'ok', 'low', 'dry', 'none'].filter((k) => counts[k]).map((k) => h('li', { class: `mr-soilsum__i mr-soilsum__i--${k}` }, [
    h('span', { class: `mr-dot mr-dot--${k === 'none' ? 'plain' : k}`, 'aria-hidden': 'true' }), `${SOIL_STATE[k]}: ${counts[k]}`]));
  card.append(...[
    h('p', { class: 'mr-soil__when' }, [dates.length ? `Stan na ${fmtDate(dates[dates.length - 1])}, wierzchnia warstwa (0–7 cm):` : 'Wierzchnia warstwa (0–7 cm):']),
    h('ul', { class: 'mr-soilsum' }, summary),
    h('ul', { class: 'mr-soilrows' }, rows.map(({ p, name }) => {
      const sm = p.soil_moisture;
      if (!sm || nn(sm.surface_pct) === null) {
        return h('li', { class: 'mr-soilrow' }, [h('span', { class: 'mr-soilrow__name' }, [h('strong', { text: name }), p.crop ? h('span', { class: 'mr-muted', text: p.crop }) : null]),
          h('span', { class: 'mr-muted mr-soilrow__na', text: 'brak danych' })]);
      }
      const top = nn(sm.surface_pct); const root = nn(sm.root_zone_pct);
      const z = soilZone(top, nn(sm.field_capacity_pct), nn(sm.wilting_point_pct));
      const txt = `${num(top, 0)}%${root !== null ? ` · korzenie ${num(root, 0)}%` : ''}`;
      return h('li', { class: 'mr-soilrow' }, [
        h('span', { class: 'mr-soilrow__name' }, [h('strong', { text: name }), p.crop ? h('span', { class: 'mr-muted', text: p.crop }) : null]),
        miniGauge(sm),
        h('span', { class: `mr-soilrow__val mr-soilrow__val--${z}` }, [h('strong', { text: SOIL_STATE[z] }), h('span', { class: 'mr-muted', text: txt })]),
      ]);
    })),
    h('ul', { class: 'mr-gauge__legend' }, [['dry', 'woda niedostępna (poniżej PTW)'], ['low', 'niedobór'], ['ok', 'optymalnie (do PP)'], ['wet', 'nadmiar (powyżej PP)']]
      .map(([k, t]) => h('li', null, [h('span', { class: `mr-dot mr-dot--${k}`, 'aria-hidden': 'true' }), t]))),
    provChip(withData[0].p.provenance && withData[0].p.provenance.soil_moisture),
    withData[0].p.soil_moisture.reference_provenance ? provChip(withData[0].p.soil_moisture.reference_provenance) : null,
  ].filter(Boolean));
  return card;
}

/** Obrazek wilgotności gleby (strefy: woda niedostępna / niedobór / optimum / nadmiar, linie PTW i PP, znaczniki 0–7 i 7–28 cm).
 *  Wspólny dla zakładki Woda i karty działki na Głównej. */
export function soilGauge(sm, prov) {
  const fc = Number.isFinite(Number(sm.field_capacity_pct)) && sm.field_capacity_pct !== null ? Number(sm.field_capacity_pct) : null;
  const wp = Number.isFinite(Number(sm.wilting_point_pct)) && sm.wilting_point_pct !== null ? Number(sm.wilting_point_pct) : null;
  const values = [['Wierzchnia warstwa (0–7 cm)', sm.surface_pct, 'top'], ['Strefa korzeni (7–28 cm)', sm.root_zone_pct, 'root']].filter(([, v]) => v !== null && v !== undefined);
  const max = Math.max(50, (fc || 0) + 10, ...values.map(([, v]) => Number(v) + 5));
  const pct = (v) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;
  const track = h('div', { class: 'mr-gauge__track', 'aria-hidden': 'true' });
  if (fc !== null && wp !== null && fc > wp) {
    const mid = wp + (fc - wp) * 0.5;
    [['dry', 0, wp], ['low', wp, mid], ['ok', mid, fc], ['wet', fc, max]].forEach(([k, a, b]) => {
      const z = h('span', { class: `mr-gauge__zone mr-gauge__zone--${k}` });
      z.style.left = pct(a); z.style.width = `${((b - a) / max) * 100}%`;
      track.appendChild(z);
    });
  } else {
    track.classList.add('mr-gauge__track--plain');
  }
  // linie referencyjne: pojemność polowa (PP) i punkt trwałego więdnięcia (PTW) – wartości tabelaryczne FAO-56
  [[fc, 'PP'], [wp, 'PTW']].forEach(([v, tag]) => {
    if (v === null) return;
    const r = h('span', { class: 'mr-gauge__ref' }, h('span', { class: 'mr-gauge__reflabel', text: tag }));
    r.style.left = pct(v);
    track.appendChild(r);
  });
  values.forEach(([label, v, k]) => {
    const m = h('span', { class: `mr-gauge__marker mr-gauge__marker--${k}` }, h('span', { class: 'mr-gauge__mlabel', text: `${k === 'top' ? '0–7' : '7–28'} cm: ${num(v, 0)}%` }));
    m.style.left = pct(Number(v));
    track.appendChild(m);
  });
  const state_ = (v) => {
    if (fc === null || wp === null) return '';
    if (v <= wp) return ' – poniżej punktu więdnięcia, rośliny nie pobierają wody';
    if (v < wp + (fc - wp) * 0.5) return ' – niedobór wody';
    if (v <= fc) return ' – wilgotność optymalna';
    return ' – nadmiar wody (powyżej pojemności polowej)';
  };
  return [
    h('div', { class: `mr-gauge${fc !== null || wp !== null ? ' mr-gauge--refs' : ''}` }, [
      track,
      h('div', { class: 'mr-gauge__scale', 'aria-hidden': 'true' }, [h('span', { text: '0%' }), h('span', { text: `${num(max, 0)}% obj.` })]),
      fc !== null || wp !== null ? h('p', { class: 'mr-gauge__refkey' }, [
        wp !== null ? h('span', null, [h('span', { class: 'mr-gauge__refswatch', 'aria-hidden': 'true' }), `PTW – punkt więdnięcia ${num(wp, 0)}%`]) : null,
        fc !== null ? h('span', null, [h('span', { class: 'mr-gauge__refswatch', 'aria-hidden': 'true' }), `PP – pojemność polowa ${num(fc, 0)}%`]) : null,
        h('span', { class: 'mr-muted', text: '(wartości tabelaryczne FAO-56)' }),
      ]) : null,
      fc !== null && wp !== null ? h('ul', { class: 'mr-gauge__legend' }, [
        ['dry', `poniżej ${num(wp, 0)}% – woda niedostępna`], ['low', 'niedobór'], ['ok', `optymalnie (do ${num(fc, 0)}%)`], ['wet', 'nadmiar'],
      ].map(([k, t]) => h('li', null, [h('span', { class: `mr-dot mr-dot--${k}`, 'aria-hidden': 'true' }), t]))) :
        h('p', { class: 'mr-hint', text: 'Brak danych o pojemności polowej i punkcie więdnięcia dla gleby tej działki – pokazujemy tylko wilgotność.' }),
    ]),
    h('ul', { class: 'mr-plainlist' }, values.map(([label, v]) => h('li', null, [h('strong', { text: `${label}: ${num(v, 0)}% obj.` }), state_(Number(v))]))),
    sm.trend_7d ? h('p', { text: `Trend 7 dni: ${sm.trend_7d}.` }) : null,
    fc !== null && wp !== null ? h('p', { class: 'mr-hint', text: `Linie przerywane: pojemność polowa ${num(fc, 0)}% i punkt więdnięcia ${num(wp, 0)}% obj. – wartości tabelaryczne (FAO-56) dla gleby „${sm.soil_class || 'średniej'}”, nie pomiar na działce.` }) : null,
    h('div', { class: 'mr-chips' }, [provChip(prov), sm.reference_provenance ? provChip(sm.reference_provenance) : null]),
  ];
}

// --- wodowskaz IMGW: poziom + stany charakterystyczne IMGW-PIB + przebieg 7 dni ---------------------------------------
function nn(v) { return v !== null && v !== undefined && Number.isFinite(Number(v)) ? Number(v) : null; }

const THRESHOLDS = [
  ['alarm_level_cm', 'alarmowy', 'alarm'],
  ['warning_level_cm', 'ostrzegawczy', 'warn'],
  ['high_level_cm', 'granica stanów wysokich', 'high'],
  ['low_level_cm', 'granica stanów niskich', 'low'],
];

function fmtHour(iso) {
  const dt = new Date(iso);
  return Number.isNaN(dt.getTime()) ? String(iso) : new Intl.DateTimeFormat('pl-PL', { weekday: 'short', day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit' }).format(dt);
}

/** Wspólna skala osi Y dla wykresu i fiolki – progi w fiolce leżą na tych samych liniach co na wykresie. */
function riverScale(ser, thr, lvl) {
  const vals = [lvl, ...ser.map((p) => Number(p.level_cm)), ...THRESHOLDS.map(([k]) => thr[k]).filter((v) => v !== null)];
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.08, 5);
  return { min: Math.max(0, Math.floor((lo - pad) / 10) * 10), max: Math.ceil((hi + pad) / 10) * 10 };
}

/** Fiolka z wodą narysowana przy osi wykresu: ta sama wysokość i skala co obszar wykresu. */
function riverVial(lvl, thr, sc) {
  const warn = thr.warning_level_cm;
  const alarm = thr.alarm_level_cm;
  const pct = (v) => `${Math.max(0, Math.min(100, ((v - sc.min) / (sc.max - sc.min)) * 100))}%`;
  const vial = h('div', { class: 'mr-river__col mr-river__col--chart', 'aria-hidden': 'true' });
  const fill = h('span', { class: `mr-river__fill${alarm !== null && lvl >= alarm ? ' mr-river__fill--alarm' : warn !== null && lvl >= warn ? ' mr-river__fill--warn' : ''}` });
  fill.style.height = pct(lvl);
  vial.appendChild(fill);
  THRESHOLDS.forEach(([k, , key]) => {
    const v = thr[k];
    if (v === null || v < sc.min || v > sc.max) return;
    const line = h('span', { class: `mr-river__line mr-river__line--${key}` });
    line.style.bottom = pct(v);
    vial.appendChild(line);
  });
  return vial;
}

const VIAL_GUTTER = 64; // miejsce na fiolkę po lewej stronie osi Y [px]

function riverChart(g, thr, lvl) {
  const ser = Array.isArray(g.series_7d) ? g.series_7d.filter((p) => p && nn(p.level_cm) !== null) : [];
  if (ser.length < 2) return null;
  const labels = ser.map((p) => fmtHour(p.t));
  const n = ser.length;
  const present = THRESHOLDS.filter(([k]) => thr[k] !== null);
  const colors = (t) => ({ alarm: t.tmax, warn: t.warn, high: t.ref, low: t.rainForecast });
  const last = ser[n - 1].level_cm;
  const sc = riverScale(ser, thr, lvl);
  const vial = riverVial(lvl, thr, sc);
  // Fiolka dostaje dokładnie pionowy zakres obszaru wykresu (chartArea), więc linie progów przechodzą z fiolki na wykres.
  const syncVial = {
    id: 'mrRiverVial',
    afterLayout(chart) {
      const a = chart.chartArea;
      if (!a) return;
      vial.style.top = `${a.top}px`;
      vial.style.height = `${a.bottom - a.top}px`;
    },
  };
  const fig = chartFigure({
    title: `Stan wody – ostatnie 7 dni (${[g.river, g.station].filter(Boolean).join(' – ')})`,
    summary: `Ostatni odczyt ${num(last, 0)} cm.${present.length ? ` Linie przerywane: ${present.map(([k, lbl]) => `stan ${lbl} ${num(thr[k], 0)} cm`).join(', ')} (IMGW-PIB).` : ''}`,
    note: present.length ? 'Fiolka po lewej to ostatni odczyt w tej samej skali co wykres. Linie – stany charakterystyczne IMGW-PIB dla tego wodowskazu.' : 'IMGW-PIB nie podaje stanów charakterystycznych dla tego wodowskazu.',
    columns: [{ label: 'Czas' }, { label: 'Stan wody [cm]' }, ...present.map(([, lbl]) => ({ label: `Stan ${lbl} [cm]` }))],
    rows: ser.map((p, i) => [labels[i], num(p.level_cm, 0), ...present.map(([k]) => num(thr[k], 0))]),
    build: (Chart, canvas, t) => ({
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Stan wody [cm]', data: ser.map((p) => p.level_cm), borderColor: t.rain, backgroundColor: t.rain, pointRadius: 0, borderWidth: 2, tension: 0.2, order: 1 },
          ...present.map(([k, lbl, key]) => refDataset(`Stan ${lbl} – IMGW-PIB [cm]`, constLine(n, thr[k]), colors(t)[key])),
        ],
      },
      plugins: [syncVial],
      options: {
        layout: { padding: { left: VIAL_GUTTER } },
        interaction: { mode: 'index', intersect: false },
        // Opis progów (z wartościami) jest obok wykresu – legenda Chart.js tylko by go powtarzała.
        plugins: { legend: Object.assign(baseLegend(t), { display: false }) },
        scales: Object.assign(baseScales(t, 'cm'), {
          x: Object.assign(baseScales(t).x, { ticks: { color: t.muted, maxTicksLimit: 7, maxRotation: 0, autoSkip: true } }),
          y: Object.assign(baseScales(t, 'cm').y, { min: sc.min, max: sc.max }),
        }),
      },
    }),
  });
  const box = fig.querySelector('.mr-chart');
  if (box) { box.classList.add('mr-chart--vial'); box.prepend(vial); }
  return fig;
}

function sameProv(a, b) {
  return a && b && a.source === b.source && String(a.observed_at || '').slice(0, 10) === String(b.observed_at || '').slice(0, 10);
}

function riverGauge(g) {
  if (!g || g.level_cm === null || g.level_cm === undefined) {
    return [h('p', { class: 'mr-muted', text: g && g.provenance && g.provenance.note ? `Brak danych – ${g.provenance.note}.` : 'Brak danych z wodowskazu IMGW-PIB w pobliżu tego pola.' }), g && g.provenance ? provChip(g.provenance) : null];
  }
  const lvl = Number(g.level_cm);
  const thr = Object.fromEntries(THRESHOLDS.map(([k]) => [k, nn(g[k])]));
  const warn = thr.warning_level_cm;
  const alarm = thr.alarm_level_cm;
  const trend = String(g.trend || '').toLowerCase();
  const tIcon = trend.startsWith('ro') ? '↑' : trend.startsWith('sp') || trend.startsWith('op') ? '↓' : '→';
  let status = 'poniżej stanu ostrzegawczego';
  if (alarm !== null && lvl >= alarm) status = 'powyżej stanu alarmowego';
  else if (warn !== null && lvl >= warn) status = 'powyżej stanu ostrzegawczego';
  else if (thr.low_level_cm !== null && lvl < thr.low_level_cm) status = 'strefa stanów niskich (poniżej granicy stanów niskich)';
  else if (thr.high_level_cm !== null && lvl >= thr.high_level_cm) status = 'strefa stanów wysokich, poniżej ostrzegawczego';
  else if (thr.low_level_cm !== null && thr.high_level_cm !== null) status = 'strefa stanów średnich';
  else if (warn === null && alarm === null) status = 'brak progów ostrzegawczych dla tego wodowskazu';
  const chart = riverChart(g, thr, lvl);
  const refProv = g.reference_provenance && !sameProv(g.provenance, g.reference_provenance) ? provChip(g.reference_provenance) : null;
  const info = h('div', { class: 'mr-river__txt' }, [
    h('p', { class: 'mr-river__name', text: [g.river, g.station].filter(Boolean).join(' – ') || 'Wodowskaz' }),
    h('p', { class: 'mr-river__lvl' }, [h('strong', { text: `${num(lvl, 0)} cm` }), h('span', { class: 'mr-river__trend', 'aria-hidden': 'true', text: ` ${tIcon}` }), ` ${g.trend ? `(${g.trend})` : ''}`]),
    h('p', { text: `Stan: ${status}.` }),
    ...THRESHOLDS.filter(([k]) => thr[k] !== null).map(([k, lbl, key]) => h('p', { class: 'mr-river__thr' }, [
      h('span', { class: `mr-river__key mr-river__key--${key}`, 'aria-hidden': 'true' }), `${lbl}: ${num(thr[k], 0)} cm`])),
    h('p', { class: 'mr-river__thr' }, [h('span', { class: 'mr-river__key mr-river__key--level', 'aria-hidden': 'true' }), 'stan wody (IMGW-PIB)']),
    g.observed_at ? h('p', { class: 'mr-muted', text: `Odczyt: ${fmtDate(g.observed_at, true)}` }) : null,
    h('div', { class: 'mr-chips' }, [provChip(g.provenance), refProv]),
  ]);
  if (!chart) {
    // Bez przebiegu 7 dni: sama fiolka (skala od 0) obok opisu.
    const sc = { min: 0, max: Math.max(lvl * 1.25, (alarm || warn || 0) * 1.1, 50) };
    const vial = riverVial(lvl, thr, sc);
    return [h('div', { class: 'mr-riverrow mr-riverrow--single' }, [h('div', { class: 'mr-river' }, [vial, info])])];
  }
  return [h('div', { class: 'mr-riverrow' }, [h('div', { class: 'mr-river' }, [info]), chart])];
}

const ECO = {
  'spełnione': ['ok', 'checkCircle', 'Spełnione'],
  'niespełnione': ['bad', 'cross', 'Niespełnione'],
  'brak danych': ['none', 'minus', 'Brak danych'],
};

function ecoscheme(d, href) {
  const crit = d && Array.isArray(d.criteria) ? d.criteria : [];
  const counts = { ok: 0, bad: 0, none: 0 };
  crit.forEach((c) => { counts[(ECO[c.status] || ECO['brak danych'])[0]] += 1; });
  const schemes = d && Array.isArray(d.schemes) ? d.schemes : [];
  const tally = [['ok', 'checkCircle', 'spełnione'], ['bad', 'cross', 'niespełnione'], ['none', 'minus', 'bez danych']];
  return [
    h('div', { class: 'mr-ecohead' }, [
      h('ul', { class: 'mr-ecotally', 'aria-label': 'Podsumowanie kryteriów' }, tally.map(([k, ico, lbl]) =>
        h('li', { class: `mr-ecotally__item mr-ecotally__item--${k}` }, [icon(ico), h('strong', { text: String(counts[k]) }), h('span', { text: lbl })]))),
      h('div', { class: 'mr-ecohead__txt' }, [
        d && d.summary ? h('p', { class: 'mr-ecohead__summary', text: d.summary }) : null,
        schemes.length ? h('p', { class: 'mr-muted' }, ['Dotyczy: ', ...schemes.flatMap((sc, i) => [
          i ? ', ' : null,
          sc.info_url && /^https:\/\//.test(sc.info_url) ? h('a', { href: sc.info_url, target: '_blank', rel: 'noopener noreferrer', text: sc.name }) : sc.name,
        ])]) : null,
      ]),
    ]),
    crit.length ? h('ul', { class: 'mr-ecogrid' }, crit.map((c) => {
      const [cls, ico, label] = ECO[c.status] || ECO['brak danych'];
      return h('li', { class: `mr-ecocard mr-ecocard--${cls}` }, [
        h('div', { class: 'mr-ecocard__top' }, [h('span', { class: `mr-status mr-status--${cls}` }, [icon(ico), label])]),
        h('p', { class: 'mr-ecocard__name', text: c.name || 'Kryterium' }),
        c.evidence ? h('p', { class: 'mr-ecocard__evidence', text: c.evidence }) : null,
        c.provenance ? provChip(c.provenance) : null,
      ]);
    })) : h('p', { class: 'mr-muted', text: 'Brak kryteriów do oceny.' }),
    h('div', { class: 'mr-ecofoot' }, [
      h('p', { class: 'mr-ecofoot__note' }, [icon('info'), d && d.disclaimer ? d.disclaimer : 'Informacja pomocnicza – nie stanowi dowodu ani wniosku do ARiMR.']),
      h('a', { class: 'mr-btn mr-btn--secondary', href, target: '_blank', rel: 'noopener' }, [icon('download'), 'Pobierz zestawienie (HTML)']),
    ]),
  ];
}

// Zakładka „Historia”: mapa działki z animacją miesięcznych zdjęć Sentinel-2 (NDVI, NDMI, EVI, gleba odsłonięta),
// porównanie wskaźnika rok do roku (z linią średniej 2019–2025) oraz opady miesięczne na tle normy IMGW-PIB.
// Wszystkie dane z serwera (CSP connect-src 'self'): /api/history/*, /api/reference/ndvi.
import { h, icon, replace } from '../dom.js';
import { api } from '../api.js';
import { state, emit } from '../state.js';
import { num, provChip, errorView, emptyView } from '../format.js';
import { chartFigure, baseScales, baseLegend, refDataset } from '../charts.js';
import { ndviReference, ndviByMonth } from '../reference.js';
import { orthoLayer } from '../map.js';
import { skeleton } from './field.js';
import { climateSection } from './climate.js';
import { cropGroups } from './crops.js';

const MONTHS = ['sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru'];
const MONTHS_FULL = ['styczeń', 'luty', 'marzec', 'kwiecień', 'maj', 'czerwiec', 'lipiec', 'sierpień', 'wrzesień', 'październik', 'listopad', 'grudzień'];
const INDICES = [
  ['ndvi', 'leaf', 'Zieleń roślin (NDVI)'],
  ['ndmi', 'drop', 'Woda w roślinach (NDMI)'],
  ['evi', 'sun', 'Wegetacja (EVI)'],
  ['bare', 'layers', 'Gleba odsłonięta'],
];
const SPEEDS = [['1400', '0,5×'], ['800', '1×'], ['400', '2×']];

const cache = new Map();         // url → Promise
let view = null;                 // stan bieżącego widoku (mapa, klatki, odtwarzanie)
let renderToken = 0;
// zakres dat: gotowe okresy albo własny (od–do, miesiące); archiwum Sentinel-2 L2A od 2017 r.
const ARCHIVE_START = '2017-01';
const RANGES = [['12', '12 mies.'], ['36', '3 lata'], ['60', '5 lat'], ['all', 'Całe archiwum (od 2017)'], ['custom', 'Własny zakres']];
const range = { preset: '36', start: null, end: null };

function ymOf(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; }
function ymBack(ym, n) {
  const [y, m] = ym.split('-').map(Number);
  const t = y * 12 + (m - 1) - n;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, '0')}`;
}
function allMonths() {
  const out = [];
  for (let ym = ymOf(new Date()); ym >= ARCHIVE_START; ym = ymBack(ym, 1)) out.push(ym);
  return out;               // od najnowszego
}
function rangeBounds() {
  const now = ymOf(new Date());
  if (range.preset === 'all') return [ARCHIVE_START, now];
  if (range.preset === 'custom' && range.start && range.end) return range.start <= range.end ? [range.start, range.end] : [range.end, range.start];
  const n = Number(range.preset) || 36;
  return [ymBack(now, n - 1) < ARCHIVE_START ? ARCHIVE_START : ymBack(now, n - 1), now];
}
function monthsBetween(a, b) {
  const [ya, ma] = a.split('-').map(Number);
  const [yb, mb] = b.split('-').map(Number);
  return (yb - ya) * 12 + (mb - ma) + 1;
}
function spanText(a, b) {
  const n = monthsBetween(a, b);
  const y = Math.floor(n / 12);
  const m = n % 12;
  const yy = y === 1 ? 'rok' : (y % 10 >= 2 && y % 10 <= 4 && !(y % 100 >= 12 && y % 100 <= 14) ? 'lata' : 'lat');
  return [y ? `${y} ${yy}` : null, m ? `${m} mies.` : null].filter(Boolean).join(' ') || '1 mies.';
}

// zaznaczone działki: { key: gospodarstwo/analiza, ids: Set indeksów, open: czy lista jest rozwinięta }
const pick = { key: null, ids: new Set(), open: false, timer: null };

function plDzialki(n) {
  const d = n % 10;
  const dd = n % 100;
  if (n === 1) return '1 działka';
  return `${n} ${d >= 2 && d <= 4 && !(dd >= 12 && dd <= 14) ? 'działki' : 'działek'}`;
}

// działki, które da się pokazać razem: z identyfikatorem ewidencyjnym (granice z ULDK)
function selectable(a) { return a.parcels.map((x, i) => (x && x.parcel_id ? i : -1)).filter((i) => i >= 0); }

function syncPick(a) {
  const key = `${state.farmName || ''}|${a.generated_at || ''}|${a.parcels.length}`;
  if (pick.key !== key) {
    pick.key = key;
    pick.open = false;
    const all = selectable(a);
    // domyślnie wszystkie działki gospodarstwa; bez identyfikatorów – wybrana działka
    pick.ids = new Set(all.length ? all : [Math.max(0, Math.min(state.selected < 0 ? 0 : state.selected, a.parcels.length - 1))]);
  }
  const chosen = Array.from(pick.ids).filter((i) => i >= 0 && i < a.parcels.length).sort((x, y) => x - y);
  return chosen.length ? chosen : [0];
}

function get(url, timeout = 60000) {
  if (!cache.has(url)) cache.set(url, api(url, { timeout }).catch((e) => { cache.delete(url); throw e; }));
  return cache.get(url);
}

function isNum(v) { return v !== null && v !== undefined && Number.isFinite(Number(v)); }
function monthLabel(ym) { const [y, m] = String(ym).split('-'); return `${MONTHS[Number(m) - 1]} ${y}`; }
function monthLong(ym) { const [y, m] = String(ym).split('-'); return `${MONTHS_FULL[Number(m) - 1]} ${y}`; }
function valueOf(row, index) { return index === 'bare' ? row.bare_pct : row[index]; }
function fmtValue(v, index) { return !isNum(v) ? 'brak danych' : (index === 'bare' ? `${num(v, 0)}%` : num(v, 2)); }

function parcelQuery(p) {
  const qs = new URLSearchParams();
  if (p.parcel_id) qs.set('parcel_id', p.parcel_id);
  if (Array.isArray(p.centroid)) { qs.set('lat', String(p.centroid[0])); qs.set('lon', String(p.centroid[1])); }
  return qs.toString();
}

function statusFor(v, index) {
  if (!isNum(v)) return ['none', 'brak bezchmurnego zdjęcia'];
  const x = Number(v);
  if (index === 'bare') return x >= 50 ? ['bad', 'przeważa gleba odsłonięta'] : x >= 15 ? ['warn', 'częściowo odsłonięta gleba'] : ['ok', 'pole pokryte roślinami'];
  if (index === 'ndmi') return x < -0.2 ? ['bad', 'bardzo sucho / brak roślin'] : x < 0 ? ['warn', 'stres wodny'] : x < 0.2 ? ['mid', 'umiarkowane uwodnienie'] : ['ok', 'dobre uwodnienie roślin'];
  const t = index === 'evi' ? [0.2, 0.35, 0.5] : [0.25, 0.4, 0.5];
  return x < t[0] ? ['bad', 'gleba odsłonięta / brak roślin'] : x < t[1] ? ['warn', 'roślinność rzadka lub w stresie'] : x < t[2] ? ['mid', 'roślinność umiarkowana'] : ['ok', 'dobry, gęsty łan'];
}

function stopPlay() {
  if (view && view.timer) { clearTimeout(view.timer); view.timer = null; }
  if (view) view.playing = false;
}

function destroyView() {
  stopPlay();
  if (view && view.map) { view.map.remove(); }
  view = null;
}

export function renderHistory(panel) {
  const token = ++renderToken;
  destroyView();
  if (state.loading) {
    replace(panel, [h('section', { class: 'mr-card mr-wide', 'aria-busy': 'true' }, [
      h('h2', { class: 'mr-card__title' }, [icon('layers'), 'Historia działki']),
      h('p', { class: 'mr-muted', role: 'status', text: 'Pobieranie danych działek…' }), skeleton(4)])]);
    return;
  }
  if (state.analysisError) { replace(panel, errorView(state.analysisError, () => emit('analysis:retry'), 'Nie udało się pobrać historii')); return; }
  const a = state.analysis;
  if (!a || !Array.isArray(a.parcels) || !a.parcels.length) {
    replace(panel, emptyView('Dodaj własne pole albo wybierz gospodarstwo z sekcji „Przykłady (demo)”.'));
    return;
  }
  const chosen = syncPick(a);
  const multi = chosen.length > 1;
  const sel = chosen.map((i) => a.parcels[i]);
  const p = sel[0];
  const idx = chosen[0];
  const areaSum = sel.reduce((acc, x) => acc + (Number(x.area_ha) || 0), 0);
  const name = multi ? `${plDzialki(chosen.length)}${areaSum ? ` (${num(areaSum, 1)} ha)` : ''}` : (p.id_tag || `Działka ${idx + 1}`);
  const ids = multi ? sel.map((x) => x.parcel_id).sort() : [];
  const q = multi ? new URLSearchParams({ parcel_ids: ids.join(',') }).toString() : parcelQuery(p);
  const refTarget = multi ? { parcel_ids: ids } : p;

  // --- wybór działek: wszystkie albo zaznaczone ---
  const picker = parcelPicker(a, chosen, panel);
  const indexBtns = INDICES.map(([k, ico, label]) => h('button', {
    type: 'button', class: 'mr-mapbar__btn', 'aria-pressed': String(k === 'ndvi'), dataset: { index: k },
    onclick: () => setIndex(k),
  }, [icon(ico), h('span', { text: label })]));
  const [rStart, rEnd] = rangeBounds();
  const rq = `start=${rStart}&end=${rEnd}`;
  const bar = h('div', { class: 'mr-histbar' }, [
    h('span', { class: 'mr-histbar__lbl', id: 'mr-hist-parcel-lbl', text: 'Działki:' }), picker,
    h('div', { class: 'mr-mapbar__group', role: 'group', 'aria-label': 'Wskaźnik' }, indexBtns),
  ]);
  const rangeBar = rangePicker(panel, rStart, rEnd);

  // --- mapa + oś czasu ---
  const mapEl = h('div', { class: 'mr-histmap__map', role: 'region', 'aria-label': multi ? `Mapa historii: ${name}` : `Mapa historii działki ${name}` });
  const mapNote = h('p', { class: 'mr-histmap__status', role: 'status', 'aria-live': 'polite' });
  const legendEl = h('div', { class: 'mr-histmap__legend' });
  const lblMonth = h('strong', { class: 'mr-timeline__month', text: '–' });
  const lblValue = h('span', { class: 'mr-timeline__value' });
  const lblBadge = h('span', { class: 'mr-status mr-status--none', text: '–' });
  const slider = h('input', { type: 'range', class: 'mr-timeline__slider', min: '0', max: '0', value: '0', 'aria-label': 'Miesiąc', disabled: true });
  const playBtn = h('button', { type: 'button', class: 'mr-btn mr-btn--primary mr-timeline__play', 'aria-pressed': 'false', disabled: true });
  const prevBtn = h('button', { type: 'button', class: 'mr-btn mr-btn--secondary', 'aria-label': 'Poprzedni miesiąc', disabled: true, text: '⏮' });
  const nextBtn = h('button', { type: 'button', class: 'mr-btn mr-btn--secondary', 'aria-label': 'Następny miesiąc', disabled: true, text: '⏭' });
  const speed = h('select', { class: 'mr-select mr-timeline__speed', 'aria-label': 'Prędkość animacji' },
    SPEEDS.map(([v, t]) => h('option', { value: v, selected: v === '800', text: t })));
  const marks = h('div', { class: 'mr-timeline__marks', 'aria-hidden': 'true' });
  const provBox = h('div', { class: 'mr-chips' });
  const mapCard = h('section', { class: 'mr-card mr-wide', 'aria-labelledby': 'mr-hist-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-hist-h' }, [icon('satellite'), `Historia z satelity – ${name}`]),
    bar,
    rangeBar,
    h('div', { class: 'mr-histmap' }, [mapEl, legendEl]),
    mapNote,
    h('div', { class: 'mr-timeline' }, [
      h('div', { class: 'mr-timeline__hud' }, [lblMonth, lblValue, lblBadge]),
      h('div', { class: 'mr-timeline__controls' }, [prevBtn, playBtn, nextBtn, h('label', { class: 'mr-timeline__speedlbl' }, ['Prędkość ', speed])]),
      h('div', { class: 'mr-timeline__track' }, [slider, marks]),
    ]),
    h('p', { class: 'mr-hint', text: 'Każda klatka to kompozyt miesięczny: dla każdego piksela mediana bezchmurnych zdjęć Sentinel-2 z danego miesiąca. '
      + 'Piksele bez bezchmurnego zdjęcia są przezroczyste. Animacja obejmuje wybrany okres.'
      + (multi ? ' Przy kilku działkach wartość miesiąca to średnia ze wszystkich pikseli zaznaczonych działek (ważona powierzchnią).' : '') }),
    provBox,
  ]);

  const trendBox = h('div', { class: 'mr-figbox', 'aria-busy': 'true' }, [h('p', { class: 'mr-muted', role: 'status', text: 'Wczytywanie historii wskaźników…' }), skeleton(3)]);
  const trendCard = h('section', { class: 'mr-card mr-wide', 'aria-labelledby': 'mr-hist-trend-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-hist-trend-h' }, [icon('leaf'), `Wskaźnik w czasie: ${monthLabel(rStart)} – ${monthLabel(rEnd)}`]), trendBox]);
  const climBox = h('div', { class: 'mr-figbox', 'aria-busy': 'true' }, [h('p', { class: 'mr-muted', role: 'status', text: 'Wczytywanie opadów…' }), skeleton(3)]);
  const climCard = h('section', { class: 'mr-card mr-wide', 'aria-labelledby': 'mr-hist-clim-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-hist-clim-h' }, [icon('cloud'), 'Opady miesięczne na tle normy IMGW-PIB i wilgotność gleby']), climBox]);

  // klimat pola (ERA5 od 1991 r.) – dla pierwszej zaznaczonej działki, uprawy wszystkich zaznaczonych
  const climateCard = climateSection(p, Array.from(cropGroups(sel).values()), token, (t) => t === renderToken);
  replace(panel, [mapCard, trendCard, climCard, climateCard]);

  view = { map: null, overlay: null, outline: null, months: [], rows: [], index: 'ndvi', pos: 0, playing: false, timer: null, q, data: null, ref: null };

  // mapa Leaflet (podkład GUGiK + kontur działki)
  try {
    const L = window.L;
    view.map = L.map(mapEl, { zoomControl: true, attributionControl: true, scrollWheelZoom: false });
    view.map.attributionControl.setPrefix(false);
    orthoLayer().addTo(view.map);
    const rings = sel.map((x) => (x.geojson_coords || []).filter((c) => Array.isArray(c) && c.length >= 2).map(([lon, lat]) => [lat, lon]))
      .filter((ll) => ll.length >= 3);
    if (rings.length) {
      view.outline = L.featureGroup(rings.map((ll) => L.polygon(ll, { className: 'mr-parcel mr-parcel--outline', weight: multi ? 2 : 3, fill: false, interactive: false })))
        .addTo(view.map);
      view.map.fitBounds(view.outline.getBounds(), { padding: [24, 24], maxZoom: 17 });
    } else if (Array.isArray(p.centroid)) {
      view.map.setView(p.centroid, 16);
    }
    setTimeout(() => { if (view && view.map && mapEl.isConnected) view.map.invalidateSize(); }, 60);
  } catch (e) {
    replace(mapEl, h('p', { class: 'mr-state mr-state--error', text: `Nie udało się wczytać mapy: ${e.message}` }));
  }

  // --- zachowanie osi czasu ---
  const paintPlay = () => {
    const on = !!(view && view.playing);
    playBtn.setAttribute('aria-pressed', String(on));
    replace(playBtn, [h('span', { 'aria-hidden': 'true', text: on ? '⏸' : '▶' }), on ? ' Zatrzymaj' : ' Odtwórz animację']);
  };
  const frameUrl = (i) => `/api/history/frame?${q}&month=${encodeURIComponent(view.months[i])}&index=${view.index}`;

  async function show(i) {
    if (!view || !view.months.length) return;
    const my = token;
    view.pos = Math.max(0, Math.min(view.months.length - 1, i));
    slider.value = String(view.pos);
    const ym = view.months[view.pos];
    const row = view.rows[view.pos] || {};
    const v = valueOf(row, view.index);
    lblMonth.textContent = monthLong(ym);
    lblValue.textContent = `${INDICES.find(([k]) => k === view.index)[2]}: ${fmtValue(v, view.index)}`;
    const [cls, txt] = statusFor(v, view.index);
    lblBadge.className = `mr-status mr-status--${cls === 'mid' ? 'mid' : cls}`;
    lblBadge.textContent = txt;
    slider.setAttribute('aria-valuetext', `${monthLong(ym)}, ${fmtValue(v, view.index)}`);
    mapNote.textContent = 'Wczytywanie zdjęcia…';
    try {
      const f = await get(frameUrl(view.pos), 45000);
      if (my !== renderToken || !view || view.months[view.pos] !== ym) return;
      if (view.overlay) { view.map.removeLayer(view.overlay); view.overlay = null; }
      if (!isNum(v) && isNum(f.mean)) {
        // miesięczna statystyka wymaga ≥50% działki bez chmur; klatka pokazuje to, co było widać
        lblValue.textContent = `${INDICES.find(([k]) => k === view.index)[2]}: ${fmtValue(f.mean, view.index)} (zdjęcie częściowe – chmury)`;
        const [c2, t2] = statusFor(f.mean, view.index);
        lblBadge.className = `mr-status mr-status--${c2}`;
        lblBadge.textContent = t2;
      }
      if (f.image && Array.isArray(f.bounds) && /^data:image\/png;base64,/.test(f.image)) {
        view.overlay = window.L.imageOverlay(f.image, f.bounds, { opacity: 0.85, alt: `${INDICES.find(([k]) => k === view.index)[2]} – ${monthLong(ym)}`, className: 'mr-ndvi' }).addTo(view.map);
        if (view.outline) view.outline.bringToFront();
        mapNote.textContent = '';
      } else {
        mapNote.textContent = `${monthLong(ym)}: brak bezchmurnego zdjęcia działki – pokazujemy sam podkład.`;
      }
      renderLegend(f.legend);
      replace(provBox, [provChip(f.provenance), view.data ? provChip(view.data.provenance) : null]);
      // wstępne pobranie kolejnych klatek (płynna animacja)
      for (let k = 1; k <= 2; k++) if (view.pos + k < view.months.length) get(frameUrl(view.pos + k), 45000).catch(() => {});
    } catch (e) {
      if (my === renderToken) mapNote.textContent = `Nie udało się pobrać zdjęcia: ${e.message}`;
    }
  }

  function renderLegend(lg) {
    if (!Array.isArray(lg)) { replace(legendEl, null); return; }
    replace(legendEl, [h('p', { class: 'mr-histmap__legendtitle', text: INDICES.find(([k]) => k === view.index)[2] }),
      h('ul', null, lg.map((b) => {
        const sw = h('span', { class: 'mr-swatch', 'aria-hidden': 'true' });
        if (/^#[0-9a-f]{6}$/i.test(String(b.color))) sw.style.background = b.color;
        return h('li', null, [sw, b.label]);
      }))]);
  }

  async function tick() {
    if (!view || !view.playing) return;
    if (!mapEl.isConnected || mapEl.closest('[hidden]')) { stopPlay(); paintPlay(); return; }   // zakładka ukryta – pauza
    const next = view.pos + 1 >= view.months.length ? null : view.pos + 1;
    if (next === null) { stopPlay(); paintPlay(); return; }
    await show(next);
    if (view && view.playing) view.timer = setTimeout(tick, Number(speed.value));
  }

  playBtn.addEventListener('click', () => {
    if (!view) return;
    if (view.playing) { stopPlay(); paintPlay(); return; }
    view.playing = true;
    paintPlay();
    if (view.pos >= view.months.length - 1) show(0).then(() => { if (view && view.playing) view.timer = setTimeout(tick, Number(speed.value)); });
    else tick();
  });
  prevBtn.addEventListener('click', () => { stopPlay(); paintPlay(); show(view.pos - 1); });
  nextBtn.addEventListener('click', () => { stopPlay(); paintPlay(); show(view.pos + 1); });
  slider.addEventListener('input', () => { stopPlay(); paintPlay(); show(Number(slider.value)); });
  paintPlay();

  function setIndex(k) {
    if (!view) return;
    view.index = k;
    indexBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.index === k)));
    show(view.pos);
    drawTrend();
  }

  function drawTrend() {
    if (!view || !view.data) return;
    trendBox.removeAttribute('aria-busy');
    replace(trendBox, trendChart(view.data, view.index, view.ref, multi, (i) => { stopPlay(); paintPlay(); show(i); mapEl.scrollIntoView({ behavior: 'smooth', block: 'center' }); }));
  }

  // --- dane: wskaźniki miesięczne + średnia wieloletnia NDVI ---
  Promise.all([get(`/api/history/indices?${q}&${rq}`, 120000), ndviReference(refTarget).catch(() => null)]).then(([d, ref]) => {
    if (token !== renderToken || !view) return;
    view.data = d;
    view.ref = ref;
    view.rows = Array.isArray(d.months) ? d.months : [];
    view.months = view.rows.map((r) => r.month);
    if (!view.months.length) {
      mapNote.textContent = `Brak historii zdjęć: ${(d.provenance && d.provenance.note) || 'brak danych'}`;
      replace(provBox, provChip(d.provenance));
      trendBox.removeAttribute('aria-busy');
      replace(trendBox, [h('p', { class: 'mr-muted', text: (d.provenance && d.provenance.note) || 'Brak danych.' }), provChip(d.provenance)]);
      return;
    }
    slider.max = String(view.months.length - 1);
    [slider, playBtn, prevBtn, nextBtn].forEach((el) => { el.disabled = false; });
    const step = Math.max(1, Math.round(view.months.length / 6));
    replace(marks, view.months.filter((_, i) => i % step === 0 || i === view.months.length - 1).map((m) => h('span', { text: monthLabel(m) })));
    show(view.months.length - 1);
    drawTrend();
  }).catch((e) => {
    if (token !== renderToken) return;
    mapNote.textContent = `Nie udało się pobrać historii: ${e.message}`;
    trendBox.removeAttribute('aria-busy');
    replace(trendBox, errorView(e, () => { cache.clear(); emit('analysis:retry'); }, 'Brak danych'));
  });

  // --- opady miesięczne vs norma ---
  // opady: środek zaznaczonych działek (leżą w jednym rejonie pogodowym)
  const cs = sel.map((x) => x.centroid).filter((x) => Array.isArray(x) && x.length >= 2);
  const c = cs.length ? [cs.reduce((t, x) => t + Number(x[0]), 0) / cs.length, cs.reduce((t, x) => t + Number(x[1]), 0) / cs.length]
    .map((v) => Math.round(v * 1e5) / 1e5) : null;
  if (c) {
    get(`/api/history/climate?lat=${encodeURIComponent(c[0])}&lon=${encodeURIComponent(c[1])}&${rq}`, 60000).then((d) => {
      if (token !== renderToken) return;
      climBox.removeAttribute('aria-busy');
      replace(climBox, climateChart(d));
    }).catch((e) => {
      if (token !== renderToken) return;
      climBox.removeAttribute('aria-busy');
      replace(climBox, h('p', { class: 'mr-muted', text: `Nie udało się pobrać opadów: ${e.message}` }));
    });
  } else {
    replace(climBox, h('p', { class: 'mr-muted', text: 'Brak położenia działki.' }));
  }
}

// --- wybór działek: „Wszystkie” albo dowolne zaznaczone (lista z polami wyboru) ------------------------------
function parcelPicker(a, chosen, panel) {
  const all = selectable(a);
  const allOn = all.length > 0 && all.every((i) => pick.ids.has(i));
  const summaryText = allOn && all.length > 1 ? `Wszystkie (${all.length})`
    : chosen.length === 1 ? (a.parcels[chosen[0]].id_tag || `Działka ${chosen[0] + 1}`) : `Zaznaczone: ${plDzialki(chosen.length)}`;
  const apply = () => {
    clearTimeout(pick.timer);
    pick.timer = setTimeout(() => { if (panel.isConnected) renderHistory(panel); }, 450);   // kilka kliknięć = jedno odświeżenie
  };
  const boxes = [];
  const need = h('p', { class: 'mr-pick__need', role: 'status', hidden: true, text: 'Zaznacz co najmniej jedną działkę.' });
  const changed = () => {
    need.hidden = pick.ids.size > 0;
    if (pick.ids.size) apply(); else clearTimeout(pick.timer);   // pusta lista – czekamy na wybór
  };
  const setAll = (on) => {
    pick.ids = new Set(on ? all : []);
    boxes.forEach(([cb, i]) => { cb.checked = pick.ids.has(i); });
    master.checked = on;
    changed();
  };
  const master = h('input', { type: 'checkbox', class: 'mr-check', id: 'mr-pick-all', checked: allOn, disabled: all.length < 2,
    onchange: (e) => setAll(e.target.checked) });
  const items = a.parcels.map((x, i) => {
    const ok = all.includes(i);
    const cb = h('input', { type: 'checkbox', class: 'mr-check', id: `mr-pick-${i}`, checked: pick.ids.has(i), disabled: !ok,
      onchange: (e) => {
        if (e.target.checked) pick.ids.add(i); else pick.ids.delete(i);
        master.checked = all.every((k) => pick.ids.has(k));
        changed();
      } });
    if (ok) boxes.push([cb, i]);
    return h('li', null, h('label', { class: 'mr-pick__item', for: `mr-pick-${i}` }, [cb,
      h('span', null, [h('strong', { text: x.id_tag || `Działka ${i + 1}` }),
        h('span', { class: 'mr-muted', text: [x.crop, x.area_ha ? `${num(x.area_ha, 2)} ha` : null, ok ? null : 'brak granic'].filter(Boolean).join(' · ') })])]));
  });
  const det = h('details', { class: 'mr-pick', open: pick.open, ontoggle: (e) => { pick.open = e.target.open; } }, [
    h('summary', { class: 'mr-pick__summary', 'aria-describedby': 'mr-hist-parcel-lbl' }, [h('span', { text: summaryText })]),
    h('div', { class: 'mr-pick__panel' }, [
      h('label', { class: 'mr-pick__item mr-pick__item--all', for: 'mr-pick-all' }, [master, h('strong', { text: `Wszystkie działki (${all.length})` })]),
      need,
      h('ul', { class: 'mr-pick__list' }, items),
      h('p', { class: 'mr-hint', text: 'Dla kilku działek wykresy pokazują średnią ze wszystkich pikseli zaznaczonych działek.' }),
    ]),
  ]);
  return det;
}

// --- wybór okresu: gotowe zakresy + własny (od–do) ---------------------------------------------------------
function rangePicker(panel, rStart, rEnd) {
  const rerender = () => { if (panel.isConnected) renderHistory(panel); };
  const btns = RANGES.map(([k, label]) => h('button', {
    type: 'button', class: 'mr-range__btn', 'aria-pressed': String(range.preset === k),
    onclick: () => {
      if (k === 'custom') { range.start = range.start || rStart; range.end = range.end || rEnd; }
      range.preset = k;
      rerender();
    },
  }, label));
  const opts = (sel) => allMonths().map((ym) => h('option', { value: ym, selected: ym === sel, text: monthLabel(ym) }));
  const from = h('select', { class: 'mr-select mr-range__sel', id: 'mr-range-from', onchange: (e) => { range.start = e.target.value; rerender(); } }, opts(rStart));
  const to = h('select', { class: 'mr-select mr-range__sel', id: 'mr-range-to', onchange: (e) => { range.end = e.target.value; rerender(); } }, opts(rEnd));
  return h('div', { class: 'mr-range' }, [
    h('span', { class: 'mr-histbar__lbl', id: 'mr-range-lbl', text: 'Okres:' }),
    h('div', { class: 'mr-range__group', role: 'group', 'aria-labelledby': 'mr-range-lbl' }, btns),
    range.preset === 'custom' ? h('div', { class: 'mr-range__custom' }, [
      h('label', { for: 'mr-range-from', text: 'od' }), from, h('label', { for: 'mr-range-to', text: 'do' }), to]) : null,
    h('span', { class: 'mr-muted mr-range__info', text: `${monthLabel(rStart)} – ${monthLabel(rEnd)} (${spanText(rStart, rEnd)})` }),
  ]);
}

// --- wykres: wskaźnik na pełnej osi czasu (każdy miesiąc wybranego okresu) + średnia wieloletnia NDVI -----------
function trendChart(d, index, ref, multi = false, onPick = null) {
  const rows = d.months || [];
  const refM = index === 'ndvi' ? ndviByMonth(ref) : new Map();
  const hasRef = refM.size > 0;
  const label = INDICES.find(([k]) => k === index)[2];
  const unit = index === 'bare' ? '% działki' : label.match(/\((.*)\)/) ? label.match(/\((.*)\)/)[1] : 'wartość';
  const vals = rows.map((r) => { const v = valueOf(r, index); return isNum(v) ? Number(v) : null; });
  const refVals = rows.map((r) => { const m = Number(r.month.slice(5, 7)); return refM.has(m) ? refM.get(m) : null; });
  const last = rows.filter((r) => isNum(valueOf(r, index))).slice(-1)[0];
  const known = vals.filter((v) => v !== null).length;
  const many = rows.length > 48;
  return chartFigure({
    title: `${label} – ${rows.length ? `${monthLabel(rows[0].month)} – ${monthLabel(rows[rows.length - 1].month)}` : 'brak danych'}`,
    summary: last ? `Ostatni miesiąc z danymi: ${monthLong(last.month)} – ${fmtValue(valueOf(last, index), index)}. Miesiące z danymi: ${known} z ${rows.length}.` : 'Brak danych.',
    chip: [provChip(d.provenance), hasRef && ref ? provChip(ref.provenance) : null],
    note: [
      hasRef ? `Linia przerywana – średnia NDVI ${multi ? 'zaznaczonych działek' : 'tej działki'} dla danego miesiąca kalendarzowego z lat ${ref.period || '2019–2025'} (Copernicus Sentinel-2).` : null,
      index === 'bare' ? 'Udział pikseli działki z NDVI < 0,25 (gleba bez roślin) w miesięcznym kompozycie.' : null,
      'Przerwy w linii to miesiące bez bezchmurnego zdjęcia (mniej niż 50% działki bez chmur). Kliknij punkt, aby pokazać ten miesiąc na mapie.',
    ].filter(Boolean).join(' '),
    columns: [{ label: 'Miesiąc' }, { label }, ...(hasRef ? [{ label: `Średnia ${ref.period || '2019–2025'}` }] : [])],
    rows: rows.map((r, i) => [monthLong(r.month), fmtValue(vals[i], index), ...(hasRef ? [fmtValue(refVals[i], index)] : [])]),
    build: (Chart, canvas, t) => ({
      type: 'line',
      data: {
        labels: rows.map((r) => monthLabel(r.month)),
        datasets: [
          { label, data: vals, borderColor: t.rain, backgroundColor: t.rain, borderWidth: 2.5,
            pointRadius: many ? 1.5 : 3, pointHoverRadius: 5, spanGaps: false, tension: 0.25, order: 1 },
          ...(hasRef ? [refDataset(`Średnia ${ref.period || '2019–2025'} (Sentinel-2)`, refVals, t.ndviRef)] : []),
        ],
      },
      options: {
        interaction: { mode: 'index', intersect: false },
        onClick: (evt, els) => { if (onPick && els && els.length) onPick(els[0].index); },
        plugins: { legend: baseLegend(t) },
        scales: Object.assign(baseScales(t, unit), {
          x: Object.assign({}, baseScales(t, unit).x, { ticks: Object.assign({}, (baseScales(t, unit).x || {}).ticks, { autoSkip: true, maxTicksLimit: many ? 14 : 18, maxRotation: 0 }) }),
          y: Object.assign(baseScales(t, unit).y, index === 'bare' ? { min: 0, max: 100 } : { suggestedMin: index === 'ndmi' ? -0.3 : 0, suggestedMax: index === 'ndmi' ? 0.5 : 1 }),
        }),
      },
    }),
  });
}

// --- wykres: opady miesięczne (ERA5) vs norma IMGW-PIB + wilgotność gleby ---------------------------------------
function climateChart(d) {
  const rows = Array.isArray(d && d.months) ? d.months : [];
  if (!rows.length) return [h('p', { class: 'mr-muted', text: (d && d.provenance && d.provenance.precip && d.provenance.precip.note) || 'Brak danych o opadach.' }),
    d && d.provenance ? provChip(d.provenance.precip) : null];
  const hasNorm = rows.some((r) => isNum(r.precip_normal_mm));
  const sumP = rows.filter((r) => r.complete).reduce((a, r) => a + (Number(r.precip_mm) || 0), 0);
  const sumN = rows.filter((r) => r.complete).reduce((a, r) => a + (Number(r.precip_normal_mm) || 0), 0);
  const summary = hasNorm && sumN ? `Pełne miesiące: opad ${num(sumP, 0)} mm wobec normy ${num(sumN, 0)} mm (${num((sumP / sumN) * 100, 0)}% normy IMGW-PIB 1991–2020).` : `Suma opadów: ${num(sumP, 0)} mm.`;
  return [
    h('p', { class: 'mr-callout mr-callout--info' }, [icon('cloud'), summary]),
    chartFigure({
      title: `Opad miesięczny i norma 1991–2020${d.normals_station ? ` (IMGW-PIB ${d.normals_station})` : ''}`,
      summary,
      chip: [provChip(d.provenance.precip), provChip(d.provenance.normals)],
      note: 'Słupki – suma opadów w miesiącu (reanaliza ERA5, nie pomiar na działce); linia przerywana – norma wieloletnia IMGW-PIB; '
        + 'zielona linia (oś prawa) – średnia wilgotność gleby 7–28 cm (ERA5-Land). Bieżący miesiąc jest niepełny – norma przeliczona proporcjonalnie.',
      columns: [{ label: 'Miesiąc' }, { label: 'Opad [mm]' }, { label: 'Norma 1991–2020 [mm]' }, { label: 'Wilgotność gleby 7–28 cm [% obj.]' }],
      rows: rows.map((r) => [monthLabel(r.month) + (r.complete ? '' : ' (niepełny)'), isNum(r.precip_mm) ? num(r.precip_mm, 1) : 'brak',
        isNum(r.precip_normal_mm) ? num(r.precip_normal_mm, 1) : 'brak', isNum(r.soil_moisture_pct) ? num(r.soil_moisture_pct, 1) : 'brak']),
      build: (Chart, canvas, t) => ({
        type: 'bar',
        data: {
          labels: rows.map((r) => monthLabel(r.month)),
          datasets: [
            { type: 'bar', label: 'Opad [mm] (ERA5)', data: rows.map((r) => r.precip_mm), backgroundColor: rows.map((r) => (r.complete ? t.rain : t.rainForecast)), borderRadius: 2, order: 2, yAxisID: 'y' },
            ...(hasNorm ? [refDataset('Norma opadu 1991–2020 (IMGW-PIB) [mm]', rows.map((r) => r.precip_normal_mm), t.ref, { yAxisID: 'y', stepped: 'middle' })] : []),
            { type: 'line', label: 'Wilgotność gleby 7–28 cm [% obj.]', data: rows.map((r) => r.soil_moisture_pct), borderColor: t.balance, backgroundColor: t.balance, pointRadius: 0, borderWidth: 2, tension: 0.3, yAxisID: 'y1', order: 1 },
          ],
        },
        options: {
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: baseLegend(t) },
          scales: {
            ...baseScales(t, 'mm / miesiąc'),
            y1: { position: 'right', ticks: { color: t.balance }, grid: { display: false }, title: { display: true, text: '% obj.', color: t.balance } },
          },
        },
      }),
    }),
  ];
}

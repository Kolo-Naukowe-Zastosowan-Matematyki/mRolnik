// „Filtruj działki”: panel kryteriów (desktop: z prawej nad mapą, telefon: arkusz od dołu), zadanie w tle na serwerze,
// wyniki z ✓/✗/? per kryterium, filtr mapy (zostaje po zamknięciu panelu do „Wyczyść filtr”), eksport CSV/GeoJSON.
// Każde włączone kryterium jest konieczne; brak danych = „do sprawdzenia”, nie odrzucenie.
import { h, icon, replace, nextId, infoTip } from './dom.js';
import { api } from './api.js';
import { state, emit, on, announce, CUSTOM_FARM } from './state.js';
import { provChip } from './format.js';
import { getMap, getMetric, setParcelFilter } from './map.js';
import { exportCsv, downloadText } from './csv.js';
import { saveDialog, showFilterSet, setKey, FILTER_FARM } from './usersets.js';

const collator = new Intl.Collator('pl', { numeric: true, sensitivity: 'base' });
const STATUS = {
  spelnia: { text: 'Spełnia', cls: 'ok', icon: 'checkCircle', order: 0 },
  do_sprawdzenia: { text: 'Do sprawdzenia', cls: 'mid', icon: 'alert', order: 1 },
  w_toku: { text: 'W toku', cls: 'none', icon: 'refresh', order: 2 },
  nie: { text: 'Nie spełnia', cls: 'bad', icon: 'cross', order: 3 },
  brak_geometrii: { text: 'Brak geometrii ULDK', cls: 'none', icon: 'minus', order: 4 },
};
const CSTAT = {
  ok: { icon: 'check', sr: 'spełnia' },
  fail: { icon: 'cross', sr: 'nie spełnia' },
  brak: { icon: 'minus', sr: 'brak danych' },
  pending: { icon: 'refresh', sr: 'w toku' },
  skipped: { icon: 'minus', sr: 'pominięte' },
};

const GROUPS = [
  { id: 'basic', title: 'Podstawowe', open: true, items: ['area', 'shape', 'water', 'town'] },
  { id: 'terrain', title: 'Teren i zagrożenia', items: ['slope', 'flood'] },
  { id: 'access', title: 'Dostęp i infrastruktura', items: ['road', 'power', 'rail', 'buildings'] },
  { id: 'land', title: 'Grunt', items: ['forest', 'ndvi'] },
  { id: 'structure', title: 'Zasób i struktura', items: ['merge'] },
];
const OPS = [['le', 'najwyżej'], ['ge', 'co najmniej']];
const DEF = {
  area: { label: 'Powierzchnia', fields: [num('min_ha', 'od [ha]', 1, 0.01), num('max_ha', 'do [ha]', null, 0.01)] },
  shape: {
    label: 'Kształt',
    fields: [sel('preset', 'Kształt', [['any', 'dowolny'], ['compact', 'zwarty'], ['rect', 'prawie prostokąt']], 'compact'),
      num('min_width_m', 'min. szerokość [m]', null, 1)],
    hint: 'Zwarty: zwartość Polsby-Popper (4πA/P²) ≥ 0,5. Prawie prostokąt: powierzchnia ≥ 85% najmniejszego obejmującego prostokąta.',
  },
  water: {
    label: 'Odległość od cieków i wód',
    fields: [sel('op', 'Warunek', OPS, 'le'), num('dist_m', 'odległość [m]', 200, 10),
      checks('kinds', 'Rodzaj wód', [['rivers', 'rzeki i strumienie'], ['canals', 'kanały'], ['ditches', 'rowy'], ['standing', 'wody stojące']], ['rivers', 'canals', 'ditches', 'standing'])],
    hint: 'Od granicy działki do najbliższego cieku lub zbiornika z BDOT10k (GUGiK).',
  },
  town: {
    label: 'Odległość od miasta', fields: [num('max_km', 'nie dalej niż [km]', 15, 0.5), num('min_pop', 'miasto ≥ mieszkańców', null, 1000)],
    hint: 'Od granicy działki do granicy miasta (PRNG + BDOT10k); liczba mieszkańców z BDOT10k.',
  },
  slope: { label: 'Nachylenie terenu', fields: [num('max_mean_pct', 'średni spadek ≤ [%]', 5, 0.5), num('max_p90_pct', '90% działki ≤ [%]', null, 0.5)], hint: 'Z numerycznego modelu terenu GUGiK (siatka 1 m).' },
  flood: {
    label: 'Zagrożenie powodziowe',
    fields: [sel('zone', 'Strefa', [['q1', 'Q1%'], ['q10', 'Q10%'], ['q02', 'Q0,2%']], 'q1'),
      sel('mode', 'Warunek', [['outside', 'poza strefą'], ['max_share', 'udział ≤ X%']], 'outside'), num('max_pct', 'X [%]', null, 1)],
    hint: 'Mapy zagrożenia powodziowego ISOK: Q10% – woda raz na 10 lat, Q1% – raz na 100 lat, Q0,2% – raz na 500 lat.',
  },
  road: {
    label: 'Droga publiczna',
    fields: [num('dist_m', 'nie dalej niż [m]', 20, 5),
      checks('categories', 'Kategoria drogi', [['krajowa', 'krajowa'], ['wojewodzka', 'wojewódzka'], ['powiatowa', 'powiatowa'], ['gminna', 'gminna'], ['wewnetrzna', 'wewnętrzna']],
        ['krajowa', 'wojewodzka', 'powiatowa', 'gminna'])],
  },
  power: { label: 'Linia elektroenergetyczna', fields: [sel('op', 'Warunek', OPS, 'ge'), num('dist_m', 'odległość [m]', 50, 10)] },
  rail: { label: 'Linia kolejowa', fields: [sel('op', 'Warunek', OPS, 'ge'), num('dist_m', 'odległość [m]', 100, 10)] },
  buildings: { label: 'Odległość od zabudowy', fields: [sel('op', 'Warunek', OPS, 'ge'), num('dist_m', 'odległość [m]', 100, 10)] },
  forest: { label: 'Udział lasu i zadrzewień', fields: [num('max_pct', 'nie więcej niż [%]', 10, 1)] },
  ndvi: { label: 'Kondycja roślin (NDVI)', fields: [num('min_mean', 'średnie NDVI ≥', 0.4, 0.05)], hint: 'Tylko z ostatniej analizy gospodarstwa (bez nowych zapytań do satelity).' },
  merge: { label: 'Możliwość scalenia', fields: [num('min_ha', 'grupa przylegających ≥ [ha]', 10, 0.5)], hint: 'Działki z puli stykające się granicami (odstęp ≤ 1 m).' },
};
const DEFAULT_ON = new Set(['area', 'shape']);
// rodzaj gminy z TERYT (7. znak): miasto i obszar wiejski gminy miejsko-wiejskiej łączymy w jedną gminę
const GM_KIND = { 1: 'gm. miejska', 2: 'gm. wiejska', 3: 'gm. miejsko-wiejska', 4: 'gm. miejsko-wiejska', 5: 'gm. miejsko-wiejska', 8: 'dzielnica', 9: 'delegatura' };

function num(key, label, def, step) { return { type: 'num', key, label, def, step }; }
function sel(key, label, opts, def) { return { type: 'sel', key, label, opts, def }; }
function checks(key, label, opts, def) { return { type: 'checks', key, label, opts, def }; }

let ui = null;
let job = null;          // { id, timer, data }
let resultLayer = null;
let zoneLayer = null;
let lastFocus = null;
let admin = null;        // Map(klucz działki → {woj, woj_name, powiat, powiat_name, gmina, gmina_name})
let adminLoading = null;
let filterInfo = null;   // { ids:Set, shown, total, data }
let chip = null;
const EGIB = '__egib';   // „Wszystkie działki w wybranym obszarze (EGiB)”
const unitsCache = new Map();   // klucz zapytania → odpowiedź /api/parcel-search/units
const LIST_MAX = 150;            // tyle pozycji listy wyników w panelu; reszta w eksporcie CSV / GeoJSON

/** Przycisk „Filtruj” do paska narzędzi (styl mr-btn--tool). Licznik pokazuje aktywny filtr. */
export function parcelSearchButton() {
  setTimeout(watchPopups, 0);   // dymki mapy nad chipem / legendą także bez aktywnego filtra
  return h('button', {
    type: 'button', class: 'mr-btn mr-btn--tool mr-ps-open', 'aria-haspopup': 'dialog', 'aria-expanded': 'false',
    onclick: (e) => (ui && !ui.panel.hidden ? close() : open(e.currentTarget)),
  }, [icon('filter'), h('span', null, ['Filtruj', h('span', { class: 'mr-btn__more', text: ' działki' })]),
    h('span', { class: 'mr-ps-count', hidden: true, 'aria-live': 'polite' })]);
}

// ------------------------------------------------------------------------------------------
// Pula działek i obszar
// ------------------------------------------------------------------------------------------
const pkey = (p) => `${p.parcel_id || ''}|${Number(p.lat).toFixed(6)}|${Number(p.lon).toFixed(6)}`;

// Moje działki = zestawy użytkownika (usersets.js) + „Pola dodane przeze mnie”; `skey` – klucz zestawu (nie idzie do serwera)
function fullPool() {
  const out = [];
  const add = (skey, farm, p) => out.push({ lat: p.lat, lon: p.lon, parcel_id: p.parcel_id || undefined,
    id_tag: (p.id_tag || '').slice(0, 120) || undefined, farm: farm.slice(0, 160), skey });
  (state.userSets || []).forEach((st) => (st.parcels || []).forEach((p) => add(setKey(st.id), st.name, p)));
  state.customParcels.forEach((p) => add(CUSTOM_FARM, 'Pola dodane przeze mnie', p));
  return out;
}

function scopePool() {
  const scope = ui.scope.value;
  const all = fullPool().filter((p) => scope === '__all' || p.skey === scope);
  if (scope !== '__all') return all;
  const seen = new Set();   // ta sama działka w kilku zestawach – raz
  return all.filter((p) => { const k = pkey(p); if (seen.has(k)) return false; seen.add(k); return true; });
}

function areaPool() {
  const [w, pw, g] = [ui.woj.value, ui.pow.value, ui.gm.value];
  return scopePool().filter((p) => {
    if (!w && !pw && !g) return true;
    const a = admin && admin.get(pkey(p));
    if (!a) return false;
    return (!w || a.woj === w) && (!pw || a.powiat === pw) && (!g || a.gm === g);
  }).slice(0, 300);
}

async function loadAdmin() {
  if (admin || adminLoading) return adminLoading;
  const pool = fullPool().slice(0, 300);
  if (!pool.length) return null;
  ui.areaNote.textContent = 'Wczytywanie jednostek administracyjnych (ULDK)…';
  adminLoading = api('/api/parcel-search/admin-values', { method: 'POST', body: { pool }, timeout: 120000 }).then((r) => {
    admin = new Map();
    (r.items || []).forEach((it) => {
      if (!it.resolved) return;
      const kind = GM_KIND[it.gmina.slice(7, 8)] || '';
      const gm = /[345]/.test(it.gmina.slice(7, 8)) ? `${it.gmina.slice(0, 6)}_3` : it.gmina;
      admin.set(pkey(pool[it.i]), { ...it, gm, gm_name: `${it.gmina_name}${kind ? ` (${kind})` : ''}`,
        powiat_name: (it.powiat_name || '').replace(/^powiat\s+/i, 'pow. ') });
    });
    ui.areaNote.textContent = r.resolved < r.pool_size ? `Bez granic ULDK: ${r.pool_size - r.resolved} działek (pominięte przy wyborze obszaru).` : '';
    fillArea();
  }).catch((err) => {
    adminLoading = null;
    ui.areaNote.textContent = `Nie wczytano jednostek administracyjnych: ${err.message}`;
  });
  return adminLoading;
}

function fillSelect(selEl, first, entries) {
  const cur = selEl.value;
  replace(selEl, [h('option', { value: '', text: first }), ...entries.sort((a, b) => collator.compare(a[1], b[1]))
    .map(([v, t, n]) => h('option', { value: v, text: n == null ? t : `${t} (${n})` }))]);
  selEl.value = entries.some(([v]) => v === cur) ? cur : '';
}

const isEgib = () => !!ui && ui.scope.value === EGIB;

async function unitsFor(woj, powiat) {
  const key = `${woj || ''}|${powiat || ''}`;
  if (!unitsCache.has(key)) {
    const qs = new URLSearchParams();
    if (woj) qs.set('woj', woj);
    if (powiat) qs.set('powiat', powiat);
    unitsCache.set(key, api(`/api/parcel-search/units?${qs}`, { timeout: 60000 }).catch((err) => { unitsCache.delete(key); throw err; }));
  }
  return unitsCache.get(key);
}

let areaSeq = 0;
async function fillAreaEgib() {
  const seq = ++areaSeq;
  ui.poolCount.textContent = 'Działki pobierane z ewidencji (EGiB, GUGiK) dla wybranej jednostki – do 20 000 na przebieg; '
    + 'po odrzuceniu za małych i nieforemnych do oceny trafia najwyżej 2000.';
  try {
    const u = await unitsFor(ui.woj.value, ui.woj.value ? ui.pow.value : '');
    if (seq !== areaSeq) return;
    fillSelect(ui.woj, 'Wybierz województwo', (u.woj || []).map((x) => [x.code, x.name]));
    fillSelect(ui.pow, 'Cały powiat / wybierz', (u.powiat || []).map((x) => [x.code, x.name.replace(/^Powiat\s+/i, 'pow. ')]));
    fillSelect(ui.gm, 'Cała jednostka / wybierz gminę', (ui.pow.value ? (u.gmina || []) : []).map((x) => [x.code, `${x.name}${x.kind ? ` (${x.kind})` : ''}`]));
    if (ui.pow.value && !u.gmina) { fillAreaEgib(); return; }   // powiat wybrany przed wczytaniem jego gmin
    ui.areaNote.textContent = !ui.woj.value ? 'Wybierz co najmniej gminę. Powiat lub województwo – tylko z kryterium „Odległość od miasta” albo „Odległość od cieków (najwyżej)”.' : '';
  } catch (err) {
    ui.areaNote.textContent = `Nie wczytano listy jednostek (GUS BDL): ${err.message}`;
  }
  ui.pow.disabled = !ui.woj.value;
  ui.gm.disabled = !ui.pow.value;
}

function egibArea() {
  const pick = (selEl) => (selEl.value ? (selEl.selectedOptions[0] || {}).textContent : '');
  const woj_name = pick(ui.woj);
  const powiat_name = pick(ui.pow);
  if (ui.gm.value) return { level: 'gmina', code: ui.gm.value, name: pick(ui.gm).replace(/\s*\(.*\)$/, ''), woj_name, powiat_name };
  if (ui.pow.value) return { level: 'powiat', code: ui.pow.value, name: powiat_name, woj_name, powiat_name };
  if (ui.woj.value) return { level: 'woj', code: ui.woj.value, name: woj_name, woj_name };
  return null;
}

function fillArea() {
  if (isEgib()) { fillAreaEgib(); return; }
  ui.areaNote.textContent = '';
  const pool = scopePool();
  const rows = admin ? pool.map((p) => admin.get(pkey(p))).filter(Boolean) : [];
  const count = (key, nameKey, list) => {
    const m = new Map();
    list.forEach((a) => { const e = m.get(a[key]) || [a[key], a[nameKey], 0]; e[2] += 1; m.set(a[key], e); });
    return [...m.values()];
  };
  fillSelect(ui.woj, 'Cała Polska', count('woj', 'woj_name', rows));
  const r1 = rows.filter((a) => !ui.woj.value || a.woj === ui.woj.value);
  fillSelect(ui.pow, 'Wszystkie powiaty', count('powiat', 'powiat_name', r1));
  const r2 = r1.filter((a) => !ui.pow.value || a.powiat === ui.pow.value);
  fillSelect(ui.gm, 'Wszystkie gminy', count('gm', 'gm_name', r2));
  ui.pow.disabled = !ui.woj.value;
  ui.gm.disabled = !ui.pow.value;
  ui.poolCount.textContent = `Działek w zakresie: ${areaPool().length}`;
}

// ------------------------------------------------------------------------------------------
// Panel
// ------------------------------------------------------------------------------------------
function labeled(label, el) {
  return h('div', { class: 'mr-field' }, [h('label', { class: 'mr-label', for: el.id, text: label }), el]);
}

function build() {
  const titleId = nextId('mr-ps-title');
  const mk = (cls) => h('select', { class: `mr-select ${cls || ''}`, id: nextId('mr-ps-sel') });
  const scope = mk();
  const woj = mk();
  const pow = mk();
  const gm = mk();
  scope.addEventListener('change', () => { [woj, pow, gm].forEach((x) => replace(x, [])); fillArea(); });
  [woj, pow, gm].forEach((s) => s.addEventListener('change', () => fillArea()));
  const areaNote = h('p', { class: 'mr-hint', role: 'status' });
  const poolCount = h('p', { class: 'mr-ps__poolcount' });
  const status = h('p', { class: 'mr-ps__status', role: 'status', 'aria-live': 'polite' });
  const bar = h('progress', { class: 'mr-ps__bar', max: '100', value: '0', hidden: true, 'aria-label': 'Postęp filtrowania' });
  const runBtn = h('button', { type: 'button', class: 'mr-btn mr-btn--primary mr-ps__run', onclick: run }, [icon('filter'), 'Filtruj']);
  const clearBtn = h('button', { type: 'button', class: 'mr-btn mr-btn--secondary mr-ps__clear', hidden: true, onclick: () => clearFilter() }, [icon('cross'), 'Wyczyść filtr']);
  const saveBtn = h('button', { type: 'button', class: 'mr-btn mr-ps__save', hidden: true, onclick: () => saveResults() });
  const results = h('div', { class: 'mr-ps__results' });
  const sources = h('div', { class: 'mr-card mr-ps__sources', hidden: true });
  const zonesToggle = h('input', { type: 'checkbox', class: 'mr-check', id: nextId('mr-ps-zones'), onchange: () => drawZones() });
  const form = h('div', { class: 'mr-ps__groups' }, GROUPS.map(group));
  const closeBtn = h('button', { type: 'button', class: 'mr-btn mr-btn--ghost mr-btn--icon mr-ps__close', 'aria-label': 'Zamknij filtrowanie działek', onclick: () => close() }, icon('cross'));
  const minBtn = h('button', { type: 'button', class: 'mr-btn mr-btn--ghost mr-btn--icon mr-ps__min', title: 'Zminimalizuj',
    'aria-label': 'Zminimalizuj panel – filtrowanie działa dalej w tle', onclick: () => minimize() }, icon('minimize'));
  const panel = h('aside', { class: 'mr-ps', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId, hidden: true }, [
    h('div', { class: 'mr-ps__head' }, [h('h2', { id: titleId, class: 'mr-ps__title' }, [icon('filter'), 'Filtruj działki']), minBtn, closeBtn]),
    h('div', { class: 'mr-ps__body' }, [
      h('fieldset', { class: 'mr-fieldset mr-ps__scope' }, [
        h('legend', { class: 'mr-label', text: 'Szukaj w' }),
        labeled('Działki', scope),
        h('div', { class: 'mr-ps__area' }, [labeled('Województwo', woj), labeled('Powiat', pow), labeled('Gmina', gm)]),
        areaNote, poolCount,
      ]),
      form,
      h('div', { class: 'mr-ps__actions' }, [runBtn, clearBtn,
        h('div', { class: 'mr-field mr-field--check mr-ps__zones' }, [zonesToggle, h('label', { for: zonesToggle.id, text: 'Pokaż strefy kryteriów' })])]),
      bar, status, saveBtn, results, sources,
    ]),
  ]);
  const backdrop = h('div', { class: 'mr-ps-backdrop', hidden: true, onclick: () => close() });
  panel.addEventListener('keydown', trapKeys);
  // Esc zamyka panel także wtedy, gdy fokus wypadł poza niego (np. po wyłączeniu przycisku „Filtruj” na czas filtrowania)
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && ui && !ui.panel.hidden && !ui.panel.contains(e.target)) { e.preventDefault(); close(); }
  });
  const root = document.getElementById('mrolnik-root') || document.body;
  root.append(backdrop, panel);
  ui = { panel, backdrop, scope, woj, pow, gm, areaNote, poolCount, status, bar, runBtn, clearBtn, saveBtn, results, sources, zonesToggle, form, inputs: {} };
  form.querySelectorAll('[data-crit]').forEach((el) => {
    if (el.dataset.field || el.dataset.role) (ui.inputs[el.dataset.crit] ||= []).push(el);
  });
}

function fillScope() {
  const cur = ui.scope.value;
  const all = new Set(fullPool().map(pkey));
  const opts = [
    h('option', { value: EGIB, text: 'Wszystkie działki w wybranym obszarze (EGiB)' }),
    h('option', { value: '__all', text: `Wszystkie Moje działki (${all.size})` }),
  ];
  [...(state.userSets || [])].sort((a, b) => collator.compare(a.name, b.name))
    .forEach((st) => opts.push(h('option', { value: setKey(st.id), text: `${st.name} (${(st.parcels || []).length})` })));
  if (state.customParcels.length) opts.push(h('option', { value: CUSTOM_FARM, text: `Pola dodane przeze mnie (${state.customParcels.length})` }));
  replace(ui.scope, opts);
  ui.scope.value = cur && [...ui.scope.options].some((o) => o.value === cur) ? cur : EGIB;
  fillArea();
}

function group(g) {
  const bodyId = nextId('mr-ps-g');
  const body = h('div', { id: bodyId, class: 'mr-ps__gbody', hidden: !g.open }, g.items.map(criterion));
  const btn = h('button', {
    type: 'button', class: 'mr-ps__gbtn', 'aria-expanded': String(!!g.open), 'aria-controls': bodyId,
    onclick: () => { const o = btn.getAttribute('aria-expanded') === 'true'; btn.setAttribute('aria-expanded', String(!o)); body.hidden = o; },
  }, [h('span', { text: g.title }), icon('chevronDown', 'mr-ps__chev')]);
  return h('section', { class: 'mr-ps__group' }, [h('h3', { class: 'mr-ps__gtitle' }, btn), body]);
}

function criterion(key) {
  const d = DEF[key];
  const onId = nextId('mr-ps-on');
  const on = h('input', { type: 'checkbox', class: 'mr-check', id: onId, dataset: { crit: key, role: 'on' }, checked: DEFAULT_ON.has(key) || null });
  const params = h('div', { class: 'mr-ps__params' }, d.fields.map((f) => field(key, f)));
  const wrap = h('div', { class: 'mr-ps__crit', dataset: { crit: key } }, [
    h('div', { class: 'mr-ps__crithead' }, [h('div', { class: 'mr-field mr-field--check' }, [on, h('label', { for: onId, class: 'mr-label', text: d.label })]),
      d.hint ? infoTip(d.label, d.hint) : null]),
    params,
  ]);
  const sync = () => { wrap.classList.toggle('mr-ps__crit--off', !on.checked); params.hidden = !on.checked; };
  on.addEventListener('change', sync);
  sync();
  return wrap;
}

function field(key, f) {
  const id = nextId('mr-ps-f');
  const data = { crit: key, field: f.key, type: f.type };
  if (f.type === 'num') {
    return h('div', { class: 'mr-field' }, [h('label', { class: 'mr-label', for: id, text: f.label }),
      h('input', { id, class: 'mr-input', type: 'number', inputmode: 'decimal', step: String(f.step || 'any'), min: '0', value: f.def ?? '', dataset: data })]);
  }
  if (f.type === 'sel') {
    const s = h('select', { id, class: 'mr-select', dataset: data }, f.opts.map(([v, t]) => h('option', { value: v, text: t })));
    s.value = f.def;
    return h('div', { class: 'mr-field' }, [h('label', { class: 'mr-label', for: id, text: f.label }), s]);
  }
  return h('fieldset', { class: 'mr-fieldset mr-ps__checks' }, [h('legend', { class: 'mr-label', text: f.label }),
    ...f.opts.map(([v, t]) => {
      const cid = nextId('mr-ps-c');
      return h('div', { class: 'mr-field mr-field--check' }, [
        h('input', { id: cid, type: 'checkbox', class: 'mr-check', value: v, checked: f.def.includes(v) || null, dataset: data }),
        h('label', { for: cid, text: t })]);
    })]);
}

function readCriteria() {
  const out = {};
  const errors = [];
  for (const key of Object.keys(DEF)) {
    const els = ui.inputs[key] || [];
    const on = els.find((e) => e.dataset.role === 'on');
    if (!on || !on.checked) continue;
    const c = { enabled: true, required: true };
    for (const f of DEF[key].fields) {
      const fe = els.filter((e) => e.dataset.field === f.key);
      if (f.type === 'num') {
        const raw = (fe[0].value || '').replace(',', '.').trim();
        fe[0].removeAttribute('aria-invalid');
        if (raw === '') { c[f.key] = null; continue; }
        const v = Number(raw);
        if (!Number.isFinite(v) || v < (key === 'ndvi' ? -1 : 0)) { fe[0].setAttribute('aria-invalid', 'true'); errors.push(`${DEF[key].label}: niepoprawna wartość „${f.label}”`); continue; }
        c[f.key] = f.key === 'min_pop' ? Math.round(v) : v;
      } else if (f.type === 'sel') {
        c[f.key] = fe[0].value;
      } else {
        c[f.key] = fe.filter((e) => e.checked).map((e) => e.value);
        if (!c[f.key].length) errors.push(`${DEF[key].label}: zaznacz co najmniej jedną opcję`);
      }
    }
    for (const k of ['dist_m', 'max_km']) if (k in c && c[k] === null) errors.push(`${DEF[key].label}: podaj wartość progu`);
    if (key === 'flood' && c.mode === 'max_share' && c.max_pct === null) errors.push('Zagrożenie powodziowe: podaj X [%]');
    if (key === 'ndvi' && c.min_mean === null) errors.push('Kondycja roślin: podaj próg NDVI');
    if (key === 'merge' && !c.min_ha) errors.push('Możliwość scalenia: podaj powierzchnię grupy');
    out[key] = c;
  }
  return { criteria: out, errors };
}

function open(btn) {
  if (!ui) build();
  lastFocus = btn || document.activeElement;
  fillScope();
  loadAdmin();
  ui.panel.hidden = false;
  ui.backdrop.hidden = false;
  pillState = null;
  renderPill();
  document.querySelectorAll('.mr-ps-open').forEach((b) => b.setAttribute('aria-expanded', 'true'));
  ui.panel.querySelector('.mr-ps__close').focus({ preventScroll: true });
}

function running() {
  // „Filtruj” kliknięte, a serwer jeszcze nie odpowiedział (przycisk wyłączony) – to też trwające filtrowanie
  return !!((ui && ui.runBtn.disabled) || (job && (!job.data || job.data.state === 'running')));
}

/** Chowa panel; filtrowanie trwa dalej, a jego stan pokazuje pastylka na mapie (klik – powrót do panelu). */
function minimize() {
  if (!ui || ui.panel.hidden) return;
  pillState = running() ? 'run' : (job && job.data ? (job.data.state === 'done' && !job.data.too_big ? 'ok' : 'err') : null);
  hidePanel();
  renderPill();
  announce('Panel filtrowania zminimalizowany. Filtrowanie trwa w tle.');
}

function close() {
  if (!ui || ui.panel.hidden) return;
  if (running()) { minimize(); return; }   // X w trakcie filtrowania nie przerywa pracy
  hidePanel();
}

function hidePanel() {
  ui.panel.hidden = true;
  ui.backdrop.hidden = true;
  document.querySelectorAll('.mr-ps-open').forEach((b) => b.setAttribute('aria-expanded', 'false'));
  if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
}

function trapKeys(e) {
  if (e.key === 'Escape') {
    if (e.target.closest('.mr-pop:not([hidden])')) return;   // najpierw zamyka się rozwinięta podpowiedź / źródło
    e.preventDefault(); close(); return;
  }
  if (e.key !== 'Tab') return;
  const f = [...ui.panel.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter((x) => !x.disabled && x.offsetParent !== null);
  if (!f.length) return;
  const first = f[0];
  const last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

// ------------------------------------------------------------------------------------------
// Filtrowanie
// ------------------------------------------------------------------------------------------
async function run() {
  const { criteria, errors } = readCriteria();
  let body;
  if (isEgib()) {
    const area = egibArea();
    const narrows = criteria.town || (criteria.water && criteria.water.op === 'le');
    if (!area) errors.push('Wybierz co najmniej gminę (albo powiat lub województwo z kryterium zawężającym obszar).');
    else if (area.level !== 'gmina' && !narrows) {
      errors.push('Obszar jest za duży: dla powiatu lub województwa włącz „Odległość od miasta” albo „Odległość od cieków i wód” (najwyżej X m) albo wybierz gminę.');
    }
    body = { source: 'egib', area, criteria };
  } else {
    if ((ui.woj.value || ui.pow.value || ui.gm.value) && !admin) await loadAdmin();
    const pool = areaPool().map(({ skey, ...p }) => p);   // eslint-disable-line no-unused-vars
    if (!pool.length) errors.push('Brak działek w wybranym zakresie.');
    body = { pool, criteria };
  }
  if (!Object.keys(criteria).length) errors.push('Włącz co najmniej jedno kryterium.');
  if (errors.length) {
    replace(ui.status, h('span', { class: 'mr-error', role: 'alert', text: errors.join(' · ') }));
    return;
  }
  if (job && job.timer) clearTimeout(job.timer);
  ui.runBtn.disabled = true;
  ui.bar.hidden = false;
  ui.bar.value = 0;
  ui.status.textContent = body.source === 'egib' ? 'Filtrowanie działek z ewidencji (EGiB)…' : `Filtrowanie ${body.pool.length} działek…`;
  replace(ui.results, []);
  try {
    const r = await api('/api/parcel-search', { method: 'POST', body, timeout: 30000 });
    dropLive();
    job = { id: r.job_id, timer: null, data: null, cursor: 0, candCursor: 0 };
    poll();
  } catch (err) {
    ui.runBtn.disabled = false;
    ui.bar.hidden = true;
    replace(ui.status, h('span', { class: 'mr-error', role: 'alert', text: errText(err) }));
  }
}

/** Komunikat błędu API; dla 422 – treść walidacji z serwera (np. „Obszar jest za duży…”). */
function errText(err) {
  const d = err && err.detail;
  if (Array.isArray(d) && d.length) return d.map((x) => String((x && x.msg) || '').replace(/^Value error,\s*/, '')).filter(Boolean).join(' ') || err.message;
  return err.message;
}

async function poll() {
  const my = job;
  try {
    // w trakcie: tylko przyrost (nowe gotowe działki i kandydaci) + liczniki; po zakończeniu – raz pełny stan
    let d = await api(`/api/parcel-search/${my.id}?since=${my.cursor || 0}&cand_since=${my.candCursor || 0}`, { timeout: 30000 });
    if (job !== my) return;
    if (d.state === 'running') {
      my.data = d;
      liveUpdate(my, d);
      my.timer = setTimeout(poll, 1500);
      return;
    }
    d = await api(`/api/parcel-search/${my.id}`, { timeout: 60000 });
    if (job !== my) return;
    my.data = d;
    dropLive();
    render(d);
    ui.runBtn.disabled = false;
    ui.bar.hidden = true;
    if (!ui.panel.hidden && !ui.panel.contains(document.activeElement)) ui.runBtn.focus({ preventScroll: true });
    if (d.state === 'done') {
      applyFilter(d);
      state.filterResult = { provenance: d.provenance };   // „Źródła użyte w analizie”
      const ps = resultParcels(d);
      if (ps.length) {
        if (state.farmName !== FILTER_FARM) state.prevFarm = state.farmName;
        showFilterSet(ps, `Wynik filtra – ${ps.length} ${plural(ps.length, 'działka', 'działki', 'działek')}`);
      }
    }
    announce(summaryText(d));
  } catch (err) {
    if (job !== my) return;
    ui.runBtn.disabled = false;
    ui.bar.hidden = true;
    replace(ui.status, h('span', { class: 'mr-error', role: 'alert', text: err.message }));
    if (ui.panel.hidden && pillState) { pillState = 'err'; renderPill(); }
    renderSave({ state: 'error', error: err.message });
  }
}

function sorted(rows) {
  return [...rows].sort((a, b) => (STATUS[a.status].order - STATUS[b.status].order)
    || (a.status === 'nie' ? (a.failed.length - b.failed.length) : 0)
    || collator.compare(a.name || '', b.name || ''));
}

function summaryText(d) {
  const c = d.counts || {};
  const withGeom = (d.results || []).length - (c.brak_geometrii || 0);
  return `${c.spelnia || 0} z ${withGeom || d.pool_size} spełnia` + (c.do_sprawdzenia ? `, ${c.do_sprawdzenia} do sprawdzenia` : '');
}

function stepText(d) {
  const st = d.step || {};
  return `Krok ${st.n || 1} z ${st.of || 1}: ${(st.label || 'pracuję').toLowerCase()} (${st.pct || 0}%)`;
}

function stepDetail(d) {
  const st = d.step || {};
  const parts = [];
  if (st.found != null) parts.push(`Znaleziono dotąd: ${st.found} ${plural(st.found, 'działkę', 'działki', 'działek')}`);
  if (st.eta_s) parts.push(`zostało ok. ${st.eta_s < 60 ? `${st.eta_s} s` : `${Math.round(st.eta_s / 60)} min`}`);
  return parts.join(' · ');
}

function plural(n, one, few, many) {
  const m10 = n % 10;
  const m100 = n % 100;
  if (n === 1) return one;
  return m10 >= 2 && m10 <= 4 && !(m100 >= 12 && m100 <= 14) ? few : many;
}

function hitsOf(d) {
  return (d.results || []).filter((r) => r.status === 'spelnia' || r.status === 'do_sprawdzenia');
}

/** Jeden przycisk: stan wczytywania wszystkich kryteriów, a po zakończeniu – zapis wyników w Moich działkach. */
function renderSave(d) {
  const b = ui.saveBtn;
  b.hidden = false;
  b.classList.remove('mr-btn--primary', 'mr-btn--secondary', 'mr-ps__save--err');
  if (d.state === 'running') {
    b.disabled = true;
    b.classList.add('mr-btn--secondary');
    replace(b, [h('span', { class: 'mr-ps-pill__spin', 'aria-hidden': 'true' }), `Wczytuję filtry… ${(d.step && d.step.pct) || 0}%`]);
    return;
  }
  if (d.state !== 'done' || d.error) {
    b.disabled = true;
    b.classList.add('mr-ps__save--err');
    replace(b, [icon('warn'), `Filtry nie wczytały się: ${d.error || d.message || 'błąd serwera'}`]);
    return;
  }
  const n = hitsOf(d).length;
  b.disabled = !n;
  b.classList.add(n ? 'mr-btn--primary' : 'mr-btn--secondary');
  replace(b, n ? [icon('plus'), `Zapisz ${n} ${plural(n, 'działkę', 'działki', 'działek')} w Moich działkach`] : [icon('info'), 'Brak działek do zapisania']);
}

function resultParcels(d) {
  return hitsOf(d).map((r) => ({ lat: r.lat, lon: r.lon, parcel_id: r.parcel_id || undefined, id_tag: (r.name || r.parcel_id || '').slice(0, 60) || undefined }));
}

async function saveResults() {
  const d = job && job.data;
  if (!d || d.state !== 'done') return;
  const s = await saveDialog({ parcels: resultParcels(d) });
  if (s) replace(ui.status, h('span', { class: 'mr-ok', text: `Zapisano w Moich działkach: „${s.name}” (${s.parcels.length} ${plural(s.parcels.length, 'działka', 'działki', 'działek')}).` }));
}

function render(d) {
  renderSave(d);
  const st = d.step || {};
  // procent nigdy nie cofa się w obrębie jednego filtrowania (etapy równoległe zgłaszają swoje sumy w różnym czasie)
  if (job && d.state === 'running') { job.maxPct = Math.max(job.maxPct || 0, st.pct || 0); st.pct = job.maxPct; }
  ui.bar.value = d.state === 'running' ? (st.pct || 0) : 100;
  if (d.state === 'running') {
    const det = stepDetail(d);
    replace(ui.status, [h('strong', { class: 'mr-ps__step', text: stepText(d) }), det ? h('span', { class: 'mr-ps__stepdet', text: det }) : null]);
  } else {
    const info = [];
    if (d.egib_stats) {
      const es = d.egib_stats;
      info.push(`EGiB: pobrano ${es.fetched} działek, w obszarze ${es.in_area}, odrzucono ${es.cheap_rejected} po powierzchni i kształcie, oceniono ${(d.results || []).length}.`);
    }
    (d.warnings || []).forEach((w) => info.push(w));
    replace(ui.status, [
      h('strong', { class: 'mr-ps__count', text: d.results && d.results.length ? summaryText(d) : (d.too_big ? 'Obszar za duży' : 'Brak działek do pokazania') }),
      info.length ? infoTip('Szczegóły filtrowania', info.join(' ')) : null,
      d.error ? h('span', { class: 'mr-error', text: ` ${d.error}` }) : null,
    ]);
  }
  if (ui.panel.hidden && pillState) {
    pillState = d.state === 'running' ? 'run' : (d.state === 'done' && !d.too_big && !d.error ? 'ok' : 'err');
    renderPill();
  }
  if (d.state === 'running') return;   // w trakcie lista i mapa dopisują się przyrostowo (liveUpdate)
  const rows = sorted(d.results || []);
  const crits = Object.keys(d.criteria || {});
  const hits = rows.filter((r) => r.status === 'spelnia' || r.status === 'do_sprawdzenia');
  const parts = [];
  if (d.too_big) {
    parts.push(h('div', { class: 'mr-ps__toobig', role: 'alert' }, [
      h('strong', { text: 'Obszar za duży' }), h('p', { text: d.too_big.message }),
      h('p', { class: 'mr-hint', text: 'Jak zawęzić: wybierz gminę zamiast powiatu, zmniejsz odległość od miasta lub od cieków, albo podnieś minimalną powierzchnię.' }),
    ]));
  }
  if (d.error) parts.push(h('p', { class: 'mr-error', role: 'alert', text: d.error }));
  if (rows.length && d.state !== 'running') {
    parts.push(h('div', { class: 'mr-ps__export' }, [
      h('button', { type: 'button', class: 'mr-btn mr-btn--small mr-btn--secondary', onclick: () => exportCsvRows(d) }, [icon('download'), 'Eksport CSV']),
      h('button', { type: 'button', class: 'mr-btn mr-btn--small mr-btn--secondary', onclick: () => exportGeo(d) }, [icon('download'), 'Eksport GeoJSON']),
    ]));
  }
  if (d.state !== 'running' && rows.length && !hits.length) {
    const near = rows.filter((r) => r.status === 'nie').slice(0, 5);
    parts.push(h('h3', { class: 'mr-ps__h', text: 'Żadna działka nie spełnia wszystkich kryteriów – najbliżej spełnienia:' }));
    parts.push(h('ol', { class: 'mr-ps__list' }, near.map((r) => item(r, crits, d))));
  }
  if (rows.length) {
    parts.push(h('h3', { class: 'mr-ps__h', text: 'Wyniki' }));
    parts.push(h('ol', { class: 'mr-ps__list' }, rows.slice(0, LIST_MAX).map((r) => item(r, crits, d))));
    if (rows.length > LIST_MAX) {
      parts.push(h('p', { class: 'mr-hint', text: `Pokazano ${LIST_MAX} pierwszych z ${rows.length} działek; pozostałe są w eksporcie CSV / GeoJSON i na mapie.` }));
    }
  }
  if (d.groups && d.groups.length) {
    parts.push(h('h3', { class: 'mr-ps__h', text: 'Grupy przylegających działek' }));
    parts.push(h('ul', { class: 'mr-ps__groupsres' }, d.groups.map((g) => h('li', { class: g.meets ? 'mr-ps__grp--ok' : '' }, [
      h('strong', { text: `${String(g.area_ha).replace('.', ',')} ha` }), ` – ${g.members.length} działek: `,
      [...g.names].sort(collator.compare).join(', '), g.meets ? ' ✓' : '']))));
  }
  replace(ui.results, parts);
  renderSources(d);
}

function item(r, crits, d) {
  const st = STATUS[r.status] || STATUS.w_toku;
  const marks = crits.map((k) => {
    const c = (r.criteria || {})[k] || { status: 'pending' };
    const cs = CSTAT[c.status] || CSTAT.pending;
    const label = (d.criteria[k] && d.criteria[k].label) || k;
    return h('li', { class: `mr-ps__mark mr-ps__mark--${c.status}`, title: `${label}: ${c.text || cs.sr}${c.note ? ` (${c.note})` : ''}` }, [
      icon(cs.icon),
      h('span', { class: 'mr-ps__marktxt' }, [h('span', { class: 'mr-ps__marklab', text: label }), ': ', c.text || cs.sr]),
      h('span', { class: 'mr-visually-hidden', text: ` – ${cs.sr}` }),
    ]);
  });
  return h('li', { class: `mr-ps__item mr-ps__item--${st.cls}` }, [
    h('div', { class: 'mr-ps__itemhead' }, [
      h('span', { class: `mr-ps__badge mr-ps__badge--${st.cls}` }, [icon(st.icon), st.text]),
      h('strong', { class: 'mr-ps__name', text: r.name || r.parcel_id || 'Działka' }),
    ]),
    h('p', { class: 'mr-ps__meta', text: [r.parcel_id, r.farm, r.commune ? `gm. ${r.commune}` : null, r.area_ha != null ? `${String(r.area_ha).replace('.', ',')} ha` : null].filter(Boolean).join(' · ') }),
    r.status === 'nie' && r.failed.length ? h('p', { class: 'mr-ps__why', text: `Nie spełnia: ${r.failed.join(', ')}` }) : null,
    r.unknown && r.unknown.length ? h('p', { class: 'mr-ps__why mr-ps__why--unk', text: `Brak danych: ${r.unknown.join(', ')}` }) : null,
    h('ul', { class: 'mr-ps__marks', 'aria-label': 'Kryteria' }, marks),
    h('div', { class: 'mr-ps__acts' }, [
      r.geometry && r.geometry.length ? h('button', { type: 'button', class: 'mr-btn mr-btn--small mr-btn--secondary', onclick: () => showOnMap(r) }, [icon('pin'), 'Pokaż na mapie']) : null,
      h('button', { type: 'button', class: 'mr-btn mr-btn--small mr-btn--ghost', onclick: () => saveDialog({
        parcels: [{ lat: r.lat, lon: r.lon, id_tag: (r.name || r.parcel_id || '').slice(0, 60) || undefined, parcel_id: r.parcel_id || undefined }],
        title: 'Dodaj działkę do Moich działek', suggested: r.name || r.parcel_id || 'Moje działki', preferExisting: true }) },
      [icon('plus'), 'Dodaj']),
    ]),
  ]);
}

function renderSources(d) {
  const provs = Object.entries(d.provenance || {});
  ui.sources.hidden = !provs.length;
  if (!provs.length) return;
  const lab = (k) => (d.criteria[k] && d.criteria[k].label) || (k === 'parcel' ? 'Granice działek' : k);
  replace(ui.sources, h('span', { class: 'mr-chips' }, provs.map(([k, p]) => provChip(p, { label: `${lab(k)}: ${p.source}`.slice(0, 80) }))));
}

// ------------------------------------------------------------------------------------------
// Filtr mapy (zostaje po zamknięciu panelu, do „Wyczyść filtr”)
// ------------------------------------------------------------------------------------------
function applyFilter(d) {
  const keep = (d.results || []).filter((r) => r.status === 'spelnia' || r.status === 'do_sprawdzenia');
  const ids = new Set(keep.map((r) => r.parcel_id).filter(Boolean));
  const pts = new Set(keep.map((r) => `${Number(r.lat).toFixed(6)}|${Number(r.lon).toFixed(6)}`));
  const total = (d.results || []).length;
  filterInfo = { ids, shown: keep.length, ok: keep.filter((r) => r.status === 'spelnia').length, total, data: d };
  // działka z analizy pasuje po identyfikatorze ULDK, a gdy go nie ma – po punkcie z żądania
  const fn = (p) => (p.parcel_id && ids.has(p.parcel_id)) || (Array.isArray(p.centroid) && pts.has(`${Number(p.centroid[0]).toFixed(6)}|${Number(p.centroid[1]).toFixed(6)}`));
  state.parcelFilter = fn;
  setParcelFilter(fn);
  drawResults(d);
  emit('parcels:filter', filterInfo);
  renderIndicator();
  ui.clearBtn.hidden = false;
  loadIndex();   // widok mapy ustawiony raz, na początku filtrowania (obszar wyszukiwania) – bez przeskoku na końcu
}

function clearFilter() {
  dropIndex();
  filterInfo = null;
  state.filterResult = null;
  if (state.farmName === FILTER_FARM) {   // Główna wraca do poprzedniego gospodarstwa / zestawu
    state.filterSet = null;
    emit('farm:change', state.prevFarm || (state.presets[0] && state.presets[0].name) || CUSTOM_FARM);
  }
  state.parcelFilter = null;
  setParcelFilter(null);
  const map = getMap();
  if (map && resultLayer) { map.removeLayer(resultLayer); resultLayer = null; }
  if (map && zoneLayer) { map.removeLayer(zoneLayer); zoneLayer = null; }
  if (ui) { ui.clearBtn.hidden = true; ui.zonesToggle.checked = false; }
  emit('parcels:filter', null);
  renderIndicator();
  announce('Wyczyszczono filtr działek – mapa pokazuje wszystkie działki.');
}

// Dymki mapy (pinezka, działka) leżą w panelu mapy Leaflet, pod kontrolkami – na czas otwartego dymka chip i pastylka
// filtra chowają się (wracają po zamknięciu), a dymek przesuwa się poniżej miejsca chipa.
let popupsWatched = false;
function watchPopups() {
  const map = getMap();
  if (popupsWatched || !map || typeof L === 'undefined') return;
  popupsWatched = true;
  L.Popup.mergeOptions({ autoPanPaddingTopLeft: L.point(10, 64), autoPanPaddingBottomRight: L.point(10, 56) });
  const app = document.querySelector('.mr-app') || document.body;
  map.on('popupopen', () => app.classList.add('mr-popup-open'));
  map.on('popupclose', () => app.classList.remove('mr-popup-open'));
}

function renderIndicator() {
  document.querySelectorAll('.mr-ps-count').forEach((c) => {
    c.hidden = !filterInfo;
    c.textContent = filterInfo ? String(filterInfo.shown) : '';
    if (filterInfo) c.setAttribute('aria-label', `aktywny filtr: ${filterInfo.shown} działek`);
  });
  const wrap = document.querySelector('.mr-mapwrap');
  if (!wrap) return;
  if (!filterInfo) { if (chip) chip.hidden = true; return; }
  if (!chip) {
    chip = h('div', { class: 'mr-ps-chip', role: 'status' });
    wrap.append(chip);
    watchPopups();
  }
  chip.hidden = false;
  const extra = filterInfo.shown > filterInfo.ok ? ` (${filterInfo.shown - filterInfo.ok} do sprawdzenia)` : '';
  const txt = filterInfo.live
    ? `Filtruję… ${filterInfo.pct || 0}% · ${filterInfo.ok} ${plural(filterInfo.ok, 'działka spełnia', 'działki spełniają', 'działek spełnia')}`
    : `Filtr: ${filterInfo.shown} z ${filterInfo.total}${extra}`;
  replace(chip, [
    icon('filter'),
    h('span', { class: 'mr-ps-chip__txt', text: txt }),
    filterInfo.indexNote ? h('span', { class: 'mr-ps-chip__note', text: filterInfo.indexNote }) : null,
    h('button', { type: 'button', class: 'mr-ps-chip__btn', onclick: fitResults }, 'Pokaż'),
    h('button', { type: 'button', class: 'mr-ps-chip__btn', onclick: () => clearFilter() }, 'Wyczyść'),
  ]);
}

// warstwa wyników: działki spełniające (zielone) i do sprawdzenia (szare przerywane) ze wszystkich gospodarstw razem
// warstwa wyników: działki spełniające (zielone) i do sprawdzenia (szare przerywane) ze wszystkich gospodarstw / z EGiB razem.
// Jeden L.geoJSON na rendererze canvas – wydajnie także dla setek działek; kolory z tokenów motywu (.mr-app).
let canvasRenderer = null;
function drawResults(d) {
  const map = getMap();
  if (!map || typeof L === 'undefined') return;
  if (resultLayer) map.removeLayer(resultLayer);
  const feats = (d.results || []).filter((r) => (r.status === 'spelnia' || r.status === 'do_sprawdzenia') && r.geometry && r.geometry.length > 2).map((r) => ({
    type: 'Feature', properties: { status: r.status, name: r.name, id: r.parcel_id, farm: r.farm },
    geometry: { type: 'Polygon', coordinates: [r.geometry] },
  }));
  const css = getComputedStyle(document.querySelector('.mr-app') || document.documentElement);
  const ok = css.getPropertyValue('--mr-ok').trim() || '#1d6b34';
  const mid = css.getPropertyValue('--mr-none').trim() || '#4d4d4d';
  if (!canvasRenderer) canvasRenderer = L.canvas({ padding: 0.3 });
  resultLayer = L.geoJSON({ type: 'FeatureCollection', features: feats }, {
    renderer: canvasRenderer,
    style: (f) => (f.properties.status === 'spelnia'
      ? { color: ok, fillColor: ok, weight: 2.5, fillOpacity: 0.15 }
      : { color: mid, fillColor: mid, weight: 2, dashArray: '6 4', fillOpacity: 0.05 }),
    onEachFeature: (f, layer) => layer.bindTooltip(`${f.properties.name || f.properties.id || ''} – ${STATUS[f.properties.status].text}`),
  }).addTo(map);
  drawZones();
}

function fitResults() {
  const map = getMap();
  if (!map || !resultLayer) return;
  const b = resultLayer.getBounds();
  if (b.isValid()) map.fitBounds(b, { padding: [40, 40], maxZoom: 16 });
}

function drawZones() {
  const map = getMap();
  if (!map || typeof L === 'undefined') return;
  if (zoneLayer) { map.removeLayer(zoneLayer); zoneLayer = null; }
  const d = filterInfo && filterInfo.data;
  if (!ui || !ui.zonesToggle.checked || !d || !d.zones) return;
  const feats = [];
  if (d.zones.area && d.zones.area.geojson) feats.push({ type: 'Feature', properties: { kind: 'area' }, geometry: d.zones.area.geojson });
  if (d.zones.water && d.zones.water.geojson) feats.push({ type: 'Feature', properties: { kind: 'water' }, geometry: d.zones.water.geojson });
  ((d.zones.town && d.zones.town.items) || []).forEach((t) => feats.push({ type: 'Feature', properties: { kind: 'town' }, geometry: t.geojson }));
  (d.groups || []).forEach((g) => { if (g.geojson) feats.push({ type: 'Feature', properties: { kind: 'group' }, geometry: g.geojson }); });
  if (!feats.length) { announce('Brak stref do pokazania dla wybranych kryteriów.'); return; }
  zoneLayer = L.geoJSON({ type: 'FeatureCollection', features: feats }, {
    style: (f) => ({ className: `mr-ps-zone mr-ps-zone--${f.properties.kind}`, weight: 1, fillOpacity: 0.12, interactive: false }),
  }).addTo(map);
  zoneLayer.bringToBack();
}

function showOnMap(r) {
  const map = getMap();
  if (!map || typeof L === 'undefined') return;
  const b = L.latLngBounds(r.geometry.map(([lon, lat]) => [lat, lon]));
  map.fitBounds(b, { maxZoom: 17, padding: [40, 40] });
  if (window.matchMedia('(max-width: 899px)').matches) close();
  announce(`Pokazano na mapie: ${r.name || r.parcel_id}`);
}

// ------------------------------------------------------------------------------------------
// Eksport
// ------------------------------------------------------------------------------------------
function exportCsvRows(d) {
  const crits = Object.keys(d.criteria || {});
  const cols = [
    { label: 'status', get: (r) => STATUS[r.status].text },
    { label: 'parcel_id', get: (r) => r.parcel_id || '' }, { label: 'nazwa', get: (r) => r.name || '' },
    { label: 'gospodarstwo', get: (r) => r.farm || '' }, { label: 'gmina', get: (r) => r.commune || '' },
    { label: 'powiat', get: (r) => r.county || '' }, { label: 'area_ha', get: (r) => (r.area_ha ?? '') },
    { label: 'lat', get: (r) => r.lat }, { label: 'lon', get: (r) => r.lon },
    ...crits.flatMap((k) => [
      { label: `${k}_status`, get: (r) => ((r.criteria[k] || {}).status || '') },
      { label: `${k}_wartosc`, get: (r) => ((r.criteria[k] || {}).text || '') },
    ]),
  ];
  downloadText('mrolnik-filtr-dzialek.csv', exportCsv(cols, sorted(d.results || [])));
}

function exportGeo(d) {
  const fc = {
    type: 'FeatureCollection',
    features: sorted(d.results || []).filter((r) => r.geometry && r.geometry.length > 2).map((r) => ({
      type: 'Feature', geometry: { type: 'Polygon', coordinates: [r.geometry] },
      properties: {
        status: r.status, parcel_id: r.parcel_id, nazwa: r.name, gospodarstwo: r.farm, gmina: r.commune, area_ha: r.area_ha,
        ...Object.fromEntries(Object.entries(r.criteria || {}).map(([k, v]) => [k, `${v.status}: ${v.text || ''}`])),
      },
    })),
    zrodla: Object.fromEntries(Object.entries(d.provenance || {}).map(([k, p]) => [k, `${p.source} (${p.source_url})`])),
  };
  downloadText('mrolnik-filtr-dzialek.geojson', JSON.stringify(fc), 'application/geo+json');
}

// ------------------------------------------------------------------------------------------
// Pastylka (panel zminimalizowany): kółko w trakcie, ✓ po sukcesie, ✗ po błędzie; klik przywraca panel
// ------------------------------------------------------------------------------------------
let pill = null;
let pillState = null;   // null | 'run' | 'ok' | 'err'

function renderPill() {
  const wrap = document.querySelector('.mr-mapwrap');
  if (!wrap) return;
  if (!pill) {
    pill = h('button', { type: 'button', class: 'mr-ps-pill', hidden: true, onclick: () => open(document.querySelector('.mr-ps-open')) });
    wrap.append(pill);
  }
  pill.hidden = !pillState;
  if (!pillState) return;
  const d = job && job.data;
  let ico;
  let text;
  if (pillState === 'run') {
    ico = h('span', { class: 'mr-ps-pill__spin', 'aria-hidden': 'true' });
    const ok = d && d.counts ? d.counts.spelnia || 0 : 0;
    text = `Filtruję… ${d && d.step ? d.step.pct : 0}%` + (ok ? ` · ${ok} ${plural(ok, 'działka spełnia', 'działki spełniają', 'działek spełnia')}` : '');
  } else if (pillState === 'ok') {
    // po zakończeniu – z końcowych liczników (filterInfo może być jeszcze z ostatniej partii na żywo)
    const n = d && d.counts ? (d.counts.spelnia || 0) + (d.counts.do_sprawdzenia || 0) : (filterInfo ? filterInfo.shown : 0);
    ico = icon('checkCircle', 'mr-ps-pill__ok');
    text = `Gotowe: ${n} ${plural(n, 'działka', 'działki', 'działek')}`;
  } else {
    ico = icon('cross', 'mr-ps-pill__err');
    text = d && d.too_big ? 'Obszar za duży – szczegóły' : 'Błąd – szczegóły';
  }
  pill.className = `mr-ps-pill mr-ps-pill--${pillState}`;
  pill.setAttribute('aria-label', `${text}. Pokaż panel filtrowania działek.`);
  replace(pill, [ico, h('span', { class: 'mr-ps-pill__txt', text, 'aria-live': 'polite' })]);
}

// ------------------------------------------------------------------------------------------
// NDVI / NDMI / NDWI dla działek wyników (jedno zapytanie CDSE dla wszystkich; nakładka wg wybranej warstwy mapy)
// ------------------------------------------------------------------------------------------
let indexLayer = null;
let indexData = null;
let indexSeq = 0;
let indexMoveOff = null;
let indexOn = false;

function dropIndex() {
  const map = getMap();
  indexSeq += 1;
  if (map && indexLayer) map.removeLayer(indexLayer);
  indexLayer = null;
  indexData = null;
  if (map && indexMoveOff) { map.off('moveend', indexMoveOff); indexMoveOff = null; }
}

// NDVI / NDMI / NDWI dla wyników w skupiskach ≤ 10 × 10 km: każde skupisko osobno (jedno zapytanie CDSE na skupisko, raster 10 m),
// po 3 naraz, rysowane w miarę napływu; przy > 12 skupiskach najpierw te w widoku mapy, reszta po przesunięciu mapy.
const INDEX_PARALLEL = 3;
const INDEX_ALL_MAX = 12;

async function loadIndex() {
  const d = job && job.data;
  const map = getMap();
  if (!d || !map || !filterInfo || !filterInfo.shown) return;
  const seq = ++indexSeq;
  filterInfo.indexNote = 'Wczytywanie indeksu z satelity…';
  renderIndicator();
  let cl;
  try {
    cl = (await api(`/api/parcel-search/${d.job_id}/index/clusters`, { timeout: 60000 })).clusters || [];
  } catch (err) {
    if (seq !== indexSeq || !filterInfo) return;
    filterInfo.indexNote = `indeks: ${err.message}`;
    renderIndicator();
    return;
  }
  if (seq !== indexSeq || !filterInfo) return;
  indexData = { jobId: d.job_id, clusters: cl, done: new Map(), busy: new Set(), seq };
  if (cl.length > INDEX_ALL_MAX && !indexMoveOff) {
    let t = null;
    indexMoveOff = () => { clearTimeout(t); t = setTimeout(() => pumpIndex(true), 600); };
    map.on('moveend', indexMoveOff);
  }
  pumpIndex(cl.length > INDEX_ALL_MAX);
}

function inView(c, b) {
  const [w, s, e, n] = c.bbox;
  return !(e < b.getWest() || w > b.getEast() || n < b.getSouth() || s > b.getNorth());
}

function pumpIndex(viewOnly) {
  const map = getMap();
  const st = indexData;
  if (!st || !map || st.seq !== indexSeq) return;
  const b = map.getBounds();
  const todo = st.clusters.filter((c) => !st.done.has(c.id) && !st.busy.has(c.id) && (!viewOnly || inView(c, b)));
  todo.sort((x, y) => Number(inView(y, b)) - Number(inView(x, b)));   // najpierw widoczne
  while (st.busy.size < INDEX_PARALLEL && todo.length) {
    const c = todo.shift();
    st.busy.add(c.id);
    api(`/api/parcel-search/${st.jobId}/index?cluster=${encodeURIComponent(c.id)}`, { timeout: 180000 }).then((r) => {
      if (st.seq !== indexSeq) return;
      st.done.set(c.id, r);
      drawIndex();
    }).catch((err) => {
      if (st.seq !== indexSeq) return;
      st.done.set(c.id, { layers: null, error: err.message });
    }).finally(() => {
      if (st.seq !== indexSeq) return;
      st.busy.delete(c.id);
      noteIndex();
      pumpIndex(viewOnly);
    });
  }
  noteIndex();
}

// data zdjęcia wprost: „15.11.2026”; różne daty skupisk – „12–15.11.2026” albo „28.10–3.11.2026”
function dateRange(dates) {
  const ds = [...new Set(dates)].sort();
  if (!ds.length) return null;
  const [y1, m1, d1] = ds[0].split('-').map(Number);
  const [y2, m2, d2] = ds[ds.length - 1].split('-').map(Number);
  const p2 = (n) => String(n).padStart(2, '0');
  if (ds.length === 1) return { one: true, text: `${p2(d1)}.${p2(m1)}.${y1}` };
  if (y1 === y2 && m1 === m2) return { one: false, text: `${p2(d1)}–${p2(d2)}.${p2(m2)}.${y2}` };
  if (y1 === y2) return { one: false, text: `${p2(d1)}.${p2(m1)}–${p2(d2)}.${p2(m2)}.${y2}` };
  return { one: false, text: `${p2(d1)}.${p2(m1)}.${y1}–${p2(d2)}.${p2(m2)}.${y2}` };
}

const fmt2 = (v) => Number(v).toFixed(2).replace('.', ',');

// wartości wskaźnika aktywnej warstwy dla wyników: średnia ważona powierzchnią skupisk (piksele 10 m ≈ powierzchnia),
// min i maks tylko gdy serwer je policzył (NDVI); NDMI/NDWI – sama średnia
function indexStats(ready, m) {
  let wsum = 0;
  let acc = 0;
  let lo = null;
  let hi = null;
  ready.forEach((r) => {
    const st = r.stats && r.stats[m];
    if (!st || st.mean == null) return;
    const w = r.area_ha || r.count || 1;
    wsum += w;
    acc += st.mean * w;
    if (st.min != null) lo = lo == null ? st.min : Math.min(lo, st.min);
    if (st.max != null) hi = hi == null ? st.max : Math.max(hi, st.max);
  });
  if (!wsum) return null;
  const mean = acc / wsum;
  // skrajne piksele ±1 (brzegi działek, pojedyncze piksele bez sygnału) nie są zakresem łanu – wtedy tylko średnia
  const range = lo != null && hi != null && lo > -0.99 && hi < 0.99;
  return `${m.toUpperCase()}: średnio ${fmt2(mean)}` + (range ? ` (min ${fmt2(lo)} – maks ${fmt2(hi)})` : '');
}

function noteIndex() {
  const st = indexData;
  if (!st || !filterInfo) return;
  const ready = [...st.done.values()].filter((r) => r.layers);
  const n = st.clusters.length;
  const fin = st.done.size;
  let txt;
  if (!ready.length && fin === n) txt = 'Indeks: brak bezchmurnego zdjęcia';
  else if (!ready.length) txt = 'Wczytywanie indeksu z satelity…';
  else {
    const dr = dateRange(ready.map((r) => r.acquired_on).filter(Boolean));
    const stats = indexStats(ready, getMetric());
    txt = [stats, dr ? `${dr.one ? 'Zdjęcie Sentinel-2 z' : 'zdjęcia Sentinel-2 z'} ${dr.text}` : null].filter(Boolean).join(' · ');
  }
  if (n > 1 && fin < n) txt += ` · skupiska ${fin} z ${n}${st.clusters.length > INDEX_ALL_MAX ? ' (przesuń mapę po resztę)' : ''}`;
  filterInfo.indexNote = txt;
  renderIndicator();
}

function drawIndex() {
  const map = getMap();
  if (!map || typeof L === 'undefined') return;
  if (indexLayer) { map.removeLayer(indexLayer); indexLayer = null; }
  const st = indexData;
  if (!st) return;
  const m = getMetric();
  const overlays = [];
  st.done.forEach((r) => {
    const lay = r.layers && r.layers[m];
    if (!lay || typeof lay.image !== 'string' || !/^data:image\/(png|jpeg|webp);base64,/.test(lay.image) || !Array.isArray(lay.bounds)) return;
    overlays.push(L.imageOverlay(lay.image, lay.bounds, { opacity: 0.8, className: 'mr-ndvi', alt: `Mapa ${m.toUpperCase()} dla działek wyników filtra` }));
  });
  if (!overlays.length) return;
  indexLayer = L.layerGroup(overlays).addTo(map);
  if (resultLayer) resultLayer.bringToFront();
}

if (!indexOn) {
  indexOn = true;
  on('map:metric', () => { if (indexData) { drawIndex(); noteIndex(); } });
}

// ------------------------------------------------------------------------------------------
// Wyniki na żywo: działki pojawiają się na mapie i na liście w miarę sprawdzania (przyrost z GET ?since=)
// ------------------------------------------------------------------------------------------
let live = null;   // { layer, candLayer, candByKey: Map, ids: Set, pts: Set, ok, shown, list, listCount, fitted }

function dropLive() {
  const map = getMap();
  if (live && map) {
    if (live.layer) map.removeLayer(live.layer);
    if (live.candLayer) map.removeLayer(live.candLayer);
  }
  live = null;
}

function liveStyle() {
  const css = getComputedStyle(document.querySelector('.mr-app') || document.documentElement);
  return {
    ok: css.getPropertyValue('--mr-ok').trim() || '#1d6b34',
    mid: css.getPropertyValue('--mr-none').trim() || '#4d4d4d',
  };
}

function liveUpdate(my, d) {
  render(d);                                   // pasek postępu, kroki, pastylka (lista wyników budowana niżej, przyrostowo)
  my.cursor = d.cursor || my.cursor || 0;
  my.candCursor = d.cand_cursor || my.candCursor || 0;
  const map = getMap();
  if (!live) {
    live = { ids: new Set(), pts: new Set(), ok: 0, shown: 0, listCount: 0, candByKey: new Map(), fitted: false };
    if (resultLayer && map) { map.removeLayer(resultLayer); resultLayer = null; }   // poprzedni filtr znika przy pierwszej partii
    dropIndex();
    replace(ui.results, [h('h3', { class: 'mr-ps__h', text: 'Wyniki (na bieżąco)' }), live.list = h('ol', { class: 'mr-ps__list' }),
      live.more = h('p', { class: 'mr-hint', hidden: true })]);
    if (map && typeof L !== 'undefined') {
      if (!canvasRenderer) canvasRenderer = L.canvas({ padding: 0.3 });
      const c = liveStyle();
      live.layer = L.geoJSON(null, {
        renderer: canvasRenderer,
        style: (f) => (f.properties.status === 'spelnia'
          ? { color: c.ok, fillColor: c.ok, weight: 2.5, fillOpacity: 0.15 }
          : { color: c.mid, fillColor: c.mid, weight: 2, dashArray: '6 4', fillOpacity: 0.05 }),
        onEachFeature: (f, layer) => layer.bindTooltip(`${f.properties.name || f.properties.id || ''} – ${STATUS[f.properties.status].text}`),
      }).addTo(map);
      live.candLayer = L.geoJSON(null, {
        renderer: canvasRenderer, interactive: false,
        style: () => ({ color: c.mid, weight: 1, opacity: 0.35, fill: false }),   // kandydat: bardzo blady kontur
      }).addTo(map);
    }
  }
  // widok: raz, na początku – do obszaru wyszukiwania (EGiB)
  if (!live.fitted && map && d.zones && d.zones.area && d.zones.area.geojson && typeof L !== 'undefined') {
    const b = L.geoJSON(d.zones.area.geojson).getBounds();
    if (b.isValid()) { map.fitBounds(b, { padding: [30, 30], maxZoom: 15 }); live.fitted = true; }
  }
  // kandydaci (przeszli tanie kryteria, czekają na resztę)
  (d.candidates_new || []).forEach((c) => {
    if (!live.candLayer || !c.geometry || c.geometry.length < 3 || live.candByKey.has(c.key)) return;
    const before = new Set(live.candLayer.getLayers());
    live.candLayer.addData({ type: 'Feature', properties: { key: c.key }, geometry: { type: 'Polygon', coordinates: [c.geometry] } });
    live.candLayer.getLayers().forEach((l) => { if (!before.has(l)) live.candByKey.set(c.key, l); });
  });
  // nowe gotowe działki: mapa (addData), lista (do LIST_MAX), filtr mapy od pierwszej partii
  const feats = [];
  (d.results_new || []).forEach((r) => {
    const cl = live.candByKey.get(r.key);
    if (cl && live.candLayer) { live.candLayer.removeLayer(cl); live.candByKey.delete(r.key); }
    if (r.status !== 'spelnia' && r.status !== 'do_sprawdzenia') return;
    live.shown += 1;
    if (r.status === 'spelnia') live.ok += 1;
    if (r.parcel_id) live.ids.add(r.parcel_id);
    live.pts.add(`${Number(r.lat).toFixed(6)}|${Number(r.lon).toFixed(6)}`);
    if (r.geometry && r.geometry.length > 2) {
      feats.push({ type: 'Feature', properties: { status: r.status, name: r.name, id: r.parcel_id }, geometry: { type: 'Polygon', coordinates: [r.geometry] } });
    }
    if (live.listCount < LIST_MAX) {
      live.list.append(item(r, Object.keys(d.criteria || {}), d));
      live.listCount += 1;
    }
  });
  if (feats.length && live.layer) live.layer.addData({ type: 'FeatureCollection', features: feats });
  if (live.shown > live.listCount) {
    live.more.hidden = false;
    live.more.textContent = `Na liście ${live.listCount} z ${live.shown} – pełna lista i eksport po zakończeniu filtrowania.`;
  }
  if (live.shown) {
    const ids = live.ids;
    const pts = live.pts;
    const fn = (p) => (p.parcel_id && ids.has(p.parcel_id)) || (Array.isArray(p.centroid) && pts.has(`${Number(p.centroid[0]).toFixed(6)}|${Number(p.centroid[1]).toFixed(6)}`));
    if ((d.results_new || []).some((r) => r.status === 'spelnia' || r.status === 'do_sprawdzenia') || !state.parcelFilter) {
      state.parcelFilter = fn;
      setParcelFilter(fn);
      emit('parcels:filter', { live: true });
    }
  }
  filterInfo = { ids: live.ids, shown: live.shown, ok: live.ok, total: (d.counts && d.counts.gotowe) || 0, data: d, live: true,
    pct: d.step ? d.step.pct : 0 };
  renderIndicator();
  if (ui.panel.hidden && pillState) renderPill();
}

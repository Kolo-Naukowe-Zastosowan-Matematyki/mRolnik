// mRolnik – punkt wejścia. Inicjalizacja na elemencie #mrolnik-root (można osadzić w stronie eRolnika).
import { h, icon, replace } from './dom.js';
import { api, setApiBase } from './api.js';
import { state, on, emit, setLive, announce, CUSTOM_FARM } from './state.js';
import { getPref, setPref } from './prefs.js';
import { initTabs } from './tabs.js';
import { initMap, renderParcels, fitToParcels, highlight, focusParcel, setAddMode, isAddMode, renderMetricBar, flyTo } from './map.js';
import { renderTools, renderExamples, addParcel } from './farm.js';
import { demoBadge, watchSources } from './format.js';
import { redrawCharts, pruneCharts } from './charts.js';
import { renderField } from './views/field.js';
import { renderHistory } from './views/history.js';
import { renderCrops } from './views/crops.js';
import { renderWater } from './views/water.js';
import { renderCalc } from './views/calc.js';
import { renderSources } from './views/sources.js';

const ANALYSIS_TIMEOUT = 60000;
const VIEWS = { pole: renderField, historia: renderHistory, woda: renderWater, plony: renderCrops, kalk: renderCalc, zrodla: renderSources };

let root;
let tabs;
let analysisCtrl = null;
const ref = (name) => root.querySelector(`[data-ref="${name}"]`);
const panelOf = (name) => root.querySelector(`#mr-tp-${name}`);

function renderTab(name) {
  const view = VIEWS[name];
  const panel = panelOf(name);
  if (!view || !panel) return;
  try {
    view(panel);
  } catch (err) {
    console.error('[mRolnik] błąd widoku', name, err);
    replace(panel, h('p', { class: 'mr-error', role: 'alert', text: 'Wystąpił błąd wyświetlania tej zakładki. Odśwież stronę.' }));
  }
  pruneCharts();
}

// --- motyw ---------------------------------------------------------------------------------------
function currentTheme() {
  const t = root.dataset.theme;
  if (t === 'light' || t === 'dark') return t;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(t) {
  if (t === 'light' || t === 'dark') root.dataset.theme = t; else delete root.dataset.theme;
  const dark = currentTheme() === 'dark';
  const btn = root.querySelector('[data-action="theme"]');
  if (btn) {
    btn.setAttribute('aria-pressed', String(dark));
    const lbl = ref('theme-label');
    if (lbl) lbl.textContent = 'Tryb ciemny';
  }
  redrawCharts();
}

// --- bannery ----------------------------------------------------------------------------------------
const notices = [];
function renderBanner() {
  const banner = ref('banner');
  const warnings = (state.analysis && Array.isArray(state.analysis.warnings) ? state.analysis.warnings : []).map(String);
  const all = [...warnings, ...notices];
  if (!all.length) { banner.hidden = true; replace(banner, []); return; }
  banner.hidden = false;
  replace(banner, h('div', { class: 'mr-banner__inner' }, [
    icon('warn', 'mr-banner__ico'),
    h('ul', { class: 'mr-banner__list' }, all.slice(0, 4).map((w) => h('li', { text: w }))),
    h('button', { type: 'button', class: 'mr-btn mr-btn--ghost mr-btn--small', onclick: () => { notices.length = 0; banner.hidden = true; } }, 'Zamknij'),
  ]));
}

// --- bieżące gospodarstwo -------------------------------------------------------------------------
function farmParcels() {
  if (state.farmName === CUSTOM_FARM) return state.customParcels.slice();
  const f = state.presets.find((x) => x.name === state.farmName);
  return f && Array.isArray(f.parcels) ? f.parcels.slice(0, 50) : [];
}

function renderCurrent() {
  const box = ref('current');
  const custom = state.farmName === CUSTOM_FARM;
  const name = custom ? 'Moje pola' : (state.farmName || 'Brak wybranego gospodarstwa');
  const examples = h('div', { class: 'mr-current__examples' });
  replace(box, [
    h('p', { class: 'mr-current__line' }, [
      h('span', { class: 'mr-muted', text: 'Pokazujesz: ' }),
      h('strong', { text: name }),
      !custom && state.farmName ? demoBadge('PRZYKŁAD') : null,
      custom ? h('span', { class: 'mr-muted', text: ` (${state.customParcels.length})` }) : null,
      state.loading ? h('span', { class: 'mr-current__busy', role: 'status', text: ' – wczytywanie danych…' }) : null,
    ]),
    examples,
  ]);
  renderExamples(examples);
}

// --- analiza ------------------------------------------------------------------------------------------
async function runAnalysis() {
  const parcels = farmParcels();
  state.request = parcels;
  state.analysisError = null;
  if (analysisCtrl) analysisCtrl.abort();
  if (!parcels.length) {
    state.analysis = null;
    state.loading = false;
    renderParcels([], -1);
    afterAnalysis();
    return;
  }
  const body = {
    parcels: parcels.map((p) => {
      const o = { lat: p.lat, lon: p.lon };
      ['crop', 'soil', 'id_tag', 'parcel_id'].forEach((k) => { if (p[k]) o[k] = String(p[k]).slice(0, 120); });
      return o;
    }),
    weather_model: 'auto',
  };
  const key = JSON.stringify(body);
  if (state.cache.has(key)) {
    state.analysis = state.cache.get(key);
    state.loading = false;
    afterAnalysis(true);
    return;
  }
  state.loading = true;
  state.analysis = null;
  renderTab(tabs.current());
  announce('Trwa analiza działek…');
  const ctrl = new AbortController();
  analysisCtrl = ctrl;
  try {
    const data = await api('/api/analyze', { method: 'POST', body, signal: ctrl.signal, timeout: ANALYSIS_TIMEOUT });
    if (ctrl !== analysisCtrl) return;
    state.analysis = data && typeof data === 'object' ? data : null;
    if (state.analysis) {
      state.cache.set(key, state.analysis);
      if (state.cache.size > 8) state.cache.delete(state.cache.keys().next().value);
    }
    state.loading = false;
    afterAnalysis(true);
    const n = state.analysis && state.analysis.parcels ? state.analysis.parcels.length : 0;
    announce(`Analiza gotowa: ${n} działek.`);
  } catch (err) {
    if (err.kind === 'abort' || ctrl !== analysisCtrl) return;
    state.loading = false;
    state.analysisError = err.message === 'Serwer nie odpowiedział w wyznaczonym czasie.'
      ? new Error(`Analiza trwa dłużej niż ${ANALYSIS_TIMEOUT / 1000} s – źródła danych odpowiadają wolno. Spróbuj ponownie za chwilę.`)
      : err;
    afterAnalysis();
    announce('Nie udało się przeanalizować działek.');
  }
}

function afterAnalysis(fit) {
  const ps = state.analysis && Array.isArray(state.analysis.parcels) ? state.analysis.parcels : [];
  if (state.selected >= ps.length) state.selected = 0;
  renderParcels(ps, state.selected);
  if (fit) fitToParcels(state.analysis);
  renderBanner();
  renderCurrent();
  renderTab(tabs.current());
  enrichInBackground(state.analysis);
}

// --- doczytywanie w tle (etap 2): historia NDVI 24 mies. i zdjęcie Sentinel-2 w barwach naturalnych ----------------------
// /api/analyze odpowiada szybko bez tych danych (history_status "pending", layers_pending ["true_color"]);
// tutaj pobieramy je po 2 naraz i odświeżamy mapę oraz bieżącą zakładkę, gdy dojdą.
const enriching = new WeakSet();
function enrichInBackground(a) {
  if (!a || !Array.isArray(a.parcels) || enriching.has(a)) return;
  enriching.add(a);
  const jobs = [];
  a.parcels.forEach((p) => {
    const qs = new URLSearchParams();
    if (p.parcel_id) qs.set('parcel_id', p.parcel_id);
    if (Array.isArray(p.centroid)) { qs.set('lat', String(p.centroid[0])); qs.set('lon', String(p.centroid[1])); }
    if (p.history_status === 'pending') {
      jobs.push(() => api(`/api/history/ndvi?${qs}`, { timeout: 60000 }).then((r) => {
        p.history = r && r.history ? r.history : null;
        p.history_status = p.history ? 'ready' : 'none';
        return 'history';
      }).catch(() => { p.history_status = 'none'; return 'history'; }));
    }
    if (Array.isArray(p.layers_pending) && p.layers_pending.includes('true_color')) {
      jobs.push(() => api(`/api/layers/true-color?${qs}`, { timeout: 60000 }).then((r) => {
        p.layers = Object.assign({}, p.layers, { true_color: r && r.layer ? r.layer : null });
        return 'photo';
      }).catch(() => 'photo').finally(() => { p.layers_pending = p.layers_pending.filter((x) => x !== 'true_color'); }));
    }
  });
  let next = 0;
  const worker = async () => {
    while (next < jobs.length) {
      const kind = await jobs[next++]();
      if (state.analysis !== a) return;               // użytkownik wybrał już inne gospodarstwo
      if (kind === 'photo') renderParcels(a.parcels, state.selected, true);
      if (kind === 'history' && ['pole', 'plony'].includes(tabs.current())) renderTab(tabs.current());
    }
  };
  worker(); worker();
}

// --- tryb dodawania na mapie -------------------------------------------------------------------
function setAdding(onFlag) {
  setAddMode(onFlag);
  const hint = ref('map-hint');
  hint.hidden = !onFlag;
  ref('map-hint-text').textContent = onFlag ? 'Kliknij na mapie środek swojego pola. Esc – anuluj.' : '';
  if (onFlag) {
    const m = ref('map');
    if (window.matchMedia('(max-width: 899px)').matches) m.scrollIntoView({ behavior: 'smooth', block: 'center' });
    announce('Tryb dodawania pola: kliknij na mapie środek pola.');
  }
}

// --- start ----------------------------------------------------------------------------------------
async function boot() {
  root = document.getElementById('mrolnik-root');
  if (!root) return;
  state.root = root;
  setApiBase(root.dataset.apiBase || '');
  setLive(ref('live'));
  watchSources(root);
  root.classList.add('mr-js');

  // motyw
  applyTheme(getPref('theme', null));
  root.querySelector('[data-action="theme"]').addEventListener('click', () => {
    const next = currentTheme() === 'dark' ? 'light' : 'dark';
    setPref('theme', next);
    applyTheme(next);
  });
  if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (!root.dataset.theme) applyTheme(null); });

  // zakładki
  tabs = initTabs(ref('tablist'), (name) => {
    setPref('tab', name);
    renderTab(name);
  });

  // mapa
  try {
    initMap(ref('map'), {
      cadastre: root.dataset.cadastreWms === '1',
      waterWms: root.dataset.waterWms || null,
      waterLayers: root.dataset.waterWmsLayers || null,
    });
    renderMetricBar(ref('mapbar'), getPref('mapMetric', 'ndvi'));
    on('map:metric', (m) => setPref('mapMetric', m));
  } catch (err) {
    replace(ref('map'), h('p', { class: 'mr-state mr-state--error', role: 'alert', text: `Nie udało się wczytać mapy: ${err.message}` }));
  }

  renderTools(ref('tools'));
  renderCurrent();

  // zdarzenia
  on('farm:change', (name) => {
    state.farmName = name;
    state.selected = 0;
    state.expanded = -1;
    if (name !== CUSTOM_FARM) setPref('farm', name);
    setAdding(false);
    // natychmiastowa reakcja na kliknięcie (analiza nowego gospodarstwa trwa 10–40 s): mapa, nazwa, „wczytywanie…”
    const pts = farmParcels().filter((p) => Number.isFinite(Number(p.lat)) && Number.isFinite(Number(p.lon)));
    if (pts.length) {
      const lat = pts.reduce((s, p) => s + Number(p.lat), 0) / pts.length;
      const lon = pts.reduce((s, p) => s + Number(p.lon), 0) / pts.length;
      flyTo(lat, lon, 14);
    }
    runAnalysis();      // ustawia state.loading synchronicznie (przed pierwszym await)
    renderCurrent();
  });
  on('analysis:retry', () => { state.cache.clear(); runAnalysis(); });
  on('parcel:select', (i) => {
    state.selected = i < 0 ? -1 : i;
    state.expanded = state.selected;
    highlight(state.selected);
    if (i >= 0) focusParcel(i);
    renderTab(tabs.current());
  });
  on('parcel:remove', (i) => {
    state.customParcels.splice(i, 1);
    state.selected = 0;
    state.expanded = -1;
    runAnalysis();
  });
  on('addmode', (v) => setAdding(v));
  on('map:add', (p) => {
    setAdding(false);
    addParcel({ lat: p.lat, lon: p.lon, id_tag: p.id_tag || `Pole ${state.customParcels.length + 1}`, crop: p.crop });
  });
  // „Edytuj pole” na pinezce: nowa nazwa / uprawa pola (w pamięci przeglądarki, także dla gospodarstwa przykładowego)
  on('parcel:edit', ({ index, id_tag, crop }) => {
    const list = state.farmName === CUSTOM_FARM ? state.customParcels
      : ((state.presets.find((x) => x.name === state.farmName) || {}).parcels || []);
    const p = list[index];
    if (!p) return;
    if (id_tag) p.id_tag = String(id_tag).slice(0, 60);
    if (crop) p.crop = String(crop).slice(0, 60); else delete p.crop;
    state.selected = index;
    state.expanded = index;
    announce(`Zapisano zmiany pola ${p.id_tag}. Trwa analiza.`);
    runAnalysis();
  });
  on('map:hint', (t) => { ref('map-hint-text').textContent = t; });
  on('notice', (t) => { notices.push(t); renderBanner(); });

  root.querySelector('[data-action="cancel-add"]').addEventListener('click', () => setAdding(false));
  root.addEventListener('keydown', (e) => { if (e.key === 'Escape' && isAddMode()) setAdding(false); });
  root.querySelector('[data-action="goto-sources"]').addEventListener('click', () => {
    tabs.select('zrodla', true);
    root.querySelector('.mr-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  const logout = root.querySelector('[data-action="logout"]');
  logout.addEventListener('click', async () => {
    try { await api('/auth/logout', { method: 'POST', noRedirect: true }); } catch (err) { console.warn('[mRolnik] wylogowanie', err); }
    window.location.assign((state.user && state.user.login_url) || '/');
  });

  // pierwsza zakładka (zawsze podświetlona) – szkielet od razu
  const startTab = getPref('tab', 'pole');
  tabs.select(VIEWS[startTab] ? startTab : 'pole');

  // użytkownik
  try {
    const me = await api('/auth/me', { timeout: 10000 });
    if (me && me.authenticated === false) {
      const u = me.login_url && me.login_url.startsWith('/') ? me.login_url : '/auth/login';
      window.location.assign(`${u}?next=${encodeURIComponent(location.pathname + location.search)}`);
      return;
    }
    state.user = me ? { ...(me.user || {}), login_url: me.login_url, mode: me.auth_mode } : null;
    if (me && me.user && me.user.name) {
      const u = ref('user');
      u.hidden = false;
      replace(u, [icon('leaf'), h('span', { text: me.user.name })]);
      logout.hidden = me.auth_mode === 'dev';
    }
  } catch (err) {
    if (err.kind === 'auth') return;
    console.warn('[mRolnik] /auth/me niedostępne', err.message);
  }

  // gospodarstwa przykładowe
  state.loading = true;
  renderTab(tabs.current());
  try {
    const p = await api('/api/presets', { timeout: 20000 });
    state.presets = p && Array.isArray(p.farms) ? p.farms.filter((f) => f && f.name && Array.isArray(f.parcels)) : [];
  } catch (err) {
    state.presets = [];
    notices.push(`Nie udało się wczytać przykładowych gospodarstw: ${err.message}`);
  }
  state.loading = false;
  const saved = getPref('farm', null);
  const def = root.dataset.defaultFarm;
  const pick = [saved, def].find((n) => n && state.presets.some((f) => f.name === n)) || (state.presets[0] && state.presets[0].name) || CUSTOM_FARM;
  emit('farm:change', pick);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();

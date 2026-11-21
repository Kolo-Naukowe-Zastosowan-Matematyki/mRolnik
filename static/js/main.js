// mRolnik – punkt wejścia. Trzykrokowy proces: KROK 1 (Wybór działek), KROK 2 (Ocena sytuacji wodnej) oraz KROK 3 (Rekomendacje).
import { h, icon, replace } from './dom.js';
import { api, setApiBase } from './api.js';
import { state, on, emit, setLive, announce, CUSTOM_FARM } from './state.js';
import { getPref, setPref } from './prefs.js';
import { initTabs } from './tabs.js';
import { initMap, renderParcels, fitToParcels, highlight, focusParcel, setAddMode, isAddMode, clearPin, renderMetricBar, setMetric, flyTo, getMap } from './map.js';
import { initWaterHover } from './waterhover.js';
import { loadSets, findSet, isSetKey, PAGE, FILTER_FARM } from './usersets.js';
import { renderSearchHero, renderTools, addParcel, removeParcel, clearAllParcels, loadPresetFarm, exportCurrent } from './farm.js';
import { watchSources } from './format.js';
import { redrawCharts, pruneCharts } from './charts.js';
import { renderField } from './views/field.js';
import { renderHistory } from './views/history.js';
import { renderCrops } from './views/crops.js';
import { renderClimate } from './views/climate.js';
import { renderWater } from './views/water.js';
import { renderCalc } from './views/calc.js';
import { renderSources, initStatusDot } from './views/sources.js';

const ANALYSIS_TIMEOUT = 60000;
const VIEWS = { pole: renderField, historia: renderHistory, woda: renderWater, plony: renderCrops, klimat: renderClimate, kalk: renderCalc, zrodla: renderSources };

let root;
let tabs;
let analysisCtrl = null;
const ref = (name) => (root ? root.querySelector(`[data-ref="${name}"]`) : null);
const panelOf = (name) => (root ? root.querySelector(`#mr-tp-${name}`) : null);

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
  if (!banner) return;
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
  const set = findSet(state.farmName);
  if (set) {
    const page = Math.max(0, Math.min(state.setPage || 0, Math.ceil(set.parcels.length / PAGE) - 1));
    state.setPage = page;
    return set.parcels.slice(page * PAGE, (page + 1) * PAGE);
  }
  const f = (state.presets || []).find((x) => x.name === state.farmName);
  return f && Array.isArray(f.parcels) ? f.parcels.slice(0, 50) : state.customParcels.slice();
}

function renderCurrent() {
  const box = ref('current');
  if (!box) return;
  const custom = state.farmName === CUSTOM_FARM;
  const set = findSet(state.farmName);
  const name = custom ? 'Moje wybrane działki' : set ? set.name : (state.farmName || 'Wybrane działki');
  const n = custom ? state.customParcels.length : (set ? set.parcels.length : farmParcels().length);
  const pages = set ? Math.ceil(n / PAGE) : 0;
  const page = state.setPage || 0;
  const goPage = (p) => { state.setPage = p; state.selected = 0; state.expanded = -1; runAnalysis(); renderCurrent(); };

  replace(box, [
    h('p', { class: 'mr-current__line' }, [
      h('span', { class: 'mr-muted', text: 'Rekomendacje dla: ' }),
      h('strong', { text: name }),
      h('span', { class: 'mr-muted', text: ` (${n} ${n === 1 ? 'działka' : (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14) ? 'działki' : 'działek')})` }),
      state.loading ? h('span', { class: 'mr-current__busy', role: 'status', text: ' – pobieranie danych…' }) : null,
    ]),
    pages > 1 ? h('p', { class: 'mr-current__pages' }, [
      h('span', { class: 'mr-muted', text: `Analiza dla działek ${page * PAGE + 1}–${Math.min(n, (page + 1) * PAGE)} z ${n}.` }),
      page > 0 ? h('button', { type: 'button', class: 'mr-btn mr-btn--small mr-btn--secondary', onclick: () => goPage(page - 1) }, `Poprzednie ${PAGE}`) : null,
      page < pages - 1 ? h('button', { type: 'button', class: 'mr-btn mr-btn--small mr-btn--primary', onclick: () => goPage(page + 1) },
        `Wczytaj kolejne ${Math.min(PAGE, n - (page + 1) * PAGE)}`) : null,
    ]) : null,
  ]);
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
      const o = { lat: Number(p.lat), lon: Number(p.lon) };
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

  // W Widoku 2 pokazujemy stan ładowania
  const loadingEl = ref('results-loading');
  const panelEl = ref('results-panel');
  if (loadingEl) loadingEl.hidden = false;
  if (panelEl) panelEl.hidden = true;

  announce('Trwa analiza wybranych działek…');
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
    if (loadingEl) loadingEl.hidden = true;
    if (panelEl) panelEl.hidden = false;
    afterAnalysis(true);
    const n = state.analysis && state.analysis.parcels ? state.analysis.parcels.length : 0;
    announce(`Rekomendacje gotowe dla ${n} działek.`);
  } catch (err) {
    if (err.kind === 'abort' || ctrl !== analysisCtrl) return;
    state.loading = false;
    if (loadingEl) loadingEl.hidden = true;
    if (panelEl) panelEl.hidden = false;
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

// --- doczytywanie w tle (etap 2): historia NDVI 24 mies. -------------------------------------------
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
    if (Array.isArray(p.layers_pending)) p.layers_pending = p.layers_pending.filter((x) => x !== 'true_color');
  });
  let next = 0;
  const worker = async () => {
    while (next < jobs.length) {
      const kind = await jobs[next++]();
      if (state.analysis !== a) return;
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
  if (hint) hint.hidden = !onFlag;
  const textEl = ref('map-hint-text');
  if (textEl) textEl.textContent = onFlag ? 'Kliknij na mapie środek swojego pola. Esc – anuluj.' : '';
  if (onFlag) {
    const m = ref('map');
    if (m && window.matchMedia('(max-width: 899px)').matches) m.scrollIntoView({ behavior: 'smooth', block: 'center' });
    announce('Tryb dodawania pola: kliknij na mapie środek pola.');
  }
}

// ==============================================================================================
// PRZEŁĄCZANIE KROKÓW: KROK 1 (Wybór działek) <-> KROK 2 (Rekomendacje)
// ==============================================================================================

/**
 * Przenosi kontener mapy do odpowiedniego slotu w DOM zależnie od kroku.
 */
function attachMapToSlot(slotId) {
  const mapwrap = ref('mapwrap');
  const mapbar = ref('mapbar');
  const selector = slotId === 'step1'
    ? '#mr-step1-map-slot'
    : (slotId === 'step2' ? '#mr-step2-water-map-slot' : '#mr-step2-map-slot');
  const target = root.querySelector(selector);
  if (target && mapwrap && mapbar) {
    target.appendChild(mapbar);
    target.appendChild(mapwrap);
    mapbar.hidden = slotId === 'step2';
    const metricGroup = mapbar.querySelector('.mr-mapbar__group');
    if (metricGroup) metricGroup.hidden = slotId === 'step1';
  }
  const m = getMap();
  if (m) {
    setTimeout(() => {
      m.invalidateSize();
    }, 60);
  }
}

/**
 * Aktualizuje widok listy zebranych działek w panelu bocznym Kroku 1.
 */
function updateCollectedList() {
  const count = state.customParcels.length;
  const totalHa = state.customParcels.reduce((sum, p) => sum + (Number(p.area_ha) || 0), 0);

  // Licznik w nagłówku panelu
  const badge = ref('collected-badge');
  if (badge) badge.textContent = String(count);

  // Łączna powierzchnia
  const summary = ref('collected-summary');
  if (summary) summary.textContent = `Łączna pow.: ${totalHa.toFixed(2).replace('.', ',')} ha`;

  // Przycisk CTA "Opracuj rekomendacje" i "Wyczyść listę"
  const ctaBtn = root.querySelector('#mr-cta-proceed');
  const ctaLabel = ref('cta-label');
  const ctaHint = ref('cta-hint');
  const clearListBtn = ref('clear-list-btn');
  const step2Btn = root.querySelector('#mr-step-btn-2');
  const step3Btn = root.querySelector('#mr-step-btn-3');

  if (clearListBtn) clearListBtn.hidden = count === 0;

  if (count === 0) {
    if (ctaBtn) ctaBtn.disabled = true;
    if (ctaLabel) ctaLabel.textContent = 'Opracuj rekomendacje';
    if (ctaHint) ctaHint.textContent = 'Wybierz co najmniej 1 działkę, aby przejść do analizy.';
    if (step2Btn) step2Btn.disabled = true;
    if (step3Btn) step3Btn.disabled = true;
  } else {
    if (ctaBtn) ctaBtn.disabled = false;
    if (ctaLabel) ctaLabel.textContent = `Opracuj rekomendacje (${count})`;
    if (ctaHint) ctaHint.textContent = 'Wszystko gotowe! Kliknij, aby przejść dalej.';
    if (step2Btn) step2Btn.disabled = false;
    if (step3Btn) step3Btn.disabled = false;
  }

  // Lista pozycji vs stan pusty
  const emptyEl = ref('collected-empty');
  const listEl = ref('collected-items');
  if (!emptyEl || !listEl) return;

  if (count === 0) {
    emptyEl.hidden = false;
    listEl.hidden = true;
    replace(listEl, []);
    return;
  }

  emptyEl.hidden = true;
  listEl.hidden = false;

  const items = state.customParcels.map((p, i) => {
    const title = p.id_tag || (p.parcel_id ? `Dz. ${p.parcel_id.split('.').pop()}` : `Działka ${i + 1}`);
    const metaParts = [];
    if (p.parcel_id) metaParts.push(p.parcel_id);
    if (p.commune) metaParts.push(`gm. ${p.commune}`);
    if (p.county && !p.commune) metaParts.push(`pow. ${p.county}`);

    return h('li', { class: 'mr-collected-item' }, [
      h('div', {
        class: 'mr-collected-item__body',
        tabindex: '0',
        role: 'button',
        'aria-label': `Przybliż mapę do działki ${title}`,
        onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (p.lat && p.lon) flyTo(p.lat, p.lon, 16); } },
        onclick: () => {
          if (p.lat && p.lon) {
            flyTo(p.lat, p.lon, 16);
            announce(`Przeniesiono do działki ${title}`);
          }
        },
      }, [
        h('div', { class: 'mr-collected-item__top' }, [
          h('strong', { class: 'mr-collected-item__name', text: title }),
          p.area_ha ? h('span', { class: 'mr-badge mr-badge--area', text: `${Number(p.area_ha).toFixed(2).replace('.', ',')} ha` }) : null,
        ]),
        metaParts.length ? h('p', { class: 'mr-collected-item__meta', text: metaParts.join(' · ') }) : null,
        p.crop ? h('span', { class: 'mr-tag mr-tag--crop', text: p.crop }) : null,
      ]),
      h('button', {
        type: 'button',
        class: 'mr-btn mr-btn--icon mr-btn--ghost mr-collected-item__remove',
        title: `Usuń działkę ${title} z listy`,
        'aria-label': `Usuń działkę ${title} z listy`,
        onclick: (e) => {
          e.stopPropagation();
          removeParcel(i);
        },
      }, [icon('trash')]),
    ]);
  });

  replace(listEl, items);
}

/**
 * Aktualizuje listę wybranych działek w prawym panelu bocznym Kroku 2.
 */
function renderWaterSidebarParcels() {
  const parcels = state.customParcels;
  const count = parcels.length;
  const totalHa = parcels.reduce((sum, p) => sum + (Number(p.area_ha) || 0), 0);
  const totalStr = `${totalHa.toFixed(2).replace('.', ',')} ha`;

  const badge = ref('water-parcels-badge');
  if (badge) badge.textContent = String(count);

  const summary = ref('water-parcels-summary');
  if (summary) summary.textContent = `Łączna pow.: ${totalStr}`;

  const metaEl = ref('water-meta');
  if (metaEl) {
    const dzWord = count === 1 ? 'działka' : (count % 10 >= 2 && count % 10 <= 4 && !(count % 100 >= 12 && count % 100 <= 14) ? 'działki' : 'działek');
    metaEl.textContent = `Wybrano ${count} ${dzWord} · łączna powierzchnia: ${totalStr}`;
  }

  const listEl = ref('water-parcels-list');
  if (!listEl) return;

  if (state.selected < 0 || state.selected >= count) state.selected = 0;

  const items = parcels.map((p, i) => {
    const title = p.id_tag || (p.parcel_id ? `Dz. ${p.parcel_id.split('.').pop()}` : `Działka ${i + 1}`);
    const metaParts = [];
    if (p.parcel_id) metaParts.push(p.parcel_id);
    if (p.commune) metaParts.push(`gm. ${p.commune}`);
    if (p.county && !p.commune) metaParts.push(`pow. ${p.county}`);

    const selectItem = () => {
      state.selected = i;
      highlight(i);
      if (p.lat && p.lon) flyTo(p.lat, p.lon, 16);
      renderWaterSidebarParcels();
      announce(`Wybrano działkę ${title}`);
    };

    return h('li', { class: `mr-collected-item${i === state.selected ? ' mr-collected-item--active' : ''}` }, [
      h('div', {
        class: 'mr-collected-item__body',
        tabindex: '0',
        role: 'button',
        'aria-pressed': String(i === state.selected),
        'aria-label': `Wybierz i przybliż działkę ${title}`,
        onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectItem(); } },
        onclick: selectItem,
      }, [
        h('div', { class: 'mr-collected-item__top' }, [
          h('strong', { class: 'mr-collected-item__name', text: title }),
          p.area_ha ? h('span', { class: 'mr-badge mr-badge--area', text: `${Number(p.area_ha).toFixed(2).replace('.', ',')} ha` }) : null,
        ]),
        metaParts.length ? h('p', { class: 'mr-collected-item__meta', text: metaParts.join(' · ') }) : null,
        p.crop ? h('span', { class: 'mr-tag mr-tag--crop', text: p.crop }) : null,
      ]),
    ]);
  });

  replace(listEl, items);
}

/**
 * Główna funkcja nawigacji między Krokiem 1 (Wybór działek), Krokiem 2 (Ocena sytuacji wodnej) i Krokiem 3 (Rekomendacje).
 */
export function goToStep(step) {
  const viewSelect = ref('view-select');
  const viewWater = ref('view-water');
  const viewResults = ref('view-results');
  const stepItem1 = ref('step-item-1');
  const stepItem2 = ref('step-item-2');
  const stepItem3 = ref('step-item-3');
  const stepBtn1 = root.querySelector('#mr-step-btn-1');
  const stepBtn2 = root.querySelector('#mr-step-btn-2');
  const stepBtn3 = root.querySelector('#mr-step-btn-3');

  if (step === 1) {
    state.currentStep = 1;
    if (viewSelect) viewSelect.hidden = false;
    if (viewWater) viewWater.hidden = true;
    if (viewResults) viewResults.hidden = true;

    if (stepItem1) {
      stepItem1.classList.add('mr-stepper__item--active');
      stepItem1.classList.remove('mr-stepper__item--done');
    }
    if (stepItem2) {
      stepItem2.classList.remove('mr-stepper__item--active', 'mr-stepper__item--done');
    }
    if (stepItem3) {
      stepItem3.classList.remove('mr-stepper__item--active', 'mr-stepper__item--done');
    }
    if (stepBtn1) stepBtn1.setAttribute('aria-current', 'step');
    if (stepBtn2) stepBtn2.removeAttribute('aria-current');
    if (stepBtn3) stepBtn3.removeAttribute('aria-current');

    // Przenieś mapę do Kroku 1
    attachMapToSlot('step1');
    setMetric(getPref('mapMetric', 'ndvi'));

    // Odśwież widok działek i mapy
    updateCollectedList();
    renderParcels(state.customParcels, -1);
    if (state.customParcels.length > 0) {
      fitToParcels({ parcels: state.customParcels }, { all: false });
    }

    announce('Widok 1: Wybór działek.');
  } else if (step === 2) {
    if (!state.customParcels.length) {
      announce('Wybierz co najmniej 1 działkę, aby przejść dalej.');
      emit('notice', 'Wybierz co najmniej 1 działkę, aby przejść dalej.');
      return;
    }

    state.currentStep = 2;
    if (viewSelect) viewSelect.hidden = true;
    if (viewWater) viewWater.hidden = false;
    if (viewResults) viewResults.hidden = true;

    if (stepItem1) {
      stepItem1.classList.remove('mr-stepper__item--active');
      stepItem1.classList.add('mr-stepper__item--done');
    }
    if (stepItem2) {
      stepItem2.classList.add('mr-stepper__item--active');
      stepItem2.classList.remove('mr-stepper__item--done');
    }
    if (stepItem3) {
      stepItem3.classList.remove('mr-stepper__item--active', 'mr-stepper__item--done');
    }
    if (stepBtn1) stepBtn1.removeAttribute('aria-current');
    if (stepBtn2) {
      stepBtn2.disabled = false;
      stepBtn2.setAttribute('aria-current', 'step');
    }
    if (stepBtn3) {
      stepBtn3.disabled = false;
      stepBtn3.removeAttribute('aria-current');
    }

    // Przenieś mapę do prawego panelu bocznego w Kroku 2 (tryb tylko do podglądu)
    setAdding(false);
    clearPin();
    attachMapToSlot('step2');
    renderWaterSidebarParcels();
    renderParcels(state.customParcels, state.selected);
    if (state.customParcels.length > 0) {
      fitToParcels({ parcels: state.customParcels }, { all: false });
    }

    if (viewWater) viewWater.scrollIntoView({ behavior: 'smooth', block: 'start' });
    announce('Widok 2: Ocena sytuacji wodnej.');
  } else if (step === 3) {
    if (!state.customParcels.length) {
      announce('Wybierz co najmniej 1 działkę, aby przejść do rekomendacji.');
      emit('notice', 'Wybierz co najmniej 1 działkę, aby przejść do rekomendacji.');
      return;
    }

    state.currentStep = 3;
    if (viewSelect) viewSelect.hidden = true;
    if (viewWater) viewWater.hidden = true;
    if (viewResults) viewResults.hidden = false;

    if (stepItem1) {
      stepItem1.classList.remove('mr-stepper__item--active');
      stepItem1.classList.add('mr-stepper__item--done');
    }
    if (stepItem2) {
      stepItem2.classList.remove('mr-stepper__item--active');
      stepItem2.classList.add('mr-stepper__item--done');
    }
    if (stepItem3) {
      stepItem3.classList.add('mr-stepper__item--active');
    }
    if (stepBtn1) stepBtn1.removeAttribute('aria-current');
    if (stepBtn2) {
      stepBtn2.disabled = false;
      stepBtn2.removeAttribute('aria-current');
    }
    if (stepBtn3) {
      stepBtn3.disabled = false;
      stepBtn3.setAttribute('aria-current', 'step');
    }

    // Przenieś mapę do Kroku 3
    attachMapToSlot('step3');
    setMetric('ndvi');

    // Nagłówek wyników
    const count = state.customParcels.length;
    const totalHa = state.customParcels.reduce((sum, p) => sum + (Number(p.area_ha) || 0), 0);
    const metaEl = ref('results-meta');
    if (metaEl) {
      const dzWord = count === 1 ? 'działki' : (count % 10 >= 2 && count % 10 <= 4 && !(count % 100 >= 12 && count % 100 <= 14) ? 'działek' : 'działek');
      metaEl.textContent = `Opracowano dla ${count} ${dzWord} (łączna powierzchnia: ${totalHa.toFixed(2).replace('.', ',')} ha) · Źródła: Sentinel-2, IMGW-PIB, GUGiK`;
    }

    // Uruchom analizę dla zebranych działek
    state.farmName = CUSTOM_FARM;
    state.selected = 0;
    state.expanded = -1;
    runAnalysis();

    // Płynne przewinięcie do góry ekranu rekomendacji
    viewResults.scrollIntoView({ behavior: 'smooth', block: 'start' });
    announce('Widok 3: Opracowane rekomendacje.');
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
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (!root.dataset.theme) applyTheme(null);
    });
  }

  // zakładki w Widoku 2
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
    const metricGroup = ref('mapbar') && ref('mapbar').querySelector('.mr-mapbar__group');
    if (metricGroup) metricGroup.hidden = true;
    initWaterHover(getMap());
    on('map:metric', (m) => setPref('mapMetric', m));
  } catch (err) {
    replace(ref('map'), h('p', { class: 'mr-state mr-state--error', role: 'alert', text: `Nie udało się wczytać mapy: ${err.message}` }));
  }

  // Centralna wyszukiwarka w Kroku 1
  const heroBox = ref('search-hero-box');
  if (heroBox) {
    renderSearchHero(heroBox);
  }

  // Obsługa przycisków Kroku 1 i Kroku 2
  const ctaBtn = root.querySelector('#mr-cta-proceed');
  if (ctaBtn) {
    ctaBtn.addEventListener('click', () => goToStep(2));
  }

  const clearListBtn = root.querySelector('#mr-btn-clear-list');
  if (clearListBtn) {
    clearListBtn.addEventListener('click', () => {
      // eslint-disable-next-line no-alert
      if (window.confirm('Czy na pewno chcesz usunąć wszystkie wybrane działki?')) {
        clearAllParcels();
      }
    });
  }

  const backBtn = root.querySelector('#mr-btn-back');
  if (backBtn) {
    backBtn.addEventListener('click', () => goToStep(1));
  }

  const waterBackBtn = root.querySelector('#mr-btn-water-back');
  if (waterBackBtn) {
    waterBackBtn.addEventListener('click', () => goToStep(1));
  }

  const waterSidebarToggle = root.querySelector('#mr-btn-water-sidebar-toggle');
  if (waterSidebarToggle) {
    waterSidebarToggle.addEventListener('click', () => {
      const sidebar = ref('water-sidebar');
      const workspace = ref('water-workspace');
      const label = ref('water-sidebar-toggle-label');
      if (!sidebar || !workspace) return;
      const willHide = !sidebar.hidden;
      sidebar.hidden = willHide;
      workspace.classList.toggle('mr-water-workspace--sidebar-hidden', willHide);
      waterSidebarToggle.setAttribute('aria-expanded', String(!willHide));
      if (label) label.textContent = willHide ? 'Pokaż mapę i działki' : 'Ukryj panel';
      if (!willHide) {
        const m = getMap();
        if (m) setTimeout(() => m.invalidateSize(), 60);
      }
    });
  }

  const step1Btn = root.querySelector('#mr-step-btn-1');
  if (step1Btn) {
    step1Btn.addEventListener('click', () => goToStep(1));
  }

  const step2Btn = root.querySelector('#mr-step-btn-2');
  if (step2Btn) {
    step2Btn.addEventListener('click', () => {
      if (!step2Btn.disabled) goToStep(2);
    });
  }

  const step3Btn = root.querySelector('#mr-step-btn-3');
  if (step3Btn) {
    step3Btn.addEventListener('click', () => {
      if (!step3Btn.disabled) goToStep(3);
    });
  }

  // Przełącznik zwijania/rozwijania mapy w Widoku 2
  const mapToggleBtn = root.querySelector('[data-action="toggle-map"]');
  if (mapToggleBtn) {
    mapToggleBtn.addEventListener('click', () => {
      const slot = ref('results-map-slot');
      if (!slot) return;
      slot.hidden = !slot.hidden;
      const label = ref('map-toggle-label');
      if (label) label.textContent = slot.hidden ? 'Pokaż mapę' : 'Zwiń mapę';
      if (!slot.hidden) {
        const m = getMap();
        if (m) setTimeout(() => m.invalidateSize(), 50);
      }
    });
  }

  // Zdarzenia aktualizacji listy działek
  on('parcels:changed', () => {
    updateCollectedList();
    if (state.currentStep === 2) renderWaterSidebarParcels();
    renderParcels(state.customParcels, state.currentStep === 2 ? state.selected : -1);
    if (state.customParcels.length > 0) {
      fitToParcels({ parcels: state.customParcels }, { all: false });
    }
  });

  on('map:add', (p) => {
    if (state.currentStep === 2) return;
    setAdding(false);
    addParcel(p);
  });

  on('parcel:remove', (i) => {
    if (state.currentStep === 2) return;
    removeParcel(i);
  });

  on('parcel:select', (i) => {
    state.selected = i < 0 ? -1 : i;
    state.expanded = state.selected;
    highlight(state.selected);
    if (i >= 0) focusParcel(i);
    if (state.currentStep === 2) renderWaterSidebarParcels();
    renderTab(tabs.current());
  });

  on('analysis:retry', () => {
    state.cache.clear();
    runAnalysis();
  });

  on('addmode', (v) => setAdding(v));
  on('map:hint', (t) => {
    const hintText = ref('map-hint-text');
    if (hintText) hintText.textContent = t;
  });
  on('notice', (t) => {
    notices.push(t);
    renderBanner();
  });

  root.querySelector('[data-action="cancel-add"]').addEventListener('click', () => setAdding(false));
  root.addEventListener('keydown', (e) => { if (e.key === 'Escape' && isAddMode()) setAdding(false); });

  // Stan usług w nagłówku
  initStatusDot(root.querySelector('[data-ref="user"]'), () => {
    goToStep(2);
    tabs.select('zrodla', true);
    const card = root.querySelector('#mr-src-st');
    (card || root.querySelector('.mr-panel')).scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  const logout = root.querySelector('[data-action="logout"]');
  if (logout) {
    logout.addEventListener('click', async () => {
      try { await api('/auth/logout', { method: 'POST', noRedirect: true }); } catch (err) { console.warn('[mRolnik] wylogowanie', err); }
      window.location.assign((state.user && state.user.login_url) || '/');
    });
  }

  // Użytkownik
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
      if (u) {
        u.hidden = false;
        replace(u, [icon('leaf'), h('span', { text: me.user.name })]);
      }
      if (logout) logout.hidden = me.auth_mode === 'dev';
    }
  } catch (err) {
    if (err.kind === 'auth') return;
    console.warn('[mRolnik] /auth/me niedostępne', err.message);
  }

  // Wczytanie gospodarstw przykładowych do pamięci (do podpowiedzi i szybkich akcji)
  const setsLoading = loadSets();
  try {
    const p = await api('/api/presets', { timeout: 20000 });
    state.presets = p && Array.isArray(p.farms) ? p.farms.filter((f) => f && f.name && Array.isArray(f.parcels)) : [];
  } catch (err) {
    state.presets = [];
  }
  await setsLoading;

  // Domyślnie KROK 1: startujemy z pustą lub gotową listą bez uruchamiania ciężkiej analizy
  state.farmName = CUSTOM_FARM;
  goToStep(1);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();

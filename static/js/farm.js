// Wybór gospodarstwa, wyszukiwarka miejsc, dodawanie działek, import/eksport CSV.
import { h, icon, replace, debounce, nextId } from './dom.js';
import { api } from './api.js';
import { state, emit, announce, CUSTOM_FARM } from './state.js';
import { importParcels, exportCsv, downloadText, inPoland, MAX_PARCELS } from './csv.js';
import { flyTo, CROP_SUGGESTIONS } from './map.js';

let searchCtrl = null;

/**
 * Pasek narzędzi (jeden rząd, także na telefonie): wyszukiwarka 0–50%, „Dodaj własne pole” 50–75%,
 * „CSV” 75–100%. Sekcje pod spodem domyślnie zwinięte (przyciski z aria-expanded).
 */
export function renderTools(container) {
  const searchId = nextId('mr-search');
  const resultsId = nextId('mr-results');
  const addId = nextId('mr-addsec');
  const csvId = nextId('mr-csvsec');

  const results = h('div', { id: resultsId, class: 'mr-results', hidden: true });
  const searchStatus = h('p', { class: 'mr-visually-hidden', role: 'status', 'aria-live': 'polite' });
  const input = h('textarea', {
    id: searchId, class: 'mr-input mr-input--search', autocomplete: 'off', maxlength: '120', rows: '2',
    spellcheck: 'false', placeholder: 'Szukaj miejsca lub nr działki',
    'aria-label': 'Szukaj miejscowości lub numeru działki (np. Środa Wielkopolska albo 302501_2.0012.114/1)',
  });
  const runSearch = debounce((q) => doSearch(q, results, searchStatus), 250);
  input.addEventListener('input', () => runSearch(input.value.replace(/\s+/g, ' ').trim()));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); doSearch(input.value.replace(/\s+/g, ' ').trim(), results, searchStatus); }
    if (e.key === 'Escape') { results.hidden = true; }
    if (e.key === 'ArrowDown') {
      const first = results.querySelector('button');
      if (first) { e.preventDefault(); first.focus(); }
    }
  });

  const addSec = h('div', { id: addId, class: 'mr-toolsec', hidden: true }, buildAddForm());
  const csvSec = h('div', { id: csvId, class: 'mr-toolsec', hidden: true }, buildCsvSection());
  const toggles = [];
  const toggle = (label, ico, sec) => {
    const b = h('button', {
      type: 'button', class: 'mr-btn mr-btn--tool', 'aria-expanded': 'false', 'aria-controls': sec.id,
      onclick: () => {
        const open = b.getAttribute('aria-expanded') !== 'true';
        toggles.forEach((t) => { t.setAttribute('aria-expanded', 'false'); document.getElementById(t.getAttribute('aria-controls')).hidden = true; });
        b.setAttribute('aria-expanded', String(open));
        sec.hidden = !open;
        if (!open) emit('addmode', false);
      },
    }, [icon(ico), h('span', { text: label })]);
    toggles.push(b);
    return b;
  };

  replace(container, [
    h('div', { class: 'mr-toolrow' }, [
      h('div', { class: 'mr-searchbox' }, [icon('search', 'mr-searchbox__ico'), input]),
      toggle('Dodaj własne pole', 'plus', addSec),
      toggle('CSV', 'upload', csvSec),
    ]),
    searchStatus,
    results,
    addSec,
    csvSec,
  ]);
}

/** Zwinięta sekcja „Przykłady (demo)” – gospodarstwa przykładowe z /api/presets. */
export function renderExamples(container) {
  if (!container) return;
  if (!state.presets.length) { replace(container, []); return; }
  replace(container, h('details', { class: 'mr-details mr-examples' }, [
    h('summary', null, [h('span', { class: 'mr-badge mr-badge--demo', text: 'DEMO' }), ' Przykłady gospodarstw']),
    h('p', { class: 'mr-hint', text: 'Punkty przykładowe do pokazu działania. Granice, NDVI i pogoda są pobierane z prawdziwych źródeł.' }),
    h('ul', { class: 'mr-examples__list' }, state.presets.map((f) => {
      const cur = state.farmName === f.name;
      return h('li', null, h('button', {
        type: 'button', class: 'mr-linkbtn' + (cur ? ' mr-linkbtn--current' : ''),
        'aria-current': cur ? 'true' : null,
        onclick: () => { if (!(cur && state.loading)) emit('farm:change', f.name); },
      }, [`${f.name} (${(f.parcels || []).length} dz.)`, cur && state.loading ? h('span', { class: 'mr-examples__busy', text: ' – wczytywanie…' }) : null]));
    })),
  ]));
}

async function doSearch(q, results, status) {
  if (searchCtrl) searchCtrl.abort();
  if (q.length < 3) {
    results.hidden = true;
    replace(results, []);
    return;
  }
  searchCtrl = new AbortController();
  try {
    const data = await api(`/api/search-location?q=${encodeURIComponent(q)}`, { signal: searchCtrl.signal, timeout: 20000 });
    const list = (data && Array.isArray(data.results) ? data.results : [])
      .filter((r) => r && Number.isFinite(Number(r.lat)) && Number.isFinite(Number(r.lon)))
      .slice(0, 8);
    if (!list.length) {
      replace(results, h('p', { class: 'mr-results__empty', text: 'Nic nie znaleziono. Spróbuj innej nazwy lub numeru działki.' }));
      status.textContent = 'Brak wyników';
    } else {
      replace(results, h('ul', { class: 'mr-results__list', 'aria-label': 'Wyniki wyszukiwania' }, list.map((r) => resultItem(r, results))));
      status.textContent = `Znaleziono ${list.length} wyników`;
    }
    results.hidden = false;
  } catch (err) {
    if (err.kind === 'abort') return;
    replace(results, h('p', { class: 'mr-results__empty', role: 'alert', text: err.message }));
    results.hidden = false;
  }
}

function resultItem(r, results) {
  const lat = Number(r.lat);
  const lon = Number(r.lon);
  const name = String(r.display_name || `${lat}, ${lon}`).slice(0, 200);
  return h('li', { class: 'mr-results__item' }, [
    h('button', {
      type: 'button', class: 'mr-results__go',
      onclick: () => { flyTo(lat, lon, 15); results.hidden = true; announce(`Pokazano na mapie: ${name}`); },
      onkeydown: navKeys,
    }, [icon('pin'), h('span', { text: name })]),
    h('button', {
      type: 'button', class: 'mr-btn mr-btn--small mr-btn--secondary',
      'aria-label': `Dodaj działkę: ${name}`,
      onclick: () => {
        results.hidden = true;
        addParcel({ lat, lon, id_tag: name.slice(0, 60), parcel_id: r.parcel_id ? String(r.parcel_id).slice(0, 120) : undefined, area_ha: r.area_ha });
      },
      onkeydown: navKeys,
    }, [icon('plus'), 'Dodaj']),
  ]);
}

function navKeys(e) {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  const btns = Array.from(e.currentTarget.closest('.mr-results').querySelectorAll('.mr-results__go'));
  const i = btns.indexOf(e.currentTarget.closest('li').querySelector('.mr-results__go'));
  const next = btns[e.key === 'ArrowDown' ? i + 1 : i - 1];
  if (next) { e.preventDefault(); next.focus(); }
}

/** Dodaje działkę użytkownika (tylko w pamięci) i uruchamia analizę „Moich działek”. */
export function addParcel(p) {
  if (!inPoland(p.lat, p.lon)) {
    announce('Współrzędne poza granicami Polski.');
    return false;
  }
  if (state.customParcels.length >= MAX_PARCELS) {
    emit('notice', `Można dodać maksymalnie ${MAX_PARCELS} działek.`);
    return false;
  }
  const parcel = { lat: p.lat, lon: p.lon, id_tag: p.id_tag || `Działka ${state.customParcels.length + 1}` };
  if (p.crop) parcel.crop = p.crop;
  if (p.parcel_id) parcel.parcel_id = p.parcel_id;
  state.customParcels.push(parcel);
  emit('farm:change', CUSTOM_FARM);
  announce(`Dodano działkę ${parcel.id_tag}. Trwa analiza.`);
  return true;
}

function fieldRow(id, label, input, hint) {
  const hintId = hint ? `${id}-hint` : null;
  if (hintId) { input.setAttribute('aria-describedby', hintId); input.dataset.hint = hintId; }
  return h('div', { class: 'mr-field' }, [
    h('label', { for: id, class: 'mr-label', text: label }),
    hint ? h('p', { id: hintId, class: 'mr-hint', text: hint }) : null,
    input,
  ]);
}

function buildAddForm() {
  const id = nextId('mr-add');
  const err = h('p', { id: `${id}-err`, class: 'mr-error', role: 'alert', hidden: true });
  const teryt = h('input', { id: `${id}-teryt`, class: 'mr-input', maxlength: '120', autocomplete: 'off', inputmode: 'text' });
  const lat = h('input', { id: `${id}-lat`, class: 'mr-input', inputmode: 'decimal', autocomplete: 'off' });
  const lon = h('input', { id: `${id}-lon`, class: 'mr-input', inputmode: 'decimal', autocomplete: 'off' });
  const name = h('input', { id: `${id}-name`, class: 'mr-input', maxlength: '60', autocomplete: 'off' });
  const crop = h('input', { id: `${id}-crop`, class: 'mr-input', maxlength: '60', autocomplete: 'off', list: `${id}-crops` });
  const crops = h('datalist', { id: `${id}-crops` }, CROP_SUGGESTIONS.map((c) => h('option', { value: c })));

  const showErr = (msg, el) => {
    err.textContent = msg;
    err.hidden = !msg;
    [teryt, lat, lon].forEach((x) => { x.removeAttribute('aria-invalid'); if (x.dataset.hint) x.setAttribute('aria-describedby', x.dataset.hint); else x.removeAttribute('aria-describedby'); });
    if (el) { el.setAttribute('aria-invalid', 'true'); el.setAttribute('aria-describedby', [el.dataset.hint, `${id}-err`].filter(Boolean).join(' ')); el.focus(); }
  };

  const submit = async (e) => {
    e.preventDefault();
    showErr('');
    const tag = name.value.trim();
    const cropV = crop.value.trim();
    const t = teryt.value.trim();
    if (t) {
      if (!/^[0-9]{6}_[0-9]\.[0-9]{4}\.[0-9A-Za-z/_.-]{1,30}$/.test(t)) {
        showErr('Numer działki ma format np. 302501_2.0012.114/1 (TERYT obrębu i numer działki).', teryt);
        return;
      }
      btn.disabled = true;
      try {
        const data = await api(`/api/search-location?q=${encodeURIComponent(t)}`, { timeout: 30000 });
        const r = data && Array.isArray(data.results) ? data.results.find((x) => Number.isFinite(Number(x.lat))) : null;
        if (!r) { showErr('Nie znaleziono działki o tym numerze w ewidencji (ULDK).', teryt); return; }
        if (addParcel({ lat: Number(r.lat), lon: Number(r.lon), id_tag: tag || `Dz. ${t.split('.').pop()}`, crop: cropV, parcel_id: t })) form.reset();
      } catch (ex) {
        showErr(ex.message, teryt);
      } finally {
        btn.disabled = false;
      }
      return;
    }
    const la = Number(lat.value.replace(',', '.'));
    const lo = Number(lon.value.replace(',', '.'));
    if (!lat.value.trim() || !Number.isFinite(la)) { showErr('Podaj numer działki albo szerokość geograficzną (np. 52,2020).', lat); return; }
    if (!lon.value.trim() || !Number.isFinite(lo)) { showErr('Podaj długość geograficzną (np. 17,2012).', lon); return; }
    if (!inPoland(la, lo)) { showErr('Współrzędne muszą leżeć w Polsce (szer. 49–55, dł. 14–24,2).', lat); return; }
    if (addParcel({ lat: la, lon: lo, id_tag: tag, crop: cropV })) form.reset();
  };

  const btn = h('button', { type: 'submit', class: 'mr-btn mr-btn--primary' }, [icon('plus'), 'Dodaj i analizuj']);
  const form = h('form', { class: 'mr-addform', novalidate: true, onsubmit: submit }, [
    err,
    fieldRow(`${id}-teryt`, 'Numer działki ewidencyjnej (TERYT)', teryt, 'Np. 302501_2.0012.114/1 – znajdziesz go w wypisie z rejestru gruntów.'),
    h('p', { class: 'mr-or', text: 'albo' }),
    h('div', { class: 'mr-row2' }, [
      fieldRow(`${id}-lat`, 'Szerokość (N)', lat),
      fieldRow(`${id}-lon`, 'Długość (E)', lon),
    ]),
    h('div', { class: 'mr-row2' }, [
      fieldRow(`${id}-name`, 'Nazwa (opcjonalnie)', name),
      fieldRow(`${id}-crop`, 'Uprawa (opcjonalnie)', crop),
    ]),
    crops,
    btn,
  ]);

  return [
    h('button', { type: 'button', class: 'mr-btn mr-btn--secondary mr-btn--block', onclick: () => emit('addmode', true) },
      [icon('pin'), 'Wskaż pole na mapie']),
    h('p', { class: 'mr-or', text: 'albo wpisz numer działki lub współrzędne' }),
    form,
  ];
}

function buildCsvSection() {
  const fileId = nextId('mr-csvfile');
  const file = h('input', { id: fileId, type: 'file', accept: '.csv,text/csv', class: 'mr-file' });
  const csvMsg = h('div', { class: 'mr-csvmsg', role: 'status', 'aria-live': 'polite' });
  file.addEventListener('change', async () => {
    const f = file.files && file.files[0];
    if (!f) return;
    if (f.size > 256 * 1024) { replace(csvMsg, h('p', { class: 'mr-error', text: 'Plik jest za duży (maks. 256 KB).' })); return; }
    const text = await f.text();
    const { parcels, errors } = importParcels(text);
    const room = MAX_PARCELS - state.customParcels.length;
    const accepted = parcels.slice(0, Math.max(0, room));
    if (parcels.length > accepted.length) errors.push(`Pominięto ${parcels.length - accepted.length} działek ponad limit ${MAX_PARCELS}.`);
    state.customParcels.push(...accepted);
    replace(csvMsg, [
      h('p', { text: `Wczytano ${accepted.length} działek.` }),
      errors.length ? h('ul', { class: 'mr-csvmsg__errors' }, errors.slice(0, 10).map((x) => h('li', { text: x }))) : null,
      errors.length > 10 ? h('p', { text: `…i ${errors.length - 10} innych uwag.` }) : null,
    ]);
    file.value = '';
    if (accepted.length) emit('farm:change', CUSTOM_FARM);
  });

  return [
    h('p', { class: 'mr-hint', text: 'Kolumny: id_tag; lat; lon; crop; soil; parcel_id (separator ; lub ,). Wiersze bez współrzędnych są pomijane – nie zgadujemy położenia.' }),
    h('div', { class: 'mr-csv__btns' }, [
      h('label', { for: fileId, class: 'mr-btn mr-btn--secondary mr-btn--file' }, [icon('upload'), 'Wczytaj plik CSV']),
      file,
      h('button', { type: 'button', class: 'mr-btn mr-btn--secondary', onclick: exportCurrent }, [icon('download'), 'Zapisz działki do CSV']),
    ]),
    csvMsg,
  ];
}

function exportCurrent() {
  const a = state.analysis;
  const req = state.request || [];
  const rows = req.map((r, i) => ({ req: r, res: a && a.parcels ? a.parcels[i] : null }));
  const cols = [
    { label: 'id_tag', get: (x) => x.req.id_tag || '' },
    { label: 'parcel_id', get: (x) => (x.res && x.res.parcel_id) || x.req.parcel_id || '' },
    { label: 'lat', get: (x) => x.req.lat },
    { label: 'lon', get: (x) => x.req.lon },
    { label: 'crop', get: (x) => x.req.crop || (x.res && x.res.crop) || '' },
    { label: 'soil', get: (x) => x.req.soil || (x.res && x.res.soil) || '' },
    { label: 'area_ha', get: (x) => (x.res && x.res.area_ha !== null && x.res.area_ha !== undefined ? x.res.area_ha : '') },
    { label: 'ndvi_mean', get: (x) => (x.res && x.res.ndvi ? x.res.ndvi.mean : '') },
    { label: 'ryzyko', get: (x) => (x.res ? x.res.risk : '') },
    { label: 'status', get: (x) => (x.res ? x.res.status_label : '') },
  ];
  if (!rows.length) { emit('notice', 'Brak działek do zapisania.'); return; }
  downloadText('mrolnik-dzialki.csv', exportCsv(cols, rows));
}

// Wybór gospodarstwa, wyszukiwarka miejsc, dodawanie działek, import/eksport CSV.
import { h, icon, replace, debounce, nextId } from './dom.js';
import { api } from './api.js';
import { state, emit, on, announce, CUSTOM_FARM } from './state.js';
import { importParcels, exportCsv, downloadText, inPoland, MAX_PARCELS } from './csv.js';
import { flyTo } from './map.js';
import { plantState, waterState } from './parcelstate.js';
import { myParcelGroups, SOURCE } from './myparcels.js';
import { parcelSearchButton } from './parcelsearch.js';
import { setKey, renameSet, deleteSet } from './usersets.js';

let searchCtrl = null;

/**
 * Pasek narzędzi (jeden rząd, także na telefonie): wyszukiwarka, przycisk „CSV” i lista rozwijana „Moje działki”.
 * Pole dodaje się kliknięciem na mapie (pinezka) albo z wyników wyszukiwania.
 */
export function renderTools(container) {
  const searchId = nextId('mr-search');
  const resultsId = nextId('mr-results');
  const csvId = nextId('mr-csvsec');

  const results = h('div', { id: resultsId, class: 'mr-results', hidden: true });
  const searchStatus = h('p', { class: 'mr-visually-hidden', role: 'status', 'aria-live': 'polite' });
  const input = h('input', {
    id: searchId, type: 'search', class: 'mr-input mr-input--search', autocomplete: 'off', maxlength: '120',
    spellcheck: 'false', enterkeyhint: 'search', placeholder: 'Szukaj miejsca lub nr działki',
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

  const csvSec = h('div', { id: csvId, class: 'mr-toolsec', hidden: true }, buildCsvSection());
  const searchBox = h('div', { class: 'mr-searchbox' }, [icon('search', 'mr-searchbox__ico'), input, results]);
  // klik poza wyszukiwarką zamyka listę wyników
  document.addEventListener('click', (e) => { if (!searchBox.contains(e.target)) results.hidden = true; });
  input.addEventListener('focus', () => { if (results.childNodes.length) results.hidden = false; });
  const toggles = [];
  const toggle = (label, ico, sec) => {
    const b = h('button', {
      type: 'button', class: 'mr-btn mr-btn--tool', 'aria-expanded': 'false', 'aria-controls': sec.id,
      onclick: () => {
        const open = b.getAttribute('aria-expanded') !== 'true';
        toggles.forEach((t) => { t.setAttribute('aria-expanded', 'false'); document.getElementById(t.getAttribute('aria-controls')).hidden = true; });
        b.setAttribute('aria-expanded', String(open));
        sec.hidden = !open;
      },
    }, [icon(ico), h('span', null, label)]);
    toggles.push(b);
    return b;
  };

  replace(container, [
    h('div', { class: 'mr-toolrow' }, [
      searchBox,
      toggle([h('span', { class: 'mr-btn__more', text: 'Wczytaj ' }), 'CSV'], 'upload', csvSec),
      buildMyParcelsMenu(),
      parcelSearchButton(),
    ]),
    searchStatus,
    csvSec,
  ]);
}

/**
 * Przycisk „Moje działki” z listą rozwijaną (obok „Wczytaj CSV”):
 *  1. „Wybierz z rejestru” (DEMO) – rozwija listę gospodarstw z rejestru (myparcels.js; na razie przykładowe),
 *  2. zestawy użytkownika z własnymi nazwami (zmiana nazwy, usunięcie; zapis na serwerze – usersets.js),
 *  3. „Pola dodane przeze mnie”.
 */
function buildMyParcelsMenu() {
  const menuId = nextId('mr-mymenu');
  const menu = h('div', { id: menuId, class: 'mr-menu', hidden: true });
  const btn = h('button', {
    type: 'button', class: 'mr-btn mr-btn--tool', 'aria-haspopup': 'true', 'aria-expanded': 'false', 'aria-controls': menuId,
    onclick: () => (menu.hidden ? open() : close()),
  }, [
    icon('layers'),
    h('span', null, [h('span', { class: 'mr-btn__more', text: 'Moje ' }), h('span', { class: 'mr-btn__cap', text: 'działki' })]),
    icon('chevronDown', 'mr-btn__chev'),
  ]);
  const wrap = h('div', { class: 'mr-menuwrap' }, [btn, menu]);
  let regOpen = false;
  let editing = null;     // id zestawu w trakcie zmiany nazwy
  let msg = '';

  const pick = (key) => { close(true); if (state.farmName !== key) emit('farm:change', key); };
  const itemBtn = (key, name, count, extra = []) => {
    const cur = state.farmName === key;
    return h('button', {
      type: 'button', class: 'mr-menu__item' + (cur ? ' mr-menu__item--current' : ''), 'aria-current': cur ? 'true' : null,
      onclick: () => pick(key), onkeydown: menuKeys,
    }, [h('span', { class: 'mr-menu__name', text: name }), ...extra, h('span', { class: 'mr-menu__count', text: `${count} dz.` })]);
  };

  function setRow(set) {
    const key = setKey(set.id);
    if (editing === set.id) {
      const inp = h('input', { type: 'text', class: 'mr-input mr-menu__rename', value: set.name, maxlength: '80', 'aria-label': `Nowa nazwa zestawu ${set.name}` });
      const save = async () => {
        const n = inp.value.replace(/\s+/g, ' ').trim();
        if (!n || n === set.name) { editing = null; render(); return; }
        try { await renameSet(set.id, n); editing = null; msg = ''; announce(`Zmieniono nazwę zestawu na „${n}”.`); }
        catch (err) { msg = (err && typeof err.detail === 'string' && err.detail) || err.message; }
        render();
      };
      inp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); save(); }
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); editing = null; render(); }
      });
      setTimeout(() => { inp.focus(); inp.select(); }, 0);
      return h('li', { class: 'mr-menu__setrow' }, [inp,
        h('button', { type: 'button', class: 'mr-btn mr-btn--small mr-btn--primary', onclick: save }, 'Zapisz'),
        h('button', { type: 'button', class: 'mr-btn mr-btn--small mr-btn--ghost', onclick: () => { editing = null; render(); } }, 'Anuluj')]);
    }
    return h('li', { class: 'mr-menu__setrow' }, [
      itemBtn(key, set.name, (set.parcels || []).length),
      h('button', { type: 'button', class: 'mr-btn mr-btn--icon mr-btn--ghost mr-menu__act', title: 'Zmień nazwę',
        'aria-label': `Zmień nazwę zestawu ${set.name}`, onclick: (e) => { e.stopPropagation(); editing = set.id; render(); } }, icon('edit')),
      h('button', { type: 'button', class: 'mr-btn mr-btn--icon mr-btn--ghost mr-menu__act', title: 'Usuń zestaw',
        'aria-label': `Usuń zestaw ${set.name}`, onclick: async (e) => {
          e.stopPropagation();
          // eslint-disable-next-line no-alert
          if (!window.confirm(`Usunąć zestaw „${set.name}” (${(set.parcels || []).length} działek)? Tej operacji nie można cofnąć.`)) return;
          try { await deleteSet(set.id); msg = ''; announce(`Usunięto zestaw „${set.name}”.`); }
          catch (err) { msg = err.message; }
          render();
        } }, icon('trash')),
    ]);
  }

  function render() {
    const groups = myParcelGroups();
    const regId = `${menuId}-reg`;
    const reg = h('button', {
      type: 'button', class: 'mr-menu__item mr-menu__reg', 'aria-expanded': String(regOpen), 'aria-controls': regId,
      onclick: () => { regOpen = !regOpen; render(); menu.querySelector('.mr-menu__reg').focus(); }, onkeydown: menuKeys,
    }, [icon('layers'), h('span', { class: 'mr-menu__name', text: 'Wybierz z rejestru' }),
      SOURCE.demo ? h('span', { class: 'mr-badge mr-badge--demo', text: 'DEMO' }) : null, icon('chevronDown', 'mr-menu__chev')]);
    const regList = h('ul', { class: 'mr-menu__list mr-menu__sub', id: regId, hidden: !regOpen, 'aria-label': 'Gospodarstwa z rejestru' },
      groups.map((g) => h('li', null, itemBtn(g.name, g.name, g.count))));
    const sets = state.userSets || [];
    const items = sets.map(setRow);
    if (state.customParcels.length) items.push(h('li', { class: 'mr-menu__sep' }, itemBtn(CUSTOM_FARM, 'Pola dodane przeze mnie', state.customParcels.length)));
    replace(menu, [
      h('p', { class: 'mr-menu__head', text: 'Moje działki' }),
      h('ul', { class: 'mr-menu__list' }, [h('li', null, reg)]),
      regOpen ? h('p', { class: 'mr-menu__hint', text: SOURCE.hint }) : null,
      regList,
      h('p', { class: 'mr-menu__sub-head', text: 'Moje zestawy' }),
      items.length ? h('ul', { class: 'mr-menu__list', 'aria-label': 'Moje zestawy działek' }, items)
        : h('p', { class: 'mr-menu__hint', text: state.setsError ? `Nie wczytano zestawów: ${state.setsError}` : 'Nie masz jeszcze zestawów – zapisz wynik filtra działek albo dodaj działki z listy wyników.' }),
      msg ? h('p', { class: 'mr-error', role: 'alert', text: msg }) : null,
    ]);
  }
  function open() {
    render();
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    const first = menu.querySelector('.mr-menu__item--current') || menu.querySelector('.mr-menu__item');
    if (first) first.focus({ preventScroll: true });
  }
  function close(focusBtn) {
    if (menu.hidden) return;
    menu.hidden = true;
    editing = null;
    btn.setAttribute('aria-expanded', 'false');
    if (focusBtn) btn.focus({ preventScroll: true });
  }
  function menuKeys(e) {
    const btns = Array.from(menu.querySelectorAll('.mr-menu__item')).filter((x) => x.offsetParent !== null);
    const i = btns.indexOf(e.currentTarget);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const next = btns[e.key === 'ArrowDown' ? i + 1 : i - 1];
      if (next) { e.preventDefault(); next.focus(); }
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault(); btns[e.key === 'Home' ? 0 : btns.length - 1].focus();
    }
  }
  on('sets:change', () => { if (!menu.hidden) render(); });
  wrap.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !menu.hidden) { e.stopPropagation(); close(true); } });
  document.addEventListener('click', (e) => { if (!wrap.contains(e.target) && e.target.isConnected) close(false); });
  return wrap;
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
    { label: 'kondycja_roslin', get: (x) => (x.res ? plantState(x.res).text : '') },
    { label: 'wilgotnosc_gleby', get: (x) => (x.res ? waterState(x.res).text : '') },
  ];
  if (!rows.length) { emit('notice', 'Brak działek do zapisania.'); return; }
  downloadText('mrolnik-dzialki.csv', exportCsv(cols, rows));
}

// „Moje działki” – zestawy działek użytkownika z własnymi nazwami (zapis na serwerze: /api/user-sets) oraz bieżący
// wynik filtra działek jako tymczasowy zestaw na Głównej. Klucz gospodarstwa w state.farmName:
//   'set:<id>'  – zestaw użytkownika,  '__filter' – wynik filtra,  '__custom' – pola dodane przeze mnie,  inne – rejestr (DEMO).
import { h, icon, nextId } from './dom.js';
import { api } from './api.js';
import { state, emit, announce } from './state.js';

export const FILTER_FARM = '__filter';
export const PAGE = 50;          // działek na jedno zapytanie /api/analyze (limit serwera; tyle samo co MAX_PARCELS w csv.js)
const CHUNK = 400;               // działek na jedno zapytanie zapisu (limit rozmiaru treści zapytania 256 kB)

state.userSets = [];
state.filterSet = null;          // { name, parcels, total }
state.setPage = 0;

export const setKey = (id) => `set:${id}`;

export function findSet(key) {
  if (key === FILTER_FARM) return state.filterSet;
  if (typeof key === 'string' && key.startsWith('set:')) return state.userSets.find((s) => setKey(s.id) === key) || null;
  return null;
}

export function isSetKey(key) { return key === FILTER_FARM || (typeof key === 'string' && key.startsWith('set:')); }

export async function loadSets() {
  try {
    const d = await api('/api/user-sets', { timeout: 20000 });
    state.userSets = Array.isArray(d && d.sets) ? d.sets : [];
    state.setsError = null;
  } catch (err) {
    state.userSets = [];
    state.setsError = err.message;
  }
  emit('sets:change');
}

function upsert(s) {
  const i = state.userSets.findIndex((x) => x.id === s.id);
  if (i >= 0) state.userSets[i] = s; else state.userSets.push(s);
  emit('sets:change');
  if (state.farmName === setKey(s.id)) emit('set:updated', s);
}

const slim = (p) => {
  const o = { lat: Number(p.lat), lon: Number(p.lon) };
  ['parcel_id', 'id_tag', 'crop', 'soil'].forEach((k) => { if (p[k]) o[k] = String(p[k]).slice(0, 120); });
  return o;
};

/** Nowy zestaw (duże listy wysyłane w częściach). Zwraca zapisany zestaw. */
export async function createSet(name, parcels) {
  const ps = parcels.map(slim);
  const r = await api('/api/user-sets', { method: 'POST', body: { name, parcels: ps.slice(0, CHUNK) }, timeout: 30000 });
  let s = r.set;
  for (let i = CHUNK; i < ps.length; i += CHUNK) {
    s = (await api(`/api/user-sets/${s.id}`, { method: 'PATCH', body: { add: ps.slice(i, i + CHUNK) }, timeout: 30000 })).set;
  }
  upsert(s);
  return s;
}

export async function addToSet(id, parcels) {
  const ps = parcels.map(slim);
  let s = null;
  for (let i = 0; i < ps.length; i += CHUNK) {
    s = (await api(`/api/user-sets/${id}`, { method: 'PATCH', body: { add: ps.slice(i, i + CHUNK) }, timeout: 30000 })).set;
  }
  if (s) upsert(s);
  return s;
}

export async function renameSet(id, name) {
  const s = (await api(`/api/user-sets/${id}`, { method: 'PATCH', body: { name }, timeout: 20000 })).set;
  upsert(s);
  return s;
}

export async function deleteSet(id) {
  await api(`/api/user-sets/${id}`, { method: 'DELETE', timeout: 20000 });
  state.userSets = state.userSets.filter((s) => s.id !== id);
  emit('sets:change');
}

function errText(err) {
  const d = err && err.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d.length) return d.map((x) => String((x && x.msg) || '')).join(' ');
  return (err && err.message) || 'Nie udało się zapisać.';
}

function dzialki(n) {
  if (n === 1) return 'działka';
  const m10 = n % 10;
  const m100 = n % 100;
  return m10 >= 2 && m10 <= 4 && !(m100 >= 12 && m100 <= 14) ? 'działki' : 'działek';
}

const today = () => new Intl.DateTimeFormat('pl-PL', { day: '2-digit', month: '2-digit' }).format(new Date());

/**
 * Okno „Zapisz w Moich działkach”: nowy zestaw (nazwa z podpowiedzią) albo dodanie do istniejącego.
 * opts: { parcels, title, suggested, preferExisting }. Zwraca Promise<zestaw | null>.
 */
export function saveDialog(opts) {
  const parcels = opts.parcels || [];
  return new Promise((resolve) => {
    const nameId = nextId('mr-set-name');
    const selId = nextId('mr-set-sel');
    const grp = nextId('mr-set-mode');
    const sets = state.userSets;
    const suggested = opts.suggested || `Wynik filtra ${today()}`;
    const name = h('input', { id: nameId, type: 'text', class: 'mr-input', maxlength: '80', value: suggested, autocomplete: 'off' });
    const sel = h('select', { id: selId, class: 'mr-select' }, sets.map((s) => h('option', { value: s.id, text: `${s.name} (${s.parcels.length})` })));
    const modeNew = h('input', { type: 'radio', name: grp, value: 'new', id: `${grp}-n`, class: 'mr-radio' });
    const modeOld = h('input', { type: 'radio', name: grp, value: 'old', id: `${grp}-o`, class: 'mr-radio', disabled: !sets.length });
    (opts.preferExisting && sets.length ? modeOld : modeNew).checked = true;
    const msg = h('p', { class: 'mr-error', role: 'alert', hidden: true });
    const save = h('button', { type: 'submit', class: 'mr-btn mr-btn--primary' }, [icon('check'), 'Zapisz']);
    const cancel = h('button', { type: 'button', class: 'mr-btn mr-btn--secondary', onclick: () => done(null) }, 'Anuluj');
    const sync = () => { name.disabled = !modeNew.checked; sel.disabled = !modeOld.checked; };
    modeNew.addEventListener('change', sync);
    modeOld.addEventListener('change', sync);
    sync();
    const titleId = nextId('mr-set-title');
    const form = h('form', { class: 'mr-setdlg__form', novalidate: true }, [
      h('h2', { class: 'mr-setdlg__title', id: titleId, text: opts.title || 'Zapisz w Moich działkach' }),
      h('p', { class: 'mr-hint', text: `${parcels.length} ${dzialki(parcels.length)} do zapisania.` }),
      h('div', { class: 'mr-setdlg__opt' }, [modeNew, h('label', { for: modeNew.id, text: 'Nowy zestaw' })]),
      h('div', { class: 'mr-field mr-setdlg__in' }, [h('label', { class: 'mr-label', for: nameId, text: 'Nazwa zestawu' }), name]),
      h('div', { class: 'mr-setdlg__opt' }, [modeOld, h('label', { for: modeOld.id, text: 'Dodaj do istniejącego zestawu' })]),
      sets.length ? h('div', { class: 'mr-field mr-setdlg__in' }, [h('label', { class: 'mr-label', for: selId, text: 'Zestaw' }), sel])
        : h('p', { class: 'mr-hint mr-setdlg__in', text: 'Nie masz jeszcze zapisanych zestawów.' }),
      msg,
      h('div', { class: 'mr-setdlg__btns' }, [save, cancel]),
    ]);
    const dlg = h('dialog', { class: 'mr-setdlg', 'aria-labelledby': titleId }, form);
    let finished = false;
    function done(v) {
      if (finished) return;
      finished = true;
      dlg.close();
      dlg.remove();
      resolve(v);
    }
    dlg.addEventListener('cancel', (e) => { e.preventDefault(); done(null); });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      msg.hidden = true;
      save.disabled = true;
      try {
        let s;
        if (modeNew.checked) {
          const n = name.value.replace(/\s+/g, ' ').trim();
          if (!n) throw new Error('Podaj nazwę zestawu.');
          s = await createSet(n, parcels);
          announce(`Zapisano zestaw „${s.name}” (${s.parcels.length} ${dzialki(s.parcels.length)}).`);
        } else {
          s = await addToSet(sel.value, parcels);
          announce(`Dodano do zestawu „${s.name}” – teraz ${s.parcels.length} ${dzialki(s.parcels.length)}.`);
        }
        done(s);
      } catch (err) {
        msg.textContent = errText(err);
        msg.hidden = false;
        save.disabled = false;
      }
    });
    (document.getElementById('mrolnik-root') || document.body).append(dlg);
    dlg.showModal();
    (modeNew.checked ? name : sel).focus();
    if (modeNew.checked) name.select();
  });
}

/** Wynik filtra jako bieżący zestaw na Głównej. */
export function showFilterSet(parcels, label) {
  state.filterSet = { name: label, parcels, total: parcels.length };
  emit('farm:change', FILTER_FARM);
}

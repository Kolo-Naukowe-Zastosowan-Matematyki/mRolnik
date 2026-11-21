// Prosty stan aplikacji + zdarzenia. Ostatnia analiza trzymana w pamięci (bez localStorage).

const listeners = new Map();

export const state = {
  root: null,
  user: null,
  presets: [],          // [{name, parcels:[{id_tag, lat, lon, crop, soil}]}]
  farmName: null,       // nazwa wybranego gospodarstwa lub '__custom'
  cropCategories: [],   // kategorie upraw dodane przez rolnika [{id, name, parcels: []}]
  activeCategoryId: null, // id aktualnie aktywnej kategorii uprawy
  customParcels: [],    // zsynchronizowana płaska lista działek ze wszystkich kategorii
  request: [],          // działki wysłane do analizy
  analysis: null,       // ostatnia odpowiedź /api/analyze
  analysisError: null,
  loading: false,
  selected: 0,          // indeks wybranej działki
  expanded: -1,         // rozwinięta karta działki na Głównej (domyślnie wszystkie zwinięte)
  cache: new Map(),     // klucz żądania → odpowiedź (pamięć podręczna sesji)
  currentStep: 1,       // 1 = wybór działek, 2 = ocena sytuacji wodnej, 3 = rekomendacje
};

export const CUSTOM_FARM = '__custom';

export function on(evt, fn) {
  if (!listeners.has(evt)) listeners.set(evt, new Set());
  listeners.get(evt).add(fn);
  return () => listeners.get(evt).delete(fn);
}

export function emit(evt, payload) {
  (listeners.get(evt) || []).forEach((fn) => {
    try { fn(payload); } catch (e) { console.error(`[mRolnik] błąd obsługi zdarzenia ${evt}`, e); }
  });
}

export function selectedParcel() {
  const a = state.analysis;
  if (!a || !Array.isArray(a.parcels) || !a.parcels.length) return null;
  return a.parcels[Math.max(0, Math.min(state.selected, a.parcels.length - 1))] || null;
}

/** Współrzędne do zapytań lokalnych (woda, ekoschemat): wybrana działka lub pierwsza z żądania. */
export function focusPoint() {
  const p = selectedParcel();
  if (p && Array.isArray(p.centroid) && p.centroid.length === 2) return { lat: p.centroid[0], lon: p.centroid[1], parcel: p };
  const r = state.request[state.selected] || state.request[0];
  return r ? { lat: r.lat, lon: r.lon, parcel: null } : null;
}

let liveEl = null;
export function setLive(el) { liveEl = el; }
/** Komunikat dla czytników ekranu (aria-live). */
export function announce(text) {
  if (!liveEl) return;
  liveEl.textContent = '';
  setTimeout(() => { liveEl.textContent = text; }, 50);
}

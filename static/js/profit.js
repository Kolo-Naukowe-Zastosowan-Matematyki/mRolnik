// Wspólne dla „Optymalnej konfiguracji upraw” (Plony) i „Kalkulatora zysku” (Kalkulatory).
// Wzory są po stronie serwera (src/calculators/profit.py, src/data/profit_data.py).
import { api } from './api.js';

const cache = new Map();

/** Działki gospodarstwa do kalkulatora: [{id, area_ha, crop}] (bez działek bez powierzchni). */
export function farmParcels(a) {
  return (a && Array.isArray(a.parcels) ? a.parcels : [])
    .map((p, i) => ({ id: p.id_tag || `Działka ${i + 1}`, area_ha: Number(p.area_ha), crop: p.crop || null, idx: i }))
    .filter((p) => Number.isFinite(p.area_ha) && p.area_ha > 0);
}

/** Województwo z największą powierzchnią działek (ceny i plony GUS są wojewódzkie). */
export function farmVoivodeship(a) {
  const m = new Map();
  (a && Array.isArray(a.parcels) ? a.parcels : []).forEach((p) => {
    if (p.voivodeship) m.set(p.voivodeship, (m.get(p.voivodeship) || 0) + (Number(p.area_ha) || 0.01));
  });
  const all = Array.from(m.entries()).sort((x, y) => y[1] - x[1]);
  return { name: all.length ? all[0][0] : null, others: all.slice(1).map(([v]) => v) };
}

function cached(key, fn) {
  if (!cache.has(key)) cache.set(key, fn().catch((e) => { cache.delete(key); throw e; }));
  return cache.get(key);
}

const strip = (ps) => ps.map(({ idx, ...rest }) => rest);

export function loadPlan(parcels, voivodeship, opts = {}) {
  const body = { voivodeship, parcels: strip(parcels), include_services: opts.services !== false, allowed: opts.allowed || null };
  return cached(`plan:${JSON.stringify(body)}`, () => api('/api/calculators/optimal-plan', { method: 'POST', body, timeout: 90000 }));
}

export function loadProfit(parcels, voivodeship, opts = {}) {
  const body = { voivodeship, parcels: strip(parcels), include_services: opts.services !== false };
  return api('/api/calculators/profit', { method: 'POST', body, timeout: 90000 });
}

export function loadCatalog(voivodeship, services = true) {
  return cached(`cat:${voivodeship}:${services}`, () => api(`/api/calculators/profit/catalog?voivodeship=${encodeURIComponent(voivodeship)}&include_services=${services}`, { timeout: 90000 }));
}

export const zl = (v, digits = 0) => new Intl.NumberFormat('pl-PL', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(v));
export const zlSigned = (v) => `${Number(v) > 0 ? '+' : Number(v) < 0 ? '−' : ''}${zl(Math.abs(Number(v)))} zł`;

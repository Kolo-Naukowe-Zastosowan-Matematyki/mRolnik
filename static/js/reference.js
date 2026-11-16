// Dane referencyjne z serwera (GUS BDL, średnia wieloletnia NDVI). Przeglądarka łączy się tylko z własnym serwerem
// (CSP connect-src 'self'); serwer pobiera i buforuje dane z serwisów państwowych/UE.
import { api } from './api.js';

const ndviCache = new Map();
const yieldCache = new Map();

function remember(map, key, make) {
  if (!map.has(key)) map.set(key, make().catch((e) => { map.delete(key); throw e; }));
  return map.get(key);
}

/** Średnie NDVI 2019–2025 dla miesięcy kalendarzowych (Copernicus Sentinel-2) dla działki albo {parcel_ids: [...]}. */
export function ndviReference(p) {
  const c = Array.isArray(p && p.centroid) ? p.centroid : null;
  const qs = new URLSearchParams();
  if (p && Array.isArray(p.parcel_ids) && p.parcel_ids.length) qs.set('parcel_ids', p.parcel_ids.slice().sort().join(','));
  else if (p && p.parcel_id) qs.set('parcel_id', p.parcel_id);
  if (c) { qs.set('lat', String(c[0])); qs.set('lon', String(c[1])); }
  const key = qs.toString();
  if (!key) return Promise.reject(new Error('Brak identyfikatora działki.'));
  return remember(ndviCache, key, () => api(`/api/reference/ndvi?${key}`, { timeout: 45000 }));
}

/** Map<miesiąc 1–12, średnia> z odpowiedzi /api/reference/ndvi. */
export function ndviByMonth(ref) {
  const m = new Map();
  (ref && Array.isArray(ref.months) ? ref.months : []).forEach((r) => {
    if (r && r.mean !== null && r.mean !== undefined) m.set(Number(r.month), Number(r.mean));
  });
  return m;
}

/** Plony z 1 ha (GUS BDL) dla uprawy w województwie działki + średnia Polski. */
export function yieldReference(crop, voivodeship, centroid) {
  const qs = new URLSearchParams({ crop });
  if (voivodeship) qs.set('voivodeship', voivodeship);
  else if (Array.isArray(centroid)) { qs.set('lat', String(centroid[0])); qs.set('lon', String(centroid[1])); }
  const key = qs.toString();
  return remember(yieldCache, key, () => api(`/api/reference/yields?${key}`, { timeout: 30000 }));
}

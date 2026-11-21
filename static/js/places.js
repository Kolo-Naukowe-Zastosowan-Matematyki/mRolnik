// Działki w kilku odległych miejscach (np. pola w dwóch województwach): grupowanie w „miejsca”.
// Wspólne dla mapy na Głównej i mapy w Historii.

export const PLACE_GAP_KM = 15;   // działka dalej niż 15 km od wszystkich działek grupy zaczyna nowe miejsce

export function kmBetween(a, b) {
  const r = Math.PI / 180;
  const x = (b[1] - a[1]) * r * Math.cos(((a[0] + b[0]) / 2) * r);
  const y = (b[0] - a[0]) * r;
  return 6371 * Math.hypot(x, y);
}

/** Grupy elementów leżących blisko siebie (łączenie pojedyncze, odstęp ≤ gapKm). pos(x) → [lat, lon] albo null.
 *  Zwraca tablice elementów, od zachodu na wschód; elementy bez położenia trafiają do pierwszej grupy. */
export function groupByDistance(items, pos, gapKm = PLACE_GAP_KM) {
  const pts = [];
  const loose = [];
  items.forEach((x) => { const c = pos(x); if (Array.isArray(c) && Number.isFinite(c[0]) && Number.isFinite(c[1])) pts.push({ x, c }); else loose.push(x); });
  const parent = pts.map((_, k) => k);
  const find = (k) => (parent[k] === k ? k : (parent[k] = find(parent[k])));
  for (let a = 0; a < pts.length; a += 1) {
    for (let b = a + 1; b < pts.length; b += 1) {
      if (kmBetween(pts[a].c, pts[b].c) <= gapKm) parent[find(a)] = find(b);
    }
  }
  const byRoot = new Map();
  pts.forEach((pt, k) => {
    const r = find(k);
    if (!byRoot.has(r)) byRoot.set(r, { lon: 0, n: 0, items: [] });
    const g = byRoot.get(r);
    g.items.push(pt.x); g.lon += pt.c[1]; g.n += 1;
  });
  const out = Array.from(byRoot.values()).sort((a, b) => a.lon / a.n - b.lon / b.n).map((g) => g.items);
  if (loose.length) { if (out.length) out[0].push(...loose); else out.push(loose); }
  return out;
}

/** Nazwa miejsca: najczęstsza gmina działek (albo powiat). */
export function placeName(parcels, k = 0) {
  const top = (key) => {
    const m = new Map();
    parcels.forEach((p) => { const v = p && p[key]; if (v) m.set(v, (m.get(v) || 0) + 1); });
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]).map(([v]) => v)[0] || '';
  };
  const commune = top('commune');
  const county = top('county');
  return { name: commune || (county ? `pow. ${county}` : `Miejsce ${k + 1}`), county };
}

export function plDzialek(n) {
  return `${n} ${n === 1 ? 'działka' : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? 'działki' : 'działek')}`;
}

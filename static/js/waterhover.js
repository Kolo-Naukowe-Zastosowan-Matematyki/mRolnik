// Mapa wodna: po najechaniu (na telefonie po stuknięciu) podświetlenie całego obiektu i dymek z nazwą, rodzajem i ID MPHP.
// Wektory z /api/water/features (BDOT10k GUGiK, ten sam cache co filtr działek) pobierane stałymi kaflami (cache w
// przeglądarce i na serwerze), gdy włączona jest warstwa „Mapa wodna” (WMS) i zoom ≥ 13. Warstwa trafień jest
// niewidoczna – widać tylko linie WMS i podświetlenie.
import { h } from './dom.js';
import { api } from './api.js';

const MIN_ZOOM = 13;
const TILE_DLON = 0.05;     // kafel ok. 3,4 × 3,3 km (limit serwera: 8 × 8 km)
const TILE_DLAT = 0.03;
const MAX_TILES = 30;       // kafli na widok (zoom 13 na szerokim ekranie ≈ 20)
const PARALLEL = 4;
const KIND_NONAME = { SWRS_L: 'ciek bez nazwy', SWKN_L: 'kanał bez nazwy', SWRM_L: 'rów bez nazwy', PTWP_A: 'woda bez nazwy' };

let map = null;
let hitLayer = null;      // niewidoczne linie/poligony – strefa trafienia
let glowLayer = null;     // podświetlenie wskazanego obiektu
let renderer = null;
let hint = null;
let tip = null;
const tiles = new Map();  // "i_j@z" → { feats } | 'busy' | 'err'
const byKey = new Map();  // key obiektu → [feature] (części z różnych kafli)
let shownZ = null;
let gen = 0;
let pump = () => {};
const inflight = new Map();   // kafel → AbortController
let timer = null;
let hovered = null;
let pinned = false;       // dymek otwarty stuknięciem (telefon) – zamyka go stuknięcie gdzie indziej

function zBucket(z) { return z >= 17 ? 17 : z >= 15 ? 15 : 13; }

function waterOn() {
  let on = false;
  map.eachLayer((l) => { if (l.options && l.options.className === 'mr-water-wms') on = true; });
  return on;
}

function css(name, fallback) {
  const v = getComputedStyle(document.querySelector('.mr-app') || document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function initWaterHover(m) {
  if (!m || typeof L === 'undefined') return;
  map = m;
  const pane = map.createPane('mrWaterHit');
  pane.style.zIndex = '455';   // nad WMS cieków (450), pod pinezkami i dymkami
  renderer = L.canvas({ pane: 'mrWaterHit', padding: 0.2, tolerance: 6 });
  glowLayer = L.layerGroup({ pane: 'mrWaterHit' });
  hitLayer = L.geoJSON(null, {
    pane: 'mrWaterHit',
    renderer,
    bubblingMouseEvents: false,
    style: (f) => (f.geometry.type.includes('Polygon')
      ? { stroke: true, weight: 8, opacity: 0, fill: true, fillOpacity: 0 }
      : { weight: 10, opacity: 0, fill: false }),
    onEachFeature: (f, layer) => {
      layer.on('mouseover', (e) => { if (!pinned) show(f, e.latlng); });
      layer.on('mousemove', (e) => { if (!pinned && tip) tip.setLatLng(e.latlng); });
      layer.on('mouseout', () => { if (!pinned) hide(); });
      layer.on('click', (e) => { pinned = true; show(f, e.latlng); });
    },
  });
  hint = L.control({ position: 'topleft' });
  hint.onAdd = () => h('div', { class: 'mr-water-hint', role: 'status', text: 'Przybliż mapę, aby wskazać ciek lub rów' });
  map.on('click', () => { if (pinned) { pinned = false; hide(); } });
  map.on('layeradd layerremove', (e) => { if (e.layer && e.layer.options && e.layer.options.className === 'mr-water-wms') sync(); });
  map.on('moveend', () => { clearTimeout(timer); timer = setTimeout(sync, 250); });
  sync();
}

function sync() {
  if (!map) return;
  const on = waterOn();
  const z = map.getZoom();
  const hintOn = on && z < MIN_ZOOM;
  if (hintOn && !hint._map) hint.addTo(map);
  if (!hintOn && hint._map) hint.remove();
  if (!on || z < MIN_ZOOM) {
    if (map.hasLayer(hitLayer)) map.removeLayer(hitLayer);
    if (map.hasLayer(glowLayer)) map.removeLayer(glowLayer);
    hide();
    inflight.forEach((c, id) => { c.abort(); tiles.delete(id); });
    inflight.clear();
    return;
  }
  if (!map.hasLayer(hitLayer)) hitLayer.addTo(map);
  if (!map.hasLayer(glowLayer)) glowLayer.addTo(map);
  load(zBucket(z));
}

function tileIds(zb) {
  const b = map.getBounds();
  const i0 = Math.floor(b.getWest() / TILE_DLON);
  const i1 = Math.floor(b.getEast() / TILE_DLON);
  const j0 = Math.floor(b.getSouth() / TILE_DLAT);
  const j1 = Math.floor(b.getNorth() / TILE_DLAT);
  const c = map.getCenter();
  const out = [];
  for (let i = i0; i <= i1; i += 1) for (let j = j0; j <= j1; j += 1) out.push([i, j]);
  // najpierw kafle bliżej środka widoku
  out.sort((a, b2) => Math.hypot((a[0] + 0.5) * TILE_DLON - c.lng, (a[1] + 0.5) * TILE_DLAT - c.lat)
    - Math.hypot((b2[0] + 0.5) * TILE_DLON - c.lng, (b2[1] + 0.5) * TILE_DLAT - c.lat));
  return out.slice(0, MAX_TILES).map(([i, j]) => `${i}_${j}@${zb}`);
}

function load(zb) {
  if (shownZ !== zb) {          // inny poziom szczegółowości – rysujemy od nowa (kafle innych zoomów zostają w pamięci)
    hitLayer.clearLayers();
    byKey.clear();
    shownZ = zb;
    tiles.forEach((t, id) => { if (id.endsWith(`@${zb}`) && t && t.feats) add(t.feats); });
  }
  const ids = tileIds(zb);
  const want = new Set(ids);
  inflight.forEach((c, id) => { if (!want.has(id)) { c.abort(); inflight.delete(id); tiles.delete(id); } });   // poza widokiem – przerywamy
  const my = ++gen;
  const todo = ids.filter((id) => !tiles.has(id));
  const next = () => {
    while (inflight.size < PARALLEL && todo.length && my === gen) {
      const id = todo.shift();
      if (tiles.has(id)) continue;
      const [ij] = id.split('@');
      const [i, j] = ij.split('_').map(Number);
      const bbox = [i * TILE_DLON, j * TILE_DLAT, (i + 1) * TILE_DLON, (j + 1) * TILE_DLAT].map((v) => v.toFixed(5)).join(',');
      const c = new AbortController();
      tiles.set(id, 'busy');
      inflight.set(id, c);
      api(`/api/water/features?bbox=${bbox}&zoom=${zb}`, { timeout: 120000, signal: c.signal })
        .then((r) => {
          const feats = (r && r.features) || [];
          tiles.set(id, { feats });
          if (shownZ === zb) add(feats);
        })
        .catch(() => { if (tiles.get(id) === 'busy') tiles.delete(id); })
        .finally(() => { if (inflight.get(id) === c) inflight.delete(id); pump(); });   // zawsze najnowsza kolejka
    }
  };
  pump = next;
  next();
}

// BDOT10k dzieli ciek na wiele obiektów (odcinki między węzłami) – podświetlamy cały: po nazwie (ta sama klasa),
// a bez nazwy po identyfikatorze MPHP; obiekt bez obu – sam.
function group(p) {
  if (p.name) return `n:${p.cls}:${p.name}`;
  if (p.mphp) return `m:${p.cls}:${p.mphp}`;
  return p.key;
}

function add(feats) {
  const fresh = [];
  feats.forEach((f) => {
    const k = group(f.properties);
    const list = byKey.get(k) || [];
    list.push(f);
    byKey.set(k, list);
    fresh.push(f);
  });
  if (fresh.length) hitLayer.addData({ type: 'FeatureCollection', features: fresh });
}

function label(p) {
  return p.name || KIND_NONAME[p.cls] || 'ciek bez nazwy';
}

function content(p) {
  const kind = p.rodzaj ? `${p.kind} – ${p.rodzaj}` : p.kind;
  const rows = [h('strong', { class: 'mr-water-tip__name', text: label(p) }), h('span', { class: 'mr-water-tip__kind', text: kind })];
  if (p.mphp) rows.push(h('span', { text: `ID MPHP: ${p.mphp}` }));
  if (p.width_m) rows.push(h('span', { text: `szerokość wg BDOT10k: ${String(p.width_m).replace('.', ',')} m` }));
  rows.push(h('small', { class: 'mr-water-tip__src', text: `BDOT10k GUGiK${p.version ? `, wersja ${p.version}` : ''}` }));
  return h('div', { class: 'mr-water-tip', role: 'tooltip' }, rows);
}

function show(f, latlng) {
  const p = f.properties;
  const g = group(p);
  if (hovered !== g) {
    hovered = g;
    glowLayer.clearLayers();
    const parts = byKey.get(g) || [f];
    const accent = css('--mr-accent', '#d4213d');
    const fc = { type: 'FeatureCollection', features: parts };
    const poly = f.geometry.type.includes('Polygon');
    L.geoJSON(fc, { pane: 'mrWaterHit', renderer, interactive: false,
      style: { color: '#ffffff', weight: 8, opacity: 0.9, fill: false } }).addTo(glowLayer);
    L.geoJSON(fc, { pane: 'mrWaterHit', renderer, interactive: false,
      style: { color: accent, weight: 4, opacity: 1, fill: poly, fillColor: accent, fillOpacity: poly ? 0.25 : 0 } }).addTo(glowLayer);
  }
  if (!tip) tip = L.tooltip({ direction: 'auto', offset: [12, 0], className: 'mr-water-tooltip', opacity: 1 });
  tip.setLatLng(latlng).setContent(content(p));
  if (!map.hasLayer(tip)) tip.openOn(map);
}

function hide() {
  hovered = null;
  if (glowLayer) glowLayer.clearLayers();
  if (tip && map && map.hasLayer(tip)) map.closeTooltip(tip);
}

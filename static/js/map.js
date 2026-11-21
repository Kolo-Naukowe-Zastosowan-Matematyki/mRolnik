// Mapa (Leaflet z static/vendor). Podkłady: ortofotomapa GUGiK (WMS) i OpenStreetMap.
import { h, icon } from './dom.js';
import { num } from './format.js';
import { inPoland } from './csv.js';
import { emit } from './state.js';
import { indexHelp } from './indices.js';
import { groupByDistance, placeName, plDzialek } from './places.js';
import { api } from './api.js';

const ORTO_WMS = 'https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution';
const OSM_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
// Wymaga dopisania integracja.gugik.gov.pl do img-src w CSP – domyślnie wyłączone (data-cadastre-wms="1").
const KIEG_WMS = 'https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow';
// Cieki, kanały i rowy z państwowej bazy BDOT10k (GUGiK) – kompozycja kartograficzna BDOT10k (ta sama usługa, którą
// sprawdza backend w src/hydromelior/hydrography.py); host mapy.geoportal.gov.pl jest już w CSP.
// UWAGA: usługa nie obsługuje EPSG:3857 (tylko 4326/2180/2176–2179), dlatego warstwa jest pobierana w EPSG:4326.
// (Wcześniejszy adres …/PZGIK/BDOT/WMS/PobieranieBDOT10k to skorowidz do pobierania paczek – nie ma w nim rowów.)
// Adres i warstwy można nadpisać atrybutami data-water-wms / data-water-wms-layers na #mrolnik-root.
const WATER_WMS = 'https://mapy.geoportal.gov.pl/wss/service/pub/guest/kompozycja_BDOT10k_WMS/MapServer/WMSServer';
// woda powierzchniowa, rzeki/strumienie (też okresowe), kanały, rowy melioracyjne, budowle hydrotechniczne, przepusty, nazwy
const WATER_LAYERS = 'WPow,rzOk,rz,kanOkr,kan,row,BHydL,Prze,rzEt,kanEt,RowEt';
const WATER_MIN_ZOOM = 11;
const ORTHO_MIN_ZOOM = 11;
// Działki w kilku odległych miejscach (np. pola w dwóch województwach) są grupowane w „miejsca” (places.js).
const FAR_ZOOM = 12;            // poniżej tego zoomu działek nie widać – zamiast nich znaczniki miejsc

let map = null;
let parcelLayer = null;
let ndviLayer = null;
let ndmiLayer = null;      // NDMI (Sentinel-2) – woda w roślinach
let ndwiLayer = null;      // NDWI (Sentinel-2) – woda stojąca na powierzchni
let photoLayer = null;     // zdjęcie Sentinel-2 w barwach naturalnych (nieużywane na mapie: „Samo zdjęcie” = ortofotomapa)
let baseOrtho = null, baseOsm = null;
let networkLayer = null;
let layersControl = null;
let legend = null;
let ndviLegend = null; // paleta z API (layers.ndvi.legend), gdy dostępna
let addMode = false;
let pin = null;        // pinezka postawiona kliknięciem w mapę (przeciągalna)
let waterLayer = null;  // cieki i rowy (BDOT10k WMS)
let groupLayer = null;  // znaczniki miejsc (grup działek) widoczne przy oddalonej mapie
let groupCtl = null;    // przyciski „Działki w N miejscach” nad mapą
let groups = [];
let metric = 'ndvi';    // co pokazuje mapa: ndvi | ndmi | ndwi | photo (te same opcje co w Historii)
let lastParcels = [];
let lastSelected = 0;
let parcelFilter = null;  // „Filtruj działki”: funkcja (działka z analizy) → bool; null = pokazuj wszystkie

/** Filtr działek na mapie (parcelsearch.js). Ukryte działki nie mają kształtu ani nakładek; „Pokaż wszystkie” je pomija. */
export function setParcelFilter(fn) {
  parcelFilter = typeof fn === 'function' ? fn : null;
  if (map) renderParcels(lastParcels, lastSelected, true);
}
const barButtons = new Map();
let waterBtn = null;
const shapes = [];

export function getMap() { return map; }

/** Podkład ortofotomapy GUGiK (WMS) – także dla mapy w zakładce Historia. */
export function orthoLayer() {
  return window.L.tileLayer.wms(ORTO_WMS, {
    layers: 'Raster', format: 'image/jpeg', transparent: false, maxZoom: 19,
    attribution: 'Ortofotomapa © <a href="https://www.geoportal.gov.pl/" target="_blank" rel="noopener noreferrer">GUGiK</a>',
  });
}

export function initMap(container, opts = {}) {
  const L = window.L;
  if (!L) throw new Error('Nie wczytano biblioteki mapy (Leaflet).');
  map = L.map(container, { center: [52.1, 19.4], zoom: 6, zoomControl: false, attributionControl: true, keyboard: true });
  L.control.zoom({ position: 'topright', zoomInTitle: 'Przybliż', zoomOutTitle: 'Oddal' }).addTo(map);
  // „Pokaż wszystkie działki” – powrót do widoku wycentrowanego na całym gospodarstwie (pod przyciskami powiększenia)
  const fit = L.control({ position: 'topright' });
  fit.onAdd = () => {
    const box = h('div', { class: 'leaflet-bar leaflet-control mr-fitctl' }, [
      h('button', { type: 'button', class: 'mr-fitctl__btn', title: 'Pokaż wszystkie działki', 'aria-label': 'Pokaż wszystkie działki',
        onclick: (e) => { e.stopPropagation(); fitToParcels(null, { all: true }); } }, icon('fitAll')),
    ]);
    L.DomEvent.disableClickPropagation(box);
    return box;
  };
  fit.addTo(map);
  map.attributionControl.setPrefix('<a href="https://leafletjs.com/" target="_blank" rel="noopener noreferrer">Leaflet</a>');

  // Ortofotomapa tylko od zoomu 11: przy widoku całej Polski usługa GUGiK zwraca ciemną mozaikę z czarnymi rogami
  // (np. gdy działki leżą w dwóch odległych miejscach). Na dalszych zoomach pod spodem jest zwykła mapa OSM.
  const ortho = L.tileLayer.wms(ORTO_WMS, {
    layers: 'Raster', format: 'image/jpeg', transparent: false, maxZoom: 19, minZoom: ORTHO_MIN_ZOOM, attribution: ORTHO_ATTR,
  });
  const osmAttr = '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">autorzy OpenStreetMap</a>';
  const osm = L.tileLayer(OSM_URL, { maxZoom: 19, attribution: osmAttr });
  const osmFar = L.tileLayer(OSM_URL, { maxZoom: ORTHO_MIN_ZOOM - 1, attribution: osmAttr, zIndex: 0 });
  const base = { 'Zdjęcia lotnicze (GUGiK)': ortho, 'Mapa (OpenStreetMap)': osm };
  (opts.base === 'osm' ? osm : ortho).addTo(map);
  osmFar.addTo(map);
  baseOrtho = ortho;
  baseOsm = osm;

  parcelLayer = L.featureGroup().addTo(map);
  groupLayer = L.layerGroup();
  map.on('zoomend', syncFar);
  map.on('moveend', syncOrthoDate);
  map.on('baselayerchange', syncOrthoDate);
  ndviLayer = L.layerGroup().addTo(map);
  ndmiLayer = L.layerGroup();
  ndwiLayer = L.layerGroup();
  photoLayer = L.layerGroup();
  networkLayer = L.layerGroup();
  const overlays = {
    'Granice działek z analizy': parcelLayer,
    'Kondycja roślin (NDVI)': ndviLayer,
    'Sieć melioracyjna (DEMO)': networkLayer,
  };
  // własny panel nad nakładką NDVI (overlayPane = 400), pod pinezkami i etykietami; nie przechwytuje kliknięć
  const waterPane = map.createPane('mrWater');
  waterPane.style.zIndex = '450';
  waterPane.style.pointerEvents = 'none';
  waterLayer = L.tileLayer.wms(opts.waterWms || WATER_WMS, {
    pane: 'mrWater',
    layers: opts.waterLayers || WATER_LAYERS, format: 'image/png', transparent: true, version: '1.3.0',
    crs: L.CRS.EPSG4326, maxZoom: 19, minZoom: WATER_MIN_ZOOM, className: 'mr-water-wms', zIndex: 5,
    attribution: 'Cieki i rowy © GUGiK (BDOT10k)',
  });
  waterLayer.on('tileerror', () => emit('map:hint', 'Warstwa cieków i rowów (GUGiK) chwilowo nie odpowiada.'));
  overlays['Cieki i rowy (GUGiK BDOT10k)'] = waterLayer;
  if (opts.cadastre) {
    overlays['Działki ewidencyjne (GUGiK)'] = L.tileLayer.wms(KIEG_WMS, {
      layers: 'dzialki,numery_dzialek', format: 'image/png', transparent: true, maxZoom: 20, minZoom: 14,
      attribution: 'Ewidencja gruntów © GUGiK',
    });
  }
  layersControl = L.control.layers(base, overlays, { position: 'topright', collapsed: true }).addTo(map);
  const toggle = container.querySelector('.leaflet-control-layers-toggle');
  if (toggle) {
    toggle.setAttribute('aria-label', 'Warstwy mapy');
    toggle.setAttribute('title', 'Warstwy mapy');
  }

  legend = L.control({ position: 'bottomleft' });
  legend.onAdd = () => buildLegend();
  // przełącznik warstw Leaflet i pasek metryk nad mapą muszą pokazywać to samo
  map.on('overlayadd', (e) => {
    if (e.layer === ndviLayer && metric !== 'ndvi') setMetric('ndvi');
    if (e.layer === waterLayer) syncWaterBtn();
  });
  map.on('overlayremove', (e) => {
    if (e.layer === ndviLayer && metric === 'ndvi') setMetric('photo');
    if (e.layer === waterLayer) syncWaterBtn();
  });

  // Kliknięcie w mapę zawsze stawia pinezkę. W trybie „Wskaż pole na mapie” pole jest dodawane od razu,
  // poza nim pinezka ma dymek z przyciskiem „Dodaj pole w tym miejscu” (można ją wcześniej przeciągnąć).
  map.on('click', (e) => {
    const { lat, lng } = e.latlng;
    if (!inPoland(lat, lng)) {
      emit('map:hint', 'Ten punkt leży poza granicami Polski – wybierz miejsce w kraju.');
      if (!addMode) window.L.popup().setLatLng(e.latlng).setContent(h('p', { text: 'Ten punkt leży poza granicami Polski – wybierz miejsce w kraju.' })).openOn(map);
      return;
    }
    if (addMode) {
      placePin(lat, lng, false);
      emit('map:add', round6(lat, lng));
    } else {
      placePin(lat, lng, true);
    }
  });
  window.addEventListener('resize', () => map.invalidateSize());
  return map;
}

function round6(lat, lng) {
  return { lat: Math.round(lat * 1e6) / 1e6, lon: Math.round(lng * 1e6) / 1e6 };
}

function pinIcon() {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 32 44');
  svg.setAttribute('width', '32');
  svg.setAttribute('height', '44');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(NS, 'path');
  path.setAttribute('d', 'M16 1C7.7 1 1 7.6 1 15.8 1 27 16 43 16 43s15-16 15-27.2C31 7.6 24.3 1 16 1z');
  path.setAttribute('class', 'mr-pin__body');
  const dot = document.createElementNS(NS, 'circle');
  dot.setAttribute('cx', '16'); dot.setAttribute('cy', '15.5'); dot.setAttribute('r', '5.5');
  dot.setAttribute('class', 'mr-pin__dot');
  svg.append(path, dot);
  const el = h('div', { class: 'mr-pin' });
  el.appendChild(svg);
  return window.L.divIcon({ className: 'mr-pin-wrap', html: el, iconSize: [32, 44], iconAnchor: [16, 43], popupAnchor: [0, -40] });
}

/** Podpowiedzi upraw w formularzach (dodawanie pola, „Edytuj pole” na pinezce). */
export const CROP_SUGGESTIONS = ['Pszenica ozima', 'Pszenica jara', 'Jęczmień jary', 'Jęczmień ozimy', 'Żyto', 'Pszenżyto', 'Rzepak ozimy', 'Kukurydza', 'Burak cukrowy', 'Ziemniak', 'Trwały użytek zielony'];
let popupSeq = 0;

/** Czy punkt leży wewnątrz wielokąta [[lat, lon], …] (promień w prawo). */
function inside(lat, lng, poly) {
  let res = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [ya, xa] = poly[i];
    const [yb, xb] = poly[j];
    if ((ya > lat) !== (yb > lat) && lng < ((xb - xa) * (lat - ya)) / (yb - ya) + xa) res = !res;
  }
  return res;
}

/** Indeks narysowanej działki, w której granicach stoi pinezka (albo -1). */
function parcelAt(lat, lng) {
  return lastParcels.findIndex((p) => {
    const ll = latlngs(p && p.geojson_coords);
    return ll.length >= 3 && inside(lat, lng, ll);
  });
}

const parcelAtCache = new Map();
function parcelAtPoint(lat, lng) {
  const k = `${lat.toFixed(6)},${lng.toFixed(6)}`;
  if (!parcelAtCache.has(k)) {
    const pr = api(`/api/parcel-at?lat=${lat.toFixed(6)}&lon=${lng.toFixed(6)}`, { timeout: 25000 });
    pr.catch(() => parcelAtCache.delete(k));
    parcelAtCache.set(k, pr);
  }
  return parcelAtCache.get(k);
}

function pinPopup(lat, lng) {
  const box = h('div', { class: 'mr-popup mr-popup--pin' });
  const at = parcelAt(lat, lng);
  const hit = at >= 0 ? lastParcels[at] : null;
  const hitName = hit ? (hit.id_tag || `Działka ${at + 1}`) : '';

  // działka ewidencyjna pod pinezką (ULDK): numer i powierzchnia; po przeciągnięciu dymek powstaje od nowa
  const parcelInfo = h('p', { class: 'mr-popup__parcel', role: 'status', text: 'Sprawdzam działkę…' });
  parcelAtPoint(lat, lng).then((r) => {
    const p = r && r.parcel;
    parcelInfo.textContent = p
      ? `Działka ${p.parcel_id} · ${Number(p.area_ha).toFixed(2).replace(".", ",")} ha${p.commune ? ` · gm. ${p.commune}` : ''}`
      : 'Brak działki w ewidencji w tym miejscu';
    parcelInfo.title = p ? 'Źródło: GUGiK ULDK; powierzchnia z granic działki' : '';
    if (pin && pin.getPopup()) pin.getPopup().update();
  }).catch(() => { parcelInfo.textContent = 'Nie udało się sprawdzić działki (ULDK nie odpowiada).'; });

  const showMenu = () => {
    box.replaceChildren(
      h('p', { class: 'mr-popup__title', text: hit ? `Pinezka na polu: ${hitName}` : 'Nowe pole w tym miejscu?' }),
      h('p', { class: 'mr-popup__coords', text: `${lat.toFixed(5)}° N, ${lng.toFixed(5)}° E` }),
      parcelInfo,
      h('p', { class: 'mr-hint', text: hit ? 'Możesz zmienić nazwę i uprawę tego pola albo dodać w tym miejscu nowe.' : 'Przeciągnij pinezkę, aby poprawić położenie. Granice działki pobierzemy z ewidencji GUGiK.' }),
      h('div', { class: 'mr-popup__actions' }, [
        h('button', { type: 'button', class: 'mr-btn mr-btn--primary', onclick: () => {
          const ll = pin.getLatLng();
          emit('map:add', round6(ll.lat, ll.lng));
          if (pin) pin.closePopup();
        } }, 'Dodaj pole w tym miejscu'),
        h('button', { type: 'button', class: 'mr-btn mr-btn--secondary', onclick: (e) => { e.stopPropagation(); showForm(); } }, [icon('edit'), 'Edytuj pole']),
        h('button', { type: 'button', class: 'mr-btn mr-btn--secondary', onclick: (e) => { e.preventDefault(); e.stopPropagation(); clearPin(); } }, 'Usuń pinezkę'),
      ]),
    );
    if (pin) pin.getPopup() && pin.getPopup().update();
  };

  // Kliknięty przycisk znika z DOM przy podmianie treści dymka – bez stopPropagation Leaflet uznałby to za klik w mapę.
  // „Edytuj pole”: pinezka na narysowanym polu → zmiana nazwy i uprawy tego pola;
  // poza polami → szczegóły nowego pola (nazwa, uprawa, dokładne współrzędne) przed dodaniem.
  function showForm() {
    const id = `mr-pinf${++popupSeq}`;
    const err = h('p', { class: 'mr-error', role: 'alert', hidden: true });
    const name = h('input', { id: `${id}-name`, class: 'mr-input', maxlength: '60', autocomplete: 'off', value: hit ? hitName : '' });
    const crop = h('input', { id: `${id}-crop`, class: 'mr-input', maxlength: '60', autocomplete: 'off', list: `${id}-crops`, value: (hit && hit.crop) || '' });
    const crops = h('datalist', { id: `${id}-crops` }, CROP_SUGGESTIONS.map((c) => h('option', { value: c })));
    const la = h('input', { id: `${id}-lat`, class: 'mr-input', inputmode: 'decimal', autocomplete: 'off', value: lat.toFixed(6) });
    const lo = h('input', { id: `${id}-lon`, class: 'mr-input', inputmode: 'decimal', autocomplete: 'off', value: lng.toFixed(6) });
    const row = (input, label) => h('div', { class: 'mr-field' }, [h('label', { class: 'mr-label', for: input.id, text: label }), input]);
    const submit = (e) => {
      e.preventDefault();
      const tag = name.value.trim();
      const cropV = crop.value.trim();
      if (hit) {
        emit('parcel:edit', { index: at, id_tag: tag || hitName, crop: cropV });
        if (pin) clearPin();
        return;
      }
      const y = Number(la.value.replace(',', '.'));
      const x = Number(lo.value.replace(',', '.'));
      if (!Number.isFinite(y) || !Number.isFinite(x) || !inPoland(y, x)) {
        err.textContent = 'Współrzędne muszą leżeć w Polsce (szer. 49–55, dł. 14–24,2).';
        err.hidden = false;
        la.setAttribute('aria-invalid', 'true');
        la.focus();
        return;
      }
      if (pin) pin.setLatLng([y, x]);
      emit('map:add', { ...round6(y, x), id_tag: tag || undefined, crop: cropV || undefined });
      if (pin) pin.closePopup();
    };
    box.replaceChildren(h('form', { class: 'mr-pinform', novalidate: true, onsubmit: submit, onkeydown: (e) => e.stopPropagation() }, [
      h('p', { class: 'mr-popup__title', text: hit ? `Edytuj pole: ${hitName}` : 'Szczegóły nowego pola' }),
      err,
      row(name, 'Nazwa pola'),
      row(crop, 'Uprawa'),
      crops,
      hit ? h('p', { class: 'mr-hint', text: 'Zmiana uprawy przelicza zalecenia dla tego pola.' })
        : h('div', { class: 'mr-row2' }, [row(la, 'Szerokość (N)'), row(lo, 'Długość (E)')]),
      h('div', { class: 'mr-popup__actions' }, [
        h('button', { type: 'submit', class: 'mr-btn mr-btn--primary' }, hit ? 'Zapisz zmiany' : 'Dodaj pole'),
        h('button', { type: 'button', class: 'mr-btn mr-btn--secondary', onclick: (e) => { e.stopPropagation(); showMenu(); } }, 'Wróć'),
      ]),
    ]));
    if (pin && pin.getPopup()) pin.getPopup().update();
    name.focus();
  }

  showMenu();
  return { box };
}

/** Stawia (albo przenosi) pinezkę. withPopup – pokaż dymek z przyciskiem dodania pola. */
export function placePin(lat, lng, withPopup) {
  const L = window.L;
  if (!map) return;
  if (!pin) {
    pin = L.marker([lat, lng], { icon: pinIcon(), draggable: true, keyboard: true, title: 'Pinezka nowego pola', alt: 'Pinezka nowego pola', riseOnHover: true }).addTo(map);
    pin.on('dragend', () => {
      const ll = pin.getLatLng();
      const pp = pinPopup(ll.lat, ll.lng);
      pin.bindPopup(pp.box, { minWidth: 240, className: 'mr-leaflet-popup' }).openPopup();
    });
  } else {
    pin.setLatLng([lat, lng]);
  }
  pin.unbindPopup();
  if (withPopup) {
    const pp = pinPopup(lat, lng);
    pin.bindPopup(pp.box, { minWidth: 240, className: 'mr-leaflet-popup' }).openPopup();
  } else {
    pin.bindTooltip('Dodawanie pola…', { direction: 'top', offset: [0, -40], className: 'mr-tooltip' }).openTooltip();
  }
}

export function clearPin() {
  // najpierw dymek i podpowiedź (bez animacji zanikania), potem sama pinezka – wszystko od razu
  if (pin) {
    const pop = pin.getPopup && pin.getPopup();
    if (pop && map) map.removeLayer(pop);
    pin.unbindPopup();
    pin.unbindTooltip();
    if (map) map.removeLayer(pin);
  }
  pin = null;
}

let ndmiLegend = null;   // paleta NDMI z API (layers.ndmi.legend)
let ndwiLegend = null;   // paleta NDWI z API (layers.ndwi.legend)

function legendBox(title, list, note, help = null) {
  const wide = window.matchMedia('(min-width: 600px)').matches;
  const box = h('details', { class: 'mr-legend', open: wide }, [
    h('summary', { class: 'mr-legend__title', text: title }),
    list,
    note ? h('p', { class: 'mr-legend__note', text: note }) : null,
    help ? indexHelp(help, { compact: true }) : null,
  ]);
  window.L.DomEvent.disableClickPropagation(box);
  return box;
}

function paletteList(lg, digits) {
  return h('ul', null, (Array.isArray(lg) ? lg : []).map((b) => {
    const sw = h('span', { class: 'mr-swatch', 'aria-hidden': 'true' });
    if (/^#[0-9a-f]{6}$/i.test(String(b.color))) sw.style.background = b.color;
    return h('li', null, [sw, `${b.label} (${Number(b.from).toFixed(digits)}–${Number(b.to).toFixed(digits)})`]);
  }));
}

// Wartość wskaźnika dla narysowanych działek: średnia ważona powierzchnią; min–maks tylko dla NDVI (liczone na serwerze
// z pikseli działki); data zdjęcia wprost („15.11.2026”, a przy różnych datach zakres).
function legendStats(m) {
  let w = 0;
  let acc = 0;
  let lo = null;
  let hi = null;
  const dates = [];
  lastParcels.forEach((p) => {
    if (!p) return;
    const src = m === 'ndvi' ? p.ndvi : (p.layers || {})[m];
    if (!src || src.mean == null) return;
    const a = Number(p.area_ha) > 0 ? Number(p.area_ha) : 1;
    w += a;
    acc += src.mean * a;
    if (m === 'ndvi') {
      if (src.min != null) lo = lo == null ? src.min : Math.min(lo, src.min);
      if (src.max != null) hi = hi == null ? src.max : Math.max(hi, src.max);
    }
    const d = src.acquired_on || (m === 'ndvi' && p.layers && p.layers.ndvi && p.layers.ndvi.acquired_on);
    if (d) dates.push(String(d).slice(0, 10));
  });
  if (!w) return null;
  const f = (v) => num(v, 2);
  const ds = [...new Set(dates)].sort();
  const dd = (x) => x.split('-').reverse().join('.');
  let when = null;
  if (ds.length === 1) when = `Zdjęcie Sentinel-2 z ${dd(ds[0])}`;
  else if (ds.length > 1) when = `zdjęcia Sentinel-2 z ${dd(ds[0])} – ${dd(ds[ds.length - 1])}`;
  // skrajne piksele ±1 (brzegi, pojedyncze piksele bez sygnału) to nie zakres łanu – wtedy tylko średnia
  const range = lo != null && hi != null && lo > -0.99 && hi < 0.99;
  return [`${m.toUpperCase()}: średnio ${f(acc / w)}` + (range ? ` (min ${f(lo)} – maks ${f(hi)})` : ''), when].filter(Boolean).join(' · ');
}

function buildLegend() {
  if (metric === 'ndmi') {
    return legendBox('Legenda: woda w roślinach (NDMI, Sentinel-2)', paletteList(ndmiLegend, 1), legendStats('ndmi'), 'ndmi');
  }
  if (metric === 'ndwi') {
    return legendBox('Legenda: woda na polu (NDWI, Sentinel-2)', paletteList(ndwiLegend, 1), legendStats('ndwi'), 'ndwi');
  }
  if (metric === 'photo') {
    return legendBox('Ortofotomapa GUGiK', null, null, 'ortho');
  }
  const wide = window.matchMedia('(min-width: 600px)').matches;
  let list;
  if (Array.isArray(ndviLegend) && ndviLegend.length) {
    list = h('ul', null, ndviLegend.map((b) => {
      const sw = h('span', { class: 'mr-swatch', 'aria-hidden': 'true' });
      if (/^#[0-9a-f]{6}$/i.test(String(b.color))) sw.style.background = b.color;
      return h('li', null, [sw, `${b.label} (${Number(b.from).toFixed(2)}–${Number(b.to).toFixed(2)})`]);
    }));
  } else {
    const items = [
      ['bare', 'Gleba odkryta lub resztki'],
      ['weak', 'Rośliny słabe, w stresie'],
      ['mid', 'Kondycja umiarkowana'],
      ['good', 'Kondycja dobra'],
    ];
    list = h('ul', null, items.map(([k, t]) => h('li', null, [h('span', { class: `mr-swatch mr-swatch--${k}`, 'aria-hidden': 'true' }), t])));
  }
  const box = h('details', { class: 'mr-legend', open: wide }, [
    h('summary', { class: 'mr-legend__title', text: 'Legenda: kondycja roślin (NDVI, Sentinel-2)' }),
    list,
    legendStats('ndvi') ? h('p', { class: 'mr-legend__note', text: legendStats('ndvi') }) : null,
    indexHelp('ndvi', { compact: true }),
  ]);
  window.L.DomEvent.disableClickPropagation(box);
  return box;
}

export function setAddMode(on) {
  addMode = on;
  if (map) map.getContainer().classList.toggle('mr-map--adding', on);
}
export function isAddMode() { return addMode; }

function latlngs(coords) {
  return (coords || []).filter((c) => Array.isArray(c) && c.length >= 2).map(([lon, lat]) => [lat, lon]);
}

/** Rysuje działki z odpowiedzi analizy. Status: kolor + wzór linii + etykieta tekstowa. */
// Etykiety na mapie: przy kilku działkach widać wszystkie; przy większym gospodarstwie (np. kilkanaście działek)
// stała etykieta tylko przy wybranej działce, pozostałe po najechaniu – inaczej etykiety zasłaniają się nawzajem.
const MAX_PERMANENT_LABELS = 6;
const labels = [];

function bindLabel(i, selected) {
  const s = shapes[i];
  if (!s || !labels[i]) return;
  const permanent = lastParcels.length <= MAX_PERMANENT_LABELS || i === selected;
  s.unbindTooltip();
  s.bindTooltip(labels[i], { permanent, direction: 'top', className: 'mr-tooltip', offset: [0, -6] });
  if (permanent && s._map) s.openTooltip();
}

export function renderParcels(parcels, selected, keepPin = false) {
  const L = window.L;
  if (!map) return;
  lastParcels = parcels || [];
  lastSelected = selected;
  if (!keepPin) clearPin();   // po analizie pole jest już narysowane z granic ewidencyjnych
  parcelLayer.clearLayers();
  ndviLayer.clearLayers();
  ndmiLayer.clearLayers();
  ndwiLayer.clearLayers();
  photoLayer.clearLayers();
  const lgm = (parcels || []).map((p) => p && p.layers && p.layers.ndmi && p.layers.ndmi.legend).find(Boolean);
  if (lgm) ndmiLegend = lgm;
  const lgw = (parcels || []).map((p) => p && p.layers && p.layers.ndwi && p.layers.ndwi.legend).find(Boolean);
  if (lgw) ndwiLegend = lgw;
  const lg = (parcels || []).map((p) => p && p.layers && p.layers.ndvi && p.layers.ndvi.legend).find(Boolean);
  if (lg && lg !== ndviLegend) { ndviLegend = lg; if (legend) { legend.remove(); } }
  shapes.length = 0;
  labels.length = 0;
  (parcels || []).forEach((p, i) => {
    if (parcelFilter && !parcelFilter(p)) return;
    const sel = i === selected ? ' mr-parcel--selected' : '';
    let cls = `mr-parcel mr-parcel--none${sel}`;
    let fillOpacity = 0.3;
    let second = [p.crop, p.area_ha ? `${num(p.area_ha, 2)} ha` : null].filter(Boolean).join(' · ') || 'działka';
    if (metric === 'ndvi' || metric === 'ndmi' || metric === 'ndwi' || metric === 'photo') {
      cls = `mr-parcel mr-parcel--outline${sel}`;
      fillOpacity = 0;
      const lay = p.layers || {};
      if (metric === 'ndvi') second = p.ndvi && p.ndvi.mean !== null && p.ndvi.mean !== undefined ? `NDVI ${num(p.ndvi.mean, 2)}` : 'NDVI: brak danych';
      else if (metric === 'ndmi') second = lay.ndmi && lay.ndmi.mean !== null && lay.ndmi.mean !== undefined ? `NDMI ${num(lay.ndmi.mean, 2)}` : 'NDMI: brak zdjęcia';
      else if (metric === 'ndwi') {
        const w = lay.ndwi;
        second = !w || w.mean === null || w.mean === undefined ? 'NDWI: brak zdjęcia'
          : Number(w.water_pct) >= 1 ? `woda stojąca: ${num(w.water_pct, 0)}% działki` : `NDWI ${num(w.mean, 2)} – bez wody stojącej`;
      }
      else second = [p.crop, p.area_ha ? `${num(p.area_ha, 2)} ha` : null, lay.true_color && lay.true_color.acquired_on ? `zdjęcie ${lay.true_color.acquired_on}` : null].filter(Boolean).join(' · ') || 'działka';
    }
    const ll = latlngs(p.geojson_coords);
    let shape;
    if (ll.length >= 3) {
      shape = L.polygon(ll, { className: cls, weight: 3, fillOpacity, bubblingMouseEvents: false });
    } else if (Array.isArray(p.centroid)) {
      shape = L.circleMarker(p.centroid, { className: cls + ' mr-parcel--point', radius: 10, weight: 3, fillOpacity: 0.3, bubblingMouseEvents: false });
    } else {
      return;
    }
    const label = h('span', { class: 'mr-maplabel' }, [
      h('strong', { text: p.id_tag || `Działka ${i + 1}` }),
      h('span', { text: ll.length >= 3 ? second : 'brak granic działki' }),
    ]);
    labels[i] = label;
    shape.on('click', () => { if (!addMode) emit('parcel:select', i); });
    shape.addTo(parcelLayer);
    shapes[i] = shape;
    bindLabel(i, selected);

    const okImg = (x) => x && typeof x.image === 'string' && /^data:image\/(png|jpeg|webp);base64,/.test(x.image) && Array.isArray(x.bounds);
    const lay = p.layers || {};
    if (okImg(lay.ndvi)) L.imageOverlay(lay.ndvi.image, lay.ndvi.bounds, { opacity: 0.75, alt: `Mapa kondycji roślin (NDVI) dla ${p.id_tag || 'działki'}`, className: 'mr-ndvi' }).addTo(ndviLayer);
    if (okImg(lay.ndmi)) L.imageOverlay(lay.ndmi.image, lay.ndmi.bounds, { opacity: 0.8, alt: `Mapa wody w roślinach (NDMI) dla ${p.id_tag || 'działki'}`, className: 'mr-ndvi' }).addTo(ndmiLayer);
    if (okImg(lay.ndwi)) L.imageOverlay(lay.ndwi.image, lay.ndwi.bounds, { opacity: 0.8, alt: `Mapa wody na powierzchni (NDWI) dla ${p.id_tag || 'działki'}`, className: 'mr-ndvi' }).addTo(ndwiLayer);
    if (okImg(lay.true_color)) L.imageOverlay(lay.true_color.image, lay.true_color.bounds, { opacity: 1, alt: `Zdjęcie Sentinel-2 działki ${p.id_tag || ''}`, className: 'mr-ndvi' }).addTo(photoLayer);
  });
  buildGroups(parcels || []);
  legend.remove();
  legend.onAdd = () => buildLegend();
  if (metric === 'risk' || (metric === 'ndmi' && ndmiLayer.getLayers().length) || (metric === 'ndwi' && ndwiLayer.getLayers().length) || metric === 'photo'
      || (metric === 'ndvi' && ndviLayer.getLayers().length && map.hasLayer(ndviLayer))) legend.addTo(map);
}

// --- pasek metryk nad mapą ---------------------------------------------------------------------------
const METRICS = [
  ['ndvi', 'leaf', 'Kondycja roślin (NDVI)', 'NDVI z satelity Sentinel-2: ile zielonej, żywej masy roślin jest na działce'],
  ['ndmi', 'drop', 'Woda w roślinach (NDMI)', 'NDMI z satelity Sentinel-2: ile wody jest w liściach – stres wodny roślin (nie wilgotność gleby w %)'],
  ['ndwi', 'waves', 'Woda na polu (NDWI)', 'NDWI z satelity Sentinel-2: woda stojąca na powierzchni – zastoiska i podtopienia'],
  ['photo', 'photo', 'Samo zdjęcie', 'Sama ortofotomapa GUGiK (zdjęcia lotnicze), bez nakładek ze wskaźnikami'],
];

export function getMetric() { return metric; }

/** Zmienia metrykę pokazywaną na mapie (nakładka, kolory działek, legenda, stan przycisków). */
export function setMetric(m) {
  if (!METRICS.some(([k]) => k === m)) m = 'ndvi';
  metric = m;
  if (map) {
    if (m === 'ndvi') { if (!map.hasLayer(ndviLayer)) ndviLayer.addTo(map); } else if (map.hasLayer(ndviLayer)) map.removeLayer(ndviLayer);
    if (m === 'ndmi') { if (!map.hasLayer(ndmiLayer)) ndmiLayer.addTo(map); } else if (map.hasLayer(ndmiLayer)) map.removeLayer(ndmiLayer);
    if (m === 'ndwi') { if (!map.hasLayer(ndwiLayer)) ndwiLayer.addTo(map); } else if (map.hasLayer(ndwiLayer)) map.removeLayer(ndwiLayer);
    if (map.hasLayer(photoLayer)) map.removeLayer(photoLayer);
    if (m === 'photo' && baseOrtho && !map.hasLayer(baseOrtho)) {   // „Samo zdjęcie” = zawsze ortofotomapa GUGiK
      if (baseOsm && map.hasLayer(baseOsm)) map.removeLayer(baseOsm);
      baseOrtho.addTo(map);
      baseOrtho.bringToBack();
    }
    renderParcels(lastParcels, lastSelected, true);
  }
  barButtons.forEach((b, k) => b.setAttribute('aria-pressed', String(k === m)));
  emit('map:metric', m);
}

function syncWaterBtn() {
  const on = isWaterOn();
  if (waterBtn) waterBtn.setAttribute('aria-pressed', String(on));
  emit('map:water', on);
}

export function isWaterOn() { return !!(map && waterLayer && map.hasLayer(waterLayer)); }

/** Włącza/wyłącza warstwę cieków i rowów (pasek nad mapą, przełącznik warstw i przycisk w zakładce Woda). */
export function setWaterLayer(on) {
  if (!map || !waterLayer) return;
  if (!on) {
    if (map.hasLayer(waterLayer)) map.removeLayer(waterLayer);
  } else if (!map.hasLayer(waterLayer)) {
    waterLayer.addTo(map);
    // rowy są rysowane dopiero od skali ok. 1:300 000 – przy widoku całego kraju przybliż do okolicy środka mapy
    if (map.getZoom() < WATER_MIN_ZOOM + 2) map.setZoom(WATER_MIN_ZOOM + 3);
  }
  syncWaterBtn();
}

/** Pasek przycisków nad mapą: wybór metryki + przełącznik cieków i rowów + podpowiedź o pinezce. */
export function renderMetricBar(container, initial) {
  barButtons.clear();
  const btns = METRICS.map(([k, ico, label, title]) => {
    const b = h('button', { type: 'button', class: 'mr-mapbar__btn', 'aria-pressed': 'false', title, onclick: () => setMetric(k) },
      [icon(ico), h('span', { text: label })]);
    barButtons.set(k, b);
    return b;
  });
  waterBtn = h('button', {
    type: 'button', class: 'mr-mapbar__btn mr-mapbar__btn--toggle', 'aria-pressed': 'false',
    title: 'Cieki, kanały i rowy z bazy BDOT10k (GUGiK) – widoczne po przybliżeniu mapy',
    onclick: () => setWaterLayer(!isWaterOn()),
  }, [icon('layers'), h('span', { text: 'Cieki i rowy' })]);
  container.replaceChildren(
    h('div', { class: 'mr-mapbar__group', role: 'group', 'aria-label': 'Co pokazać na mapie' }, btns),
    waterBtn,
    h('p', { class: 'mr-mapbar__hint' }, [icon('pin'), 'Kliknij mapę, aby postawić pinezkę i dodać pole']),
  );
  setMetric(initial || metric);
  syncWaterBtn();
}

export function highlight(selected) {
  lastSelected = selected;
  shapes.forEach((s, i) => {
    if (!s) return;
    const el = s.getElement && s.getElement();
    if (el) el.classList.toggle('mr-parcel--selected', i === selected);
    if (lastParcels.length > MAX_PERMANENT_LABELS) bindLabel(i, selected);
  });
}

// --- data nalotu ortofotomapy w podpisie mapy (skorowidz GUGiK, /api/ortho/date) ------------------------------
// Przy dużym zbliżeniu widać zdjęcie lotnicze z konkretnego nalotu; przy oddalonej mapie mozaika łączy różne lata,
// więc daty nie podajemy.
const ORTHO_ATTR = 'Ortofotomapa © <a href="https://www.geoportal.gov.pl/" target="_blank" rel="noopener noreferrer">GUGiK</a>';
const ORTHO_DATE_ZOOM = 14;
const orthoDates = new Map();   // „lat:lon” (ok. 1 km) → Promise<{date}>
let orthoAttr = ORTHO_ATTR;
let orthoTimer = null;

function setOrthoAttr(txt) {
  if (!map || !baseOrtho || txt === orthoAttr) return;
  const ac = map.attributionControl;
  if (ac && map.hasLayer(baseOrtho)) { ac.removeAttribution(orthoAttr); ac.addAttribution(txt); }
  baseOrtho.options.attribution = txt;
  orthoAttr = txt;
}

function syncOrthoDate() {
  clearTimeout(orthoTimer);
  if (!map || !baseOrtho || !map.hasLayer(baseOrtho) || map.getZoom() < ORTHO_DATE_ZOOM) { setOrthoAttr(ORTHO_ATTR); return; }
  orthoTimer = setTimeout(() => {
    const c = map.getCenter();
    const key = `${c.lat.toFixed(2)}:${c.lng.toFixed(2)}`;
    if (!orthoDates.has(key)) {
      orthoDates.set(key, api(`/api/ortho/date?lat=${c.lat.toFixed(4)}&lon=${c.lng.toFixed(4)}`, { timeout: 20000 })
        .catch(() => { orthoDates.delete(key); return null; }));
    }
    orthoDates.get(key).then((r) => {
      if (!map || map.getZoom() < ORTHO_DATE_ZOOM || !map.hasLayer(baseOrtho)) return;
      const d = r && typeof r.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(r.date) ? r.date : null;
      if (!d) { setOrthoAttr(ORTHO_ATTR); return; }
      const [y, m, dd] = d.split('-');
      setOrthoAttr(`${ORTHO_ATTR}, zdjęcie z ${dd}.${m}.${y}`);
    });
  }, 400);
}

// --- działki w kilku odległych miejscach ------------------------------------------------------------------
/** Grupy działek leżących blisko siebie – tylko działki narysowane na mapie. */
function parcelGroups(parcels) {
  const items = [];
  parcels.forEach((p, i) => {
    const s = shapes[i];
    if (!s) return;
    const c = s.getBounds ? s.getBounds().getCenter() : s.getLatLng();
    items.push({ i, p, c: [c.lat, c.lng] });
  });
  return groupByDistance(items, (x) => x.c).map((xs, k) => {
    const bounds = window.L.latLngBounds([]);
    xs.forEach((x) => { const s = shapes[x.i]; bounds.extend(s.getBounds ? s.getBounds() : s.getLatLng()); });
    const { name, county } = placeName(xs.map((x) => x.p), k);
    return { idx: xs.map((x) => x.i), area: xs.reduce((sum, x) => sum + (Number(x.p.area_ha) || 0), 0), bounds, center: bounds.getCenter(), place: name, county };
  });
}

function fitGroup(k) {
  const g = groups[k];
  if (!g || !map) return;
  map.fitBounds(g.bounds, { padding: [40, 40], maxZoom: 16 });
}

function syncGroupBtns() {
  if (!groupCtl || !map) return;
  const view = map.getBounds();
  const far = map.getZoom() < FAR_ZOOM;
  groupCtl.querySelectorAll('[data-group]').forEach((b) => {
    const g = groups[Number(b.dataset.group)];
    b.setAttribute('aria-pressed', String(!far && !!g && view.contains(g.center)));
  });
}

function syncFar() {
  if (!map) return;
  const far = map.getZoom() < FAR_ZOOM;
  map.getContainer().classList.toggle('mr-map--far', far);
  if (far && groups.length && !map.hasLayer(groupLayer)) groupLayer.addTo(map);
  if (!far && map.hasLayer(groupLayer)) groupLayer.remove();
  syncGroupBtns();
}

function buildGroups(parcels) {
  const L = window.L;
  groups = parcelGroups(parcels);
  groupLayer.clearLayers();
  groups.forEach((g, k) => {
    const el = h('span', { class: 'mr-groupmark__box' }, [
      h('strong', { class: 'mr-groupmark__n', text: String(g.idx.length) }),
      h('span', { class: 'mr-groupmark__name', text: g.place }),
    ]);
    L.marker(g.center, {
      icon: L.divIcon({ className: 'mr-groupmark', html: el, iconSize: null }),
      title: `${g.place}: ${plDzialek(g.idx.length)} – kliknij, aby przybliżyć`, keyboard: true, riseOnHover: true,
    }).on('click', () => fitGroup(k)).addTo(groupLayer);
  });
  if (groupCtl) { groupCtl.remove(); groupCtl = null; }
  if (groups.length > 1) {
    const ctl = L.control({ position: 'topleft' });
    ctl.onAdd = () => {
      const box = h('div', { class: 'leaflet-control mr-groupctl', role: 'group', 'aria-label': 'Miejsca z działkami' }, [
        h('span', { class: 'mr-groupctl__lbl', text: `Działki w ${groups.length} miejscach:` }),
        ...groups.map((g, k) => h('button', {
          type: 'button', class: 'mr-groupctl__btn', 'aria-pressed': 'false', dataset: { group: String(k) },
          title: `${g.place}${g.county && !g.place.startsWith('pow.') ? `, pow. ${g.county}` : ''}: ${plDzialek(g.idx.length)}`,
          onclick: (e) => { e.stopPropagation(); fitGroup(k); },
        }, [h('span', { class: 'mr-groupctl__name', text: g.place }), h('span', { class: 'mr-groupctl__n', text: String(g.idx.length) })])),
      ]);
      L.DomEvent.disableClickPropagation(box);
      return box;
    };
    ctl.addTo(map);
    groupCtl = ctl.getContainer();
    map.off('moveend', syncGroupBtns);
    map.on('moveend', syncGroupBtns);
  }
  syncFar();
}

let lastCenter = null;
/** Po analizie: działki w jednym miejscu – widok na wszystkie; w kilku odległych miejscach – na miejsce wybranej
 *  działki (albo największe), a pozostałe są pod przyciskami nad mapą. opts.all – zawsze cała Polska z działkami. */
export function fitToParcels(analysis, opts = {}) {
  if (!map) return;
  const b = parcelLayer.getBounds();
  if (analysis && Array.isArray(analysis.map_center)) lastCenter = analysis.map_center;
  if (b.isValid() && groups.length > 1 && !opts.all) {
    let k = groups.findIndex((g) => g.idx.includes(lastSelected));
    if (k < 0) k = groups.reduce((best, g, j) => (g.area > groups[best].area ? j : best), 0);
    fitGroup(k);
  } else if (b.isValid()) {
    map.fitBounds(b, { padding: [40, 40], maxZoom: 16 });
  } else if (lastCenter) {
    map.setView(lastCenter, 14);
  } else if (!analysis) {
    emit('map:hint', 'Brak działek do pokazania – dodaj pole albo wybierz gospodarstwo.');
  }
}

export function focusParcel(i) {
  const s = shapes[i];
  if (!s || !map) return;
  if (s.getBounds) map.fitBounds(s.getBounds(), { padding: [60, 60], maxZoom: 17 });
  else map.setView(s.getLatLng(), 16);
}

export function flyTo(lat, lon, zoom = 15) {
  if (map) map.setView([lat, lon], zoom);
}

/** Sieć melioracyjna (GeoJSON) – zawsze jako warstwa DEMO, gdy API tak oznaczy. */
export function renderNetwork(data) {
  const L = window.L;
  if (!map) return;
  networkLayer.clearLayers();
  const feats = (data && Array.isArray(data.features) ? data.features : []).filter((f) => f && f.type === 'Feature' && f.geometry);
  if (!feats.length) return;
  L.geoJSON({ type: 'FeatureCollection', features: feats }, {
    style: () => ({ className: 'mr-net', weight: 3 }),
    pointToLayer: (f, latlng) => L.circleMarker(latlng, { className: 'mr-net mr-net--point', radius: 7, weight: 2, fillOpacity: 0.9 }),
    onEachFeature: (f, layer) => {
      const pr = f.properties || {};
      const box = h('div', { class: 'mr-popup' }, [
        data.demo ? h('span', { class: 'mr-badge mr-badge--demo', text: 'DEMO' }) : null,
        h('p', { class: 'mr-popup__title', text: pr.name || 'Obiekt sieci' }),
        pr.kind ? h('p', { text: `Rodzaj: ${pr.kind}` }) : null,
        pr.status ? h('p', { text: `Stan: ${pr.status}` }) : null,
      ]);
      layer.bindPopup(box);
    },
  }).addTo(networkLayer);
  if (!map.hasLayer(networkLayer)) networkLayer.addTo(map);
}

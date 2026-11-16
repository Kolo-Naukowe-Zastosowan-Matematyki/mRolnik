// Mapa (Leaflet z static/vendor). Podkłady: ortofotomapa GUGiK (WMS) i OpenStreetMap.
import { h, icon } from './dom.js';
import { riskInfo, num } from './format.js';
import { inPoland } from './csv.js';
import { emit } from './state.js';

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

let map = null;
let parcelLayer = null;
let ndviLayer = null;
let ndmiLayer = null;      // NDMI (Sentinel-2) – woda w roślinach
let photoLayer = null;     // zdjęcie Sentinel-2 w barwach naturalnych
let networkLayer = null;
let layersControl = null;
let legend = null;
let ndviLegend = null; // paleta z API (layers.ndvi.legend), gdy dostępna
let addMode = false;
let pin = null;        // pinezka postawiona kliknięciem w mapę (przeciągalna)
let waterLayer = null;  // cieki i rowy (BDOT10k WMS)
let metric = 'ndvi';    // co pokazuje mapa: ndvi | ndmi | risk | photo
let lastParcels = [];
let lastSelected = 0;
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
        onclick: (e) => { e.stopPropagation(); fitToParcels(); } }, icon('fitAll')),
    ]);
    L.DomEvent.disableClickPropagation(box);
    return box;
  };
  fit.addTo(map);
  map.attributionControl.setPrefix('<a href="https://leafletjs.com/" target="_blank" rel="noopener noreferrer">Leaflet</a>');

  const ortho = L.tileLayer.wms(ORTO_WMS, {
    layers: 'Raster', format: 'image/jpeg', transparent: false, maxZoom: 19,
    attribution: 'Ortofotomapa © <a href="https://www.geoportal.gov.pl/" target="_blank" rel="noopener noreferrer">GUGiK</a>',
  });
  const osm = L.tileLayer(OSM_URL, {
    maxZoom: 19,
    attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">autorzy OpenStreetMap</a>',
  });
  const base = { 'Zdjęcia lotnicze (GUGiK)': ortho, 'Mapa (OpenStreetMap)': osm };
  (opts.base === 'osm' ? osm : ortho).addTo(map);

  parcelLayer = L.featureGroup().addTo(map);
  ndviLayer = L.layerGroup().addTo(map);
  ndmiLayer = L.layerGroup();
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

function pinPopup(lat, lng) {
  const box = h('div', { class: 'mr-popup mr-popup--pin' });
  const at = parcelAt(lat, lng);
  const hit = at >= 0 ? lastParcels[at] : null;
  const hitName = hit ? (hit.id_tag || `Działka ${at + 1}`) : '';

  const showMenu = () => {
    box.replaceChildren(
      h('p', { class: 'mr-popup__title', text: hit ? `Pinezka na polu: ${hitName}` : 'Nowe pole w tym miejscu?' }),
      h('p', { class: 'mr-popup__coords', text: `${lat.toFixed(5)}° N, ${lng.toFixed(5)}° E` }),
      h('p', { class: 'mr-hint', text: hit ? 'Możesz zmienić nazwę i uprawę tego pola albo dodać w tym miejscu nowe.' : 'Przeciągnij pinezkę, aby poprawić położenie. Granice działki pobierzemy z ewidencji GUGiK.' }),
      h('div', { class: 'mr-popup__actions' }, [
        h('button', { type: 'button', class: 'mr-btn mr-btn--primary', onclick: () => {
          const ll = pin.getLatLng();
          emit('map:add', round6(ll.lat, ll.lng));
          if (pin) pin.closePopup();
        } }, 'Dodaj pole w tym miejscu'),
        h('button', { type: 'button', class: 'mr-btn mr-btn--secondary', onclick: (e) => { e.stopPropagation(); showForm(); } }, [icon('edit'), 'Edytuj pole']),
        h('button', { type: 'button', class: 'mr-btn mr-btn--secondary', onclick: () => clearPin() }, 'Usuń pinezkę'),
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
  if (pin && map) map.removeLayer(pin);
  pin = null;
}

let ndmiLegend = null;   // paleta NDMI z API (layers.ndmi.legend)

function legendBox(title, list, note) {
  const wide = window.matchMedia('(min-width: 600px)').matches;
  const box = h('details', { class: 'mr-legend', open: wide }, [
    h('summary', { class: 'mr-legend__title', text: title }),
    list,
    note ? h('p', { class: 'mr-legend__note', text: note }) : null,
  ]);
  window.L.DomEvent.disableClickPropagation(box);
  return box;
}

function buildLegend() {
  if (metric === 'ndmi') {
    const lg = Array.isArray(ndmiLegend) ? ndmiLegend : [];
    return legendBox('Legenda: woda w roślinach (NDMI, Sentinel-2)', h('ul', null, lg.map((b) => {
      const sw = h('span', { class: 'mr-swatch', 'aria-hidden': 'true' });
      if (/^#[0-9a-f]{6}$/i.test(String(b.color))) sw.style.background = b.color;
      return h('li', null, [sw, `${b.label} (${Number(b.from).toFixed(1)}–${Number(b.to).toFixed(1)})`]);
    })), 'NDMI = (B8A − B11)/(B8A + B11): woda w roślinach i wierzchniej warstwie powierzchni, ta sama scena co NDVI. To nie jest wilgotność gleby w %.');
  }
  if (metric === 'photo') {
    return legendBox('Zdjęcie Sentinel-2 w barwach naturalnych', h('p', { text: 'Na działkach: zdjęcie z tej samej sceny co NDVI (B4/B3/B2). Poza działkami: ortofotomapa GUGiK.' }), null);
  }
  if (metric === 'risk') {
    return legendBox('Legenda: ryzyko (ocena mRolnik)', h('ul', null, ['Niskie', 'Średnie', 'Podwyższone', 'Wysokie', 'Brak danych'].map((r) => {
      const ri = riskInfo(r);
      return h('li', null, [h('span', { class: `mr-swatch mr-swatch--risk-${ri.cls}`, 'aria-hidden': 'true' }), ri.text]);
    })), 'Z kondycji roślin (NDVI) i zapasu wody w glebie.');
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
  photoLayer.clearLayers();
  const lgm = (parcels || []).map((p) => p && p.layers && p.layers.ndmi && p.layers.ndmi.legend).find(Boolean);
  if (lgm) ndmiLegend = lgm;
  const lg = (parcels || []).map((p) => p && p.layers && p.layers.ndvi && p.layers.ndvi.legend).find(Boolean);
  if (lg && lg !== ndviLegend) { ndviLegend = lg; if (legend) { legend.remove(); } }
  shapes.length = 0;
  labels.length = 0;
  (parcels || []).forEach((p, i) => {
    const r = riskInfo(p.risk);
    const sel = i === selected ? ' mr-parcel--selected' : '';
    let cls = `mr-parcel mr-parcel--${r.cls}${sel}`;
    let fillOpacity = 0.3;
    let second = r.text;
    if (metric === 'ndvi' || metric === 'ndmi' || metric === 'photo') {
      cls = `mr-parcel mr-parcel--outline${sel}`;
      fillOpacity = 0;
      const lay = p.layers || {};
      if (metric === 'ndvi') second = p.ndvi && p.ndvi.mean !== null && p.ndvi.mean !== undefined ? `NDVI ${num(p.ndvi.mean, 2)}` : 'NDVI: brak danych';
      else if (metric === 'ndmi') second = lay.ndmi && lay.ndmi.mean !== null && lay.ndmi.mean !== undefined ? `NDMI ${num(lay.ndmi.mean, 2)}` : 'NDMI: brak zdjęcia';
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
    if (okImg(lay.true_color)) L.imageOverlay(lay.true_color.image, lay.true_color.bounds, { opacity: 1, alt: `Zdjęcie Sentinel-2 działki ${p.id_tag || ''}`, className: 'mr-ndvi' }).addTo(photoLayer);
  });
  legend.remove();
  if (metric === 'risk' || (metric === 'ndmi' && ndmiLayer.getLayers().length) || (metric === 'photo' && photoLayer.getLayers().length)
      || (metric === 'ndvi' && ndviLayer.getLayers().length && map.hasLayer(ndviLayer))) legend.addTo(map);
}

// --- pasek metryk nad mapą ---------------------------------------------------------------------------
const METRICS = [
  ['ndvi', 'leaf', 'Kondycja roślin (NDVI)', 'Zdjęcie satelitarne Sentinel-2 – zieleń roślin w obrębie działki'],
  ['ndmi', 'drop', 'Woda w roślinach (NDMI)', 'Sentinel-2: NDMI = (B8A − B11)/(B8A + B11) z tej samej sceny co NDVI – woda w roślinach, nie wilgotność gleby w %'],
  ['risk', 'warn', 'Ryzyko', 'Ocena mRolnik z kondycji roślin i zapasu wody'],
  ['photo', 'satellite', 'Samo zdjęcie', 'Zdjęcie Sentinel-2 w barwach naturalnych na działkach (ta sama scena co NDVI) na tle ortofotomapy GUGiK'],
];

export function getMetric() { return metric; }

/** Zmienia metrykę pokazywaną na mapie (nakładka, kolory działek, legenda, stan przycisków). */
export function setMetric(m) {
  if (!METRICS.some(([k]) => k === m)) m = 'ndvi';
  metric = m;
  if (map) {
    if (m === 'ndvi') { if (!map.hasLayer(ndviLayer)) ndviLayer.addTo(map); } else if (map.hasLayer(ndviLayer)) map.removeLayer(ndviLayer);
    if (m === 'ndmi') { if (!map.hasLayer(ndmiLayer)) ndmiLayer.addTo(map); } else if (map.hasLayer(ndmiLayer)) map.removeLayer(ndmiLayer);
    if (m === 'photo') { if (!map.hasLayer(photoLayer)) photoLayer.addTo(map); } else if (map.hasLayer(photoLayer)) map.removeLayer(photoLayer);
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

let lastCenter = null;
export function fitToParcels(analysis) {
  if (!map) return;
  const b = parcelLayer.getBounds();
  if (analysis && Array.isArray(analysis.map_center)) lastCenter = analysis.map_center;
  if (b.isValid()) {
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

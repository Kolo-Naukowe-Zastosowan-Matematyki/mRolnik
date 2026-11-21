// Wybór gospodarstwa, centralna wyszukiwarka działek, dodawanie/usuwanie z listy, import/eksport CSV.
import { h, icon, replace, debounce, nextId } from './dom.js';
import { api } from './api.js';
import { state, emit, announce } from './state.js';
import { importParcels, exportCsv, downloadText, inPoland, MAX_PARCELS } from './csv.js';
import { flyTo, fitToParcels, parcelAtPoint } from './map.js';
import { plantState, waterState } from './parcelstate.js';
import {
  resolveCropInfo,
  syncCropCategoriesFromParcels,
  clearAllCategories,
  plDzialek,
} from './categories.js';

let searchCtrl = null;

/**
 * Główna centralna wyszukiwarka działek dla Kroku 1 (Widok 1).
 * Wyszukuje po numerze działki (np. TERYT lub 111/5), obrębie lub miejscowości.
 */
export function renderSearchHero(container) {
  const searchId = nextId('mr-hero-search');
  const resultsId = nextId('mr-hero-results');

  const results = h('div', { id: resultsId, class: 'mr-hero-results', hidden: true });
  const searchStatus = h('p', { class: 'mr-visually-hidden', role: 'status', 'aria-live': 'polite' });

  const input = h('input', {
    id: searchId,
    type: 'search',
    class: 'mr-input mr-input--hero-search',
    autocomplete: 'off',
    maxlength: '120',
    spellcheck: 'false',
    enterkeyhint: 'search',
    placeholder: 'Wpisz numer działki (np. TERYT 302505_5.0014.111/5 lub 111/5), obręb lub miejscowość, albo zaznacz pole bezpośrednio na mapie poniżej.',
    'aria-label': 'Wyszukaj działkę lub miejscowość',
  });

  const clearBtn = h('button', {
    type: 'button',
    class: 'mr-hero-search__clear',
    title: 'Wyczyść pole wyszukiwania',
    'aria-label': 'Wyczyść wyszukiwanie',
    hidden: true,
    onclick: () => {
      input.value = '';
      clearBtn.hidden = true;
      results.hidden = true;
      input.focus();
    },
  }, [icon('cross')]);

  const runSearch = debounce((q) => {
    clearBtn.hidden = !q;
    doHeroSearch(q, results, searchStatus);
  }, 220);

  input.addEventListener('input', () => runSearch(input.value.replace(/\s+/g, ' ').trim()));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      doHeroSearch(input.value.replace(/\s+/g, ' ').trim(), results, searchStatus);
    }
    if (e.key === 'Escape') {
      results.hidden = true;
    }
    if (e.key === 'ArrowDown') {
      const first = results.querySelector('button');
      if (first) { e.preventDefault(); first.focus(); }
    }
  });

  const searchBox = h('div', { class: 'mr-hero-searchbox' }, [
    icon('search', 'mr-hero-searchbox__ico'),
    input,
    clearBtn,
    results,
  ]);

  // Kliknięcie poza wyszukiwarką zamyka listę wyników
  document.addEventListener('click', (e) => {
    if (!searchBox.contains(e.target)) results.hidden = true;
  });
  input.addEventListener('focus', () => {
    if (results.childNodes.length && input.value.trim().length >= 2) results.hidden = false;
  });

  replace(container, [
    searchBox,
    searchStatus,
  ]);
}

/**
 * Wyszukiwanie miejsc lub działek z API /api/search-location.
 */
async function doHeroSearch(q, results, status) {
  if (searchCtrl) searchCtrl.abort();
  if (q.length < 2) {
    results.hidden = true;
    replace(results, []);
    return;
  }
  searchCtrl = new AbortController();
  try {
    const data = await api(`/api/search-location?q=${encodeURIComponent(q)}`, {
      signal: searchCtrl.signal,
      timeout: 20000,
    });
    const list = (data && Array.isArray(data.results) ? data.results : [])
      .filter((r) => r && Number.isFinite(Number(r.lat)) && Number.isFinite(Number(r.lon)))
      .slice(0, 10);

    if (!list.length) {
      replace(results, h('div', { class: 'mr-hero-results__empty' }, [
        icon('info'),
        h('p', { text: `Brak wyników dla „${q}”. Sprawdź poprawność numeru działki lub wpisz nazwę miejscowości.` }),
      ]));
      status.textContent = 'Brak wyników';
    } else {
      replace(results, h('ul', { class: 'mr-hero-results__list', 'aria-label': 'Wyniki wyszukiwania' },
        list.map((r) => heroResultItem(r, results))));
      status.textContent = `Znaleziono ${list.length} wyników`;
    }
    results.hidden = false;
  } catch (err) {
    if (err.kind === 'abort') return;
    replace(results, h('div', { class: 'mr-hero-results__empty mr-hero-results__empty--error' }, [
      icon('alert'),
      h('p', { text: `Błąd wyszukiwania: ${err.message}` }),
    ]));
    results.hidden = false;
  }
}

/**
 * Pojedynczy wiersz wyniku w wyszukiwarce.
 */
function heroResultItem(r, resultsContainer) {
  const lat = Number(r.lat);
  const lon = Number(r.lon);
  const isParcel = !!r.is_parcel;
  const rawName = String(r.display_name || `${lat}, ${lon}`);

  // Sprawdź czy działka jest już na liście wybranych
  const existingParcel = state.customParcels.find((p) =>
    (r.parcel_id && p.parcel_id === r.parcel_id) ||
    (Math.abs(p.lat - lat) < 0.0001 && Math.abs(p.lon - lon) < 0.0001)
  );

  const titleText = isParcel
    ? (r.id_tag || (r.parcel_id ? `Dz. ${r.parcel_id.split('.').pop()}` : 'Działka'))
    : rawName;

  const subText = isParcel
    ? [
        r.parcel_id ? `TERYT: ${r.parcel_id}` : null,
        r.area_ha ? `${Number(r.area_ha).toFixed(2).replace('.', ',')} ha` : null,
        r.commune ? `gm. ${r.commune}` : null,
      ].filter(Boolean).join(' · ')
    : 'Miejscowość / punkt na mapie';

  let parcelActionBtn;
  if (isParcel) {
    if (existingParcel) {
      parcelActionBtn = h('span', {
        class: 'mr-hero-results__added-badge',
        title: 'Działka znajduje się już na Twojej liście',
      }, [
        icon('checkCircle'),
        'Na liście',
      ]);
    } else {
      parcelActionBtn = h('button', {
        type: 'button',
        class: 'mr-btn mr-btn--primary mr-btn--small',
        title: 'Dodaj tę działkę do listy',
        onclick: () => {
          resultsContainer.hidden = true;
          addParcel({
            lat,
            lon,
            parcel_id: r.parcel_id || undefined,
            id_tag: r.id_tag || (r.parcel_id ? `Dz. ${r.parcel_id.split('.').pop()}` : titleText),
            area_ha: r.area_ha,
            commune: r.commune,
            county: r.county,
            voivodeship: r.voivodeship,
            crop: r.crop,
            soil: r.soil,
            geojson_coords: r.geojson_coords,
          });
          flyTo(lat, lon, 16);
        },
      }, [icon('plus'), 'Dodaj działkę']);
    }
  }

  return h('li', { class: `mr-hero-results__item${isParcel ? ' mr-hero-results__item--parcel' : ''}` }, [
    h('div', { class: 'mr-hero-results__icon' }, [
      icon(isParcel ? 'leaf' : 'pin'),
    ]),
    h('div', { class: 'mr-hero-results__content' }, [
      h('strong', { class: 'mr-hero-results__title', text: titleText }),
      h('span', { class: 'mr-hero-results__sub', text: subText }),
    ]),
    h('div', { class: 'mr-hero-results__actions' }, [
      h('button', {
        type: 'button',
        class: 'mr-btn mr-btn--ghost mr-btn--small',
        title: 'Pokaż to miejsce na mapie',
        onclick: () => {
          flyTo(lat, lon, isParcel ? 16 : 14);
          resultsContainer.hidden = true;
          announce(`Pokazano na mapie: ${titleText}`);
        },
      }, [icon('search'), 'Pokaż']),
      isParcel
        ? parcelActionBtn
        : h('button', {
            type: 'button',
            class: 'mr-btn mr-btn--secondary mr-btn--small',
            title: 'Przybliż mapę do tej miejscowości, aby wybrać działki',
            onclick: () => {
              flyTo(lat, lon, 14);
              resultsContainer.hidden = true;
              announce(`Przeniesiono mapę do: ${titleText}. Możesz teraz zaznaczyć swoje działki.`);
            },
          }, ['Wybierz na mapie']),
    ]),
  ]);
}

/**
 * Ładuje przykładowe gospodarstwo do listy wybranych działek w Kroku 1.
 */
export function loadPresetFarm(name) {
  const f = (state.presets || []).find((x) => x && x.name === name);
  if (!f || !Array.isArray(f.parcels) || !f.parcels.length) {
    emit('notice', `Nie znaleziono gospodarstwa „${name}”.`);
    return;
  }
  f.parcels.forEach((p) => {
    addParcel(p);
  });

  announce(`Wczytano działki z gospodarstwa ${name}.`);
  fitToParcels({ parcels: state.customParcels }, { all: false });
}

/**
 * Dodaje działkę bezpośrednio do listy w Kroku 1 (bez wymagania uprawy na tym etapie).
 */
export function addParcel(p) {
  if (!p || !Number.isFinite(Number(p.lat)) || !Number.isFinite(Number(p.lon))) {
    return false;
  }

  const lat = Number(p.lat);
  const lon = Number(p.lon);

  if (!inPoland(lat, lon)) {
    announce('Współrzędne poza granicami Polski.');
    emit('notice', 'Wybrany punkt leży poza granicami Polski.');
    return false;
  }

  if (state.customParcels.length >= MAX_PARCELS) {
    emit('notice', `Można dodać maksymalnie ${MAX_PARCELS} działek.`);
    return false;
  }

  const exists = state.customParcels.some((x) =>
    (p.parcel_id && x.parcel_id === p.parcel_id) ||
    (Math.abs(x.lat - lat) < 0.0001 && Math.abs(x.lon - lon) < 0.0001)
  );
  if (exists) {
    emit('notice', 'Ta działka znajduje się już na Twojej liście.');
    return false;
  }

  const cropInfo = p.crop ? resolveCropInfo(p.crop) : null;
  const tag = p.id_tag || (p.parcel_id ? `Dz. ${p.parcel_id.split('.').pop()}` : `Działka ${state.customParcels.length + 1}`);

  const parcel = {
    lat,
    lon,
    id_tag: tag,
    parcel_id: p.parcel_id || undefined,
    area_ha: p.area_ha !== undefined && p.area_ha !== null ? Number(p.area_ha) : undefined,
    commune: p.commune || undefined,
    county: p.county || undefined,
    voivodeship: p.voivodeship || undefined,
    crop: cropInfo ? cropInfo.name : undefined,
    cropId: cropInfo ? (cropInfo.cropId || undefined) : undefined,
    retentionGroupId: cropInfo ? (cropInfo.retentionGroupId || undefined) : undefined,
    retentionGroupName: cropInfo ? (cropInfo.retentionGroupName || undefined) : undefined,
    soil: p.soil || undefined,
    geojson_coords: p.geojson_coords || undefined,
  };

  state.customParcels.push(parcel);
  syncCropCategoriesFromParcels();

  emit('parcels:changed');
  announce(`Dodano działkę ${parcel.id_tag}. Łącznie na liście: ${state.customParcels.length} ${plDzialek(state.customParcels.length)}.`);

  // Jeśli działka trafiła na listę bez granic (np. z wyszukiwarki przykładów albo CSV), doczytaj granice z GUGiK w tle.
  if (!(Array.isArray(parcel.geojson_coords) && parcel.geojson_coords.length >= 3)) {
    const genericTag = !p.id_tag && !p.parcel_id;
    parcelAtPoint(parcel.lat, parcel.lon).then((res) => {
      const rp = res && res.parcel;
      if (!rp || !state.customParcels.includes(parcel)) return;
      if (Array.isArray(rp.geojson_coords) && rp.geojson_coords.length >= 3) {
        parcel.geojson_coords = rp.geojson_coords;
      }
      if (!parcel.parcel_id && rp.parcel_id) parcel.parcel_id = rp.parcel_id;
      if (genericTag && rp.parcel_id) parcel.id_tag = `Dz. ${rp.parcel_id.split('.').pop()}`;
      if (parcel.area_ha === undefined && Number.isFinite(Number(rp.area_ha))) parcel.area_ha = Number(rp.area_ha);
      if (!parcel.commune && rp.commune) parcel.commune = rp.commune;
      if (!parcel.county && rp.county) parcel.county = rp.county;
      if (!parcel.voivodeship && rp.voivodeship) parcel.voivodeship = rp.voivodeship;
      syncCropCategoriesFromParcels();
      emit('parcels:changed');
    }).catch(() => {});
  }

  return true;
}

/**
 * Usuwa pojedynczą działkę z listy zebranych działek.
 */
export function removeParcel(index) {
  if (index < 0 || index >= state.customParcels.length) return;
  const removed = state.customParcels.splice(index, 1)[0];
  if (state.selected >= state.customParcels.length) {
    state.selected = Math.max(0, state.customParcels.length - 1);
  }
  syncCropCategoriesFromParcels();
  emit('parcels:changed');
  announce(`Usunięto działkę ${removed.id_tag || ''}. Pozostało: ${state.customParcels.length} ${plDzialek(state.customParcels.length)}.`);
}

/**
 * Czyści wszystkie zebrane działki i przypisania upraw.
 */
export function clearAllParcels() {
  state.customParcels = [];
  state.selected = 0;
  clearAllCategories();
  emit('parcels:changed');
  announce('Usunięto wszystkie wybrane działki.');
}

/**
 * Eksport bieżących działek do pliku CSV.
 */
export function exportCurrent() {
  const a = state.analysis;
  const list = state.customParcels.length ? state.customParcels : (state.request || []);
  const rows = list.map((r, i) => ({ req: r, res: a && a.parcels ? a.parcels[i] : null }));
  const cols = [
    { label: 'id_tag', get: (x) => x.req.id_tag || '' },
    { label: 'parcel_id', get: (x) => (x.res && x.res.parcel_id) || x.req.parcel_id || '' },
    { label: 'lat', get: (x) => x.req.lat },
    { label: 'lon', get: (x) => x.req.lon },
    { label: 'crop', get: (x) => x.req.crop || (x.res && x.res.crop) || '' },
    { label: 'soil', get: (x) => x.req.soil || (x.res && x.res.soil) || '' },
    { label: 'area_ha', get: (x) => (x.res && x.res.area_ha !== null && x.res.area_ha !== undefined ? x.res.area_ha : (x.req.area_ha || '')) },
    { label: 'ndvi_mean', get: (x) => (x.res && x.res.ndvi ? x.res.ndvi.mean : '') },
    { label: 'kondycja_roslin', get: (x) => (x.res ? plantState(x.res).text : '') },
    { label: 'wilgotnosc_gleby', get: (x) => (x.res ? waterState(x.res).text : '') },
  ];
  if (!rows.length) { emit('notice', 'Brak działek do zapisania.'); return; }
  downloadText('mrolnik-dzialki.csv', exportCsv(cols, rows));
}

// Zachowanie wstecznej zgodności
export function renderTools(container) {
  renderSearchHero(container);
}

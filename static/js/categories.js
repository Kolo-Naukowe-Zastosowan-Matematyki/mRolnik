// Zarządzanie kategoriami upraw tworzonymi przez rolnika i przypisywaniem do nich działek (Widok 1).
import { h, icon, replace } from './dom.js';
import { state, emit, announce } from './state.js';
import { flyTo, parcelAtPoint } from './map.js';
import { inPoland, MAX_PARCELS } from './csv.js';
import { POLISH_CROPS, RETENTION_GROUPS, filterCrops, findCrop, getCropRetentionGroup } from './crops_retention.js';

let editingCategoryId = null;

/**
 * Tworzy nową kategorię uprawy.
 * Nowo utworzona kategoria staje się automatycznie aktywną.
 */
export function addCategory(input) {
  let crop = null;
  let name = '';

  if (typeof input === 'object' && input !== null) {
    crop = input;
    name = crop.name || '';
  } else {
    const str = String(input || '').trim();
    if (!str) {
      emit('notice', 'Wybierz lub wpisz nazwę uprawy.');
      return null;
    }
    crop = findCrop(str);
    name = crop ? crop.name : str;
  }

  // Sprawdź czy kategoria o takiej nazwie już istnieje (bez rozróżniania wielkości liter)
  const existing = state.cropCategories.find((c) =>
    c.name.toLowerCase() === name.toLowerCase() ||
    (crop && c.cropId && c.cropId === crop.id)
  );
  if (existing) {
    setActiveCategory(existing.id);
    emit('notice', `Uprawa „${existing.name}” już istnieje – została wybrana jako aktywna.`);
    return existing;
  }

  const id = `cat_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
  const retGroup = crop ? getCropRetentionGroup(crop.retention_group_id || crop.group_id || crop.name) : getCropRetentionGroup(name);

  const category = {
    id,
    cropId: crop ? crop.id : null,
    name,
    retentionGroupId: retGroup ? retGroup.id : null,
    retentionGroupName: retGroup ? retGroup.name : null,
    parcels: [],
  };

  state.cropCategories.push(category);
  state.activeCategoryId = id;
  syncCustomParcels();

  emit('categories:changed');
  emit('parcels:changed');
  announce(`Utworzono uprawę ${name}. Jest teraz aktywna. Działki z mapy i wyszukiwarki będą dodawane do tej uprawy.`);
  return category;
}

/**
 * Ustawia wybraną kategorię jako aktywną.
 */
export function setActiveCategory(id) {
  if (state.activeCategoryId === id) return;
  state.activeCategoryId = id;
  emit('categories:changed');
  const cat = state.cropCategories.find((c) => c.id === id);
  if (cat) {
    announce(`Aktywna uprawa: ${cat.name}. Działki będą dodawane do tej uprawy.`);
  }
}

/**
 * Zwraca aktualnie aktywną kategorię lub null.
 */
export function getActiveCategory() {
  if (!state.activeCategoryId) return null;
  return state.cropCategories.find((c) => c.id === state.activeCategoryId) || null;
}

/**
 * Zmienia nazwę istniejącej kategorii i aktualizuje pole crop w przypisanych działkach.
 */
export function renameCategory(id, newName) {
  const cat = state.cropCategories.find((c) => c.id === id);
  if (!cat) return;
  const trimmed = String(newName || '').trim();
  if (!trimmed) {
    emit('notice', 'Nazwa uprawy nie może być pusta.');
    return;
  }
  if (trimmed === cat.name) {
    editingCategoryId = null;
    emit('categories:changed');
    return;
  }

  // Sprawdź czy inna kategoria nie ma już takiej nazwy
  const duplicate = state.cropCategories.find((c) => c.id !== id && c.name.toLowerCase() === trimmed.toLowerCase());
  if (duplicate) {
    emit('notice', `Uprawa o nazwie „${trimmed}” już istnieje.`);
    return;
  }

  const matchedCrop = findCrop(trimmed);
  cat.name = trimmed;
  if (matchedCrop) {
    cat.cropId = matchedCrop.id;
    const rg = getCropRetentionGroup(matchedCrop.group_id);
    cat.retentionGroupId = rg ? rg.id : null;
    cat.retentionGroupName = rg ? rg.name : null;
  }
  cat.parcels.forEach((p) => {
    p.crop = trimmed;
  });
  editingCategoryId = null;
  syncCustomParcels();

  emit('categories:changed');
  emit('parcels:changed');
  announce(`Zmieniono nazwę uprawy na ${trimmed}.`);
}

/**
 * Usuwa kategorię i wszystkie przypisane do niej działki.
 */
export function removeCategory(id) {
  const idx = state.cropCategories.findIndex((c) => c.id === id);
  if (idx < 0) return;
  const removed = state.cropCategories.splice(idx, 1)[0];

  if (state.activeCategoryId === id) {
    state.activeCategoryId = state.cropCategories.length ? state.cropCategories[0].id : null;
  }
  syncCustomParcels();

  emit('categories:changed');
  emit('parcels:changed');
  announce(`Usunięto uprawę ${removed.name}.`);
}

/**
 * Dodaje działkę do aktualnie aktywnej kategorii uprawy.
 */
export function addParcelToActiveCategory(p) {
  if (!state.cropCategories.length || !state.activeCategoryId) {
    emit('notice', 'Najpierw wybierz uprawę i utwórz kategorię, aby przypisać do niej działki.');
    announce('Brak aktywnej uprawy. Najpierw utwórz kategorię uprawy.');
    const input = typeof document !== 'undefined'
      ? (document.getElementById('mr-crop-combobox-input') || document.getElementById('mr-new-crop-name'))
      : null;
    if (input) {
      input.focus();
      input.classList.add('mr-input--attention');
      setTimeout(() => input.classList.remove('mr-input--attention'), 1400);
    }
    return false;
  }

  const cat = getActiveCategory();
  if (!cat) return false;

  if (!inPoland(p.lat, p.lon)) {
    announce('Współrzędne poza granicami Polski.');
    emit('notice', 'Wybrany punkt leży poza granicami Polski.');
    return false;
  }

  if (state.customParcels.length >= MAX_PARCELS) {
    emit('notice', `Można dodać maksymalnie ${MAX_PARCELS} działek.`);
    return false;
  }

  // Sprawdź czy działka znajduje się już w TEJ kategorii
  const inThisCat = cat.parcels.some((x) =>
    (p.parcel_id && x.parcel_id === p.parcel_id) ||
    (Math.abs(x.lat - p.lat) < 0.0001 && Math.abs(x.lon - p.lon) < 0.0001)
  );
  if (inThisCat) {
    emit('notice', `Działka znajduje się już w uprawie „${cat.name}”.`);
    return false;
  }

  // Sprawdź czy działka jest przypisana do INNEJ kategorii
  const otherCat = state.cropCategories.find((c) =>
    c.id !== cat.id &&
    c.parcels.some((x) =>
      (p.parcel_id && x.parcel_id === p.parcel_id) ||
      (Math.abs(x.lat - p.lat) < 0.0001 && Math.abs(x.lon - p.lon) < 0.0001)
    )
  );
  if (otherCat) {
    emit('notice', `Ta działka jest już przypisana do uprawy „${otherCat.name}”.`);
    return false;
  }

  const tag = p.id_tag || (p.parcel_id ? `Dz. ${p.parcel_id.split('.').pop()}` : `Działka ${state.customParcels.length + 1}`);
  const parcel = {
    lat: Number(p.lat),
    lon: Number(p.lon),
    id_tag: tag,
    parcel_id: p.parcel_id || undefined,
    area_ha: p.area_ha !== undefined && p.area_ha !== null ? Number(p.area_ha) : undefined,
    commune: p.commune || undefined,
    county: p.county || undefined,
    voivodeship: p.voivodeship || undefined,
    crop: cat.name,
    categoryId: cat.id,
    soil: p.soil || undefined,
    geojson_coords: p.geojson_coords || undefined,
  };

  cat.parcels.push(parcel);
  syncCustomParcels();

  emit('categories:changed');
  emit('parcels:changed');
  announce(`Dodano działkę ${parcel.id_tag} do uprawy ${cat.name}. Łącznie w uprawie: ${cat.parcels.length}.`);

  // Jeśli działka trafiła na listę bez granic (np. z wyszukiwarki przykładów albo CSV), doczytaj granice z GUGiK w tle.
  if (!(Array.isArray(parcel.geojson_coords) && parcel.geojson_coords.length >= 3)) {
    const genericTag = !p.id_tag && !p.parcel_id;
    parcelAtPoint(parcel.lat, parcel.lon).then((res) => {
      const rp = res && res.parcel;
      if (!rp || !cat.parcels.includes(parcel)) return;
      if (Array.isArray(rp.geojson_coords) && rp.geojson_coords.length >= 3) {
        parcel.geojson_coords = rp.geojson_coords;
      }
      if (!parcel.parcel_id && rp.parcel_id) parcel.parcel_id = rp.parcel_id;
      if (genericTag && rp.parcel_id) parcel.id_tag = `Dz. ${rp.parcel_id.split('.').pop()}`;
      if (parcel.area_ha === undefined && Number.isFinite(Number(rp.area_ha))) parcel.area_ha = Number(rp.area_ha);
      if (!parcel.commune && rp.commune) parcel.commune = rp.commune;
      if (!parcel.county && rp.county) parcel.county = rp.county;
      if (!parcel.voivodeship && rp.voivodeship) parcel.voivodeship = rp.voivodeship;
      syncCustomParcels();
      emit('categories:changed');
      emit('parcels:changed');
    }).catch(() => {});
  }
  return true;
}

/**
 * Usuwa pojedynczą działkę z konkretnej kategorii.
 */
export function removeParcelFromCategory(categoryId, parcelIndex) {
  const cat = state.cropCategories.find((c) => c.id === categoryId);
  if (!cat || parcelIndex < 0 || parcelIndex >= cat.parcels.length) return;
  const removed = cat.parcels.splice(parcelIndex, 1)[0];
  syncCustomParcels();

  emit('categories:changed');
  emit('parcels:changed');
  announce(`Usunięto działkę ${removed.id_tag || ''} z uprawy ${cat.name}. Pozostało w tej uprawie: ${cat.parcels.length}.`);
}

/**
 * Usuwa działkę po jej globalnym indeksie w state.customParcels.
 */
export function removeParcelGlobal(index) {
  if (index < 0 || index >= state.customParcels.length) return;
  const target = state.customParcels[index];
  if (!target) return;

  for (const cat of state.cropCategories) {
    const pIdx = cat.parcels.findIndex((p) =>
      (target.parcel_id && p.parcel_id === target.parcel_id) ||
      (Math.abs(p.lat - target.lat) < 0.0001 && Math.abs(p.lon - target.lon) < 0.0001)
    );
    if (pIdx >= 0) {
      removeParcelFromCategory(cat.id, pIdx);
      return;
    }
  }
}

/**
 * Czyści wszystkie kategorie i działki.
 */
export function clearAllCategories() {
  state.cropCategories = [];
  state.activeCategoryId = null;
  editingCategoryId = null;
  syncCustomParcels();

  emit('categories:changed');
  emit('parcels:changed');
  announce('Usunięto wszystkie uprawy i działki.');
}

/**
 * Synchronizuje płaską listę state.customParcels z wszystkimi działkami w kategoriach.
 */
export function syncCustomParcels() {
  state.customParcels = state.cropCategories.flatMap((c) =>
    c.parcels.map((p) => ({
      ...p,
      crop: c.name,
      categoryId: c.id,
    }))
  );
}

/**
 * Odmiana słowa „działka”.
 */
export function plDzialek(n) {
  if (n === 1) return 'działka';
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return 'działki';
  return 'działek';
}

/**
 * Odmiana słowa „uprawa”.
 */
export function plUpraw(n) {
  if (n === 1) return 'uprawa';
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return 'uprawy';
  return 'upraw';
}

/**
 * Renderuje bloki kategorii w prawej kolumnie Kroku 1.
 */
export function renderCropCategories(container, emptyContainer) {
  if (!container) return;

  const count = state.cropCategories.length;
  if (count === 0) {
    container.hidden = true;
    if (emptyContainer) emptyContainer.hidden = false;
    replace(container, []);
    return;
  }

  container.hidden = false;
  if (emptyContainer) emptyContainer.hidden = true;

  const cards = state.cropCategories.map((cat) => {
    const isActive = cat.id === state.activeCategoryId;
    const catHa = cat.parcels.reduce((sum, p) => sum + (Number(p.area_ha) || 0), 0);
    const pCount = cat.parcels.length;
    const isEditing = editingCategoryId === cat.id;

    // Nagłówek kategorii
    const headerTitleRow = isEditing
      ? h('form', {
          class: 'mr-crop-rename-form',
          novalidate: true,
          onsubmit: (e) => {
            e.preventDefault();
            const input = e.target.querySelector('input');
            renameCategory(cat.id, input ? input.value : cat.name);
          },
        }, [
          h('input', {
            type: 'text',
            class: 'mr-input mr-input--small mr-crop-rename-input',
            value: cat.name,
            maxlength: '80',
            required: true,
            autocomplete: 'off',
            spellcheck: 'false',
            autofocus: true,
            onkeydown: (e) => {
              if (e.key === 'Escape') {
                editingCategoryId = null;
                emit('categories:changed');
              }
            },
          }),
          h('button', {
            type: 'submit',
            class: 'mr-btn mr-btn--primary mr-btn--small',
            title: 'Zapisz nazwę uprawy',
          }, [icon('checkCircle'), 'Zapisz']),
          h('button', {
            type: 'button',
            class: 'mr-btn mr-btn--ghost mr-btn--small',
            title: 'Anuluj zmianę nazwy',
            onclick: (e) => {
              e.stopPropagation();
              editingCategoryId = null;
              emit('categories:changed');
            },
          }, ['Anuluj']),
        ])
      : h('div', { class: 'mr-crop-card__title-box' }, [
          h('div', { class: 'mr-crop-card__name-line' }, [
            h('strong', { class: 'mr-crop-card__name', text: cat.name }),
            cat.retentionGroupName
              ? h('span', {
                  class: 'mr-badge mr-badge--retention-group',
                  title: `Grupa retencyjna: ${cat.retentionGroupName}`,
                  text: cat.retentionGroupName,
                })
              : null,
            isActive
              ? h('span', { class: 'mr-badge mr-badge--active-crop', title: 'Działki z mapy i wyszukiwarki trafiają do tej uprawy' }, [
                  icon('checkCircle'),
                  'Aktywna uprawa',
                ])
              : h('button', {
                  type: 'button',
                  class: 'mr-btn mr-btn--ghost mr-btn--small mr-crop-activate-btn',
                  title: `Kliknij, aby przypisywać kolejne działki do uprawy „${cat.name}”`,
                  onclick: (e) => {
                    e.stopPropagation();
                    setActiveCategory(cat.id);
                  },
                }, ['Ustaw jako aktywną']),
          ]),
          h('p', { class: 'mr-crop-card__meta', text: `${pCount} ${plDzialek(pCount)} · ${catHa.toFixed(2).replace('.', ',')} ha` }),
        ]);

    const header = h('div', {
      class: `mr-crop-card__header${isActive ? ' mr-crop-card__header--active' : ''}`,
      role: 'button',
      tabindex: '0',
      'aria-pressed': String(isActive),
      'aria-label': `Uprawa ${cat.name}, ${pCount} ${plDzialek(pCount)}. Kliknij, aby ustawić jako aktywną.`,
      onkeydown: (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          if (!isEditing) {
            e.preventDefault();
            setActiveCategory(cat.id);
          }
        }
      },
      onclick: (e) => {
        // Kliknięcie w nagłówek (o ile nie kliknięto przycisku akcji) aktywuje uprawę
        if (!isEditing && !e.target.closest('button') && !e.target.closest('input')) {
          setActiveCategory(cat.id);
        }
      },
    }, [
      h('div', { class: 'mr-crop-card__header-main' }, [
        icon('leaf', 'mr-crop-card__ico'),
        headerTitleRow,
      ]),
      h('div', { class: 'mr-crop-card__actions' }, [
        !isEditing
          ? h('button', {
              type: 'button',
              class: 'mr-btn mr-btn--icon mr-btn--ghost mr-crop-card__btn-edit',
              title: `Zmień nazwę uprawy „${cat.name}”`,
              'aria-label': `Zmień nazwę uprawy ${cat.name}`,
              onclick: (e) => {
                e.stopPropagation();
                editingCategoryId = cat.id;
                emit('categories:changed');
              },
            }, [icon('edit')])
          : null,
        h('button', {
          type: 'button',
          class: 'mr-btn mr-btn--icon mr-btn--ghost mr-crop-card__btn-delete',
          title: `Usuń uprawę „${cat.name}” wraz z przypisanymi działkami`,
          'aria-label': `Usuń uprawę ${cat.name}`,
          onclick: (e) => {
            e.stopPropagation();
            const msg = pCount > 0
              ? `Czy na pewno chcesz usunąć uprawę „${cat.name}” wraz z ${pCount} przypisanymi ${plDzialek(pCount)}?`
              : `Czy na pewno chcesz usunąć uprawę „${cat.name}”?`;
            if (window.confirm(msg)) {
              removeCategory(cat.id);
            }
          },
        }, [icon('trash')]),
      ]),
    ]);

    // Zawartość kategorii: lista działek lub komunikat o braku działek
    let body;
    if (pCount === 0) {
      body = h('div', { class: 'mr-crop-card__empty' }, [
        icon('info', 'mr-crop-card__empty-ico'),
        h('p', { text: 'Brak działek w tej uprawie.' }),
        h('p', { class: 'mr-crop-card__empty-sub', text: isActive ? 'Wybierz działkę na mapie lub wyszukaj powyżej, aby ją tutaj dodać.' : 'Ustaw tę uprawę jako aktywną, aby dodawać do niej działki.' }),
      ]);
    } else {
      const parcelItems = cat.parcels.map((p, pIdx) => {
        const title = p.id_tag || (p.parcel_id ? `Dz. ${p.parcel_id.split('.').pop()}` : `Działka ${pIdx + 1}`);
        const metaParts = [];
        if (p.parcel_id) metaParts.push(p.parcel_id);
        if (p.commune) metaParts.push(`gm. ${p.commune}`);
        if (p.county && !p.commune) metaParts.push(`pow. ${p.county}`);

        return h('li', { class: 'mr-crop-parcel-item' }, [
          h('div', {
            class: 'mr-crop-parcel-item__body',
            tabindex: '0',
            role: 'button',
            'aria-label': `Przybliż mapę do działki ${title}`,
            onkeydown: (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                if (p.lat && p.lon) flyTo(p.lat, p.lon, 16);
              }
            },
            onclick: () => {
              if (p.lat && p.lon) {
                flyTo(p.lat, p.lon, 16);
                announce(`Przeniesiono mapę do działki ${title}`);
              }
            },
          }, [
            h('div', { class: 'mr-crop-parcel-item__top' }, [
              h('strong', { class: 'mr-crop-parcel-item__name', text: title }),
              p.area_ha ? h('span', { class: 'mr-badge mr-badge--area', text: `${Number(p.area_ha).toFixed(2).replace('.', ',')} ha` }) : null,
            ]),
            metaParts.length ? h('p', { class: 'mr-crop-parcel-item__meta', text: metaParts.join(' · ') }) : null,
          ]),
          h('button', {
            type: 'button',
            class: 'mr-btn mr-btn--icon mr-btn--ghost mr-crop-parcel-item__remove',
            title: `Usuń działkę ${title} z uprawy „${cat.name}”`,
            'aria-label': `Usuń działkę ${title} z uprawy ${cat.name}`,
            onclick: (e) => {
              e.stopPropagation();
              removeParcelFromCategory(cat.id, pIdx);
            },
          }, [icon('trash')]),
        ]);
      });

      body = h('ul', { class: 'mr-crop-parcel-list', 'aria-label': `Działki w uprawie ${cat.name}` }, parcelItems);
    }

    return h('div', {
      class: `mr-crop-card${isActive ? ' mr-crop-card--active' : ''}`,
      'data-category-id': cat.id,
    }, [
      header,
      body,
    ]);
  });

  replace(container, cards);
}

/**
 * Renderuje wskaźnik aktywnej uprawy w centralnej sekcji wyszukiwarki (search-hero).
 */
export function renderHeroActiveCrop(container) {
  if (!container) return;

  const count = state.cropCategories.length;
  if (count === 0) {
    replace(container, h('div', { class: 'mr-hero-crop-alert mr-hero-crop-alert--empty', role: 'status' }, [
      icon('info', 'mr-hero-crop-alert__ico'),
      h('div', { class: 'mr-hero-crop-alert__text' }, [
        h('strong', { text: 'Nie wybrano uprawy.' }),
        h('span', { text: ' Wybierz uprawę z listy rozwijanej w panelu po prawej stronie, aby utworzyć pierwszą kategorię i dodawać do niej działki.' }),
      ]),
    ]));
    return;
  }

  const activeCat = getActiveCategory();
  if (!activeCat) {
    replace(container, h('div', { class: 'mr-hero-crop-alert mr-hero-crop-alert--empty', role: 'status' }, [
      icon('info', 'mr-hero-crop-alert__ico'),
      h('div', { class: 'mr-hero-crop-alert__text' }, [
        h('strong', { text: 'Wybierz aktywną uprawę.' }),
        h('span', { text: ' Kliknij na jedną z utworzonych upraw po prawej stronie, aby przypisywać do niej działki.' }),
      ]),
    ]));
    return;
  }

  const n = activeCat.parcels.length;
  const dzWord = plDzialek(n);

  // Jeśli jest więcej niż 1 kategoria, dodajemy szybki selektor zmiany
  const switchControls = count > 1
    ? h('div', { class: 'mr-hero-crop-switch' }, [
        h('label', { for: 'mr-hero-crop-select', class: 'mr-visually-hidden', text: 'Zmień aktywną uprawę' }),
        h('span', { class: 'mr-hero-crop-switch__label', text: 'Zmień uprawę:' }),
        h('select', {
          id: 'mr-hero-crop-select',
          class: 'mr-select mr-hero-crop-switch__select',
          onchange: (e) => {
            setActiveCategory(e.target.value);
          },
        }, state.cropCategories.map((c) =>
          h('option', {
            value: c.id,
            selected: c.id === activeCat.id,
            text: `${c.name} (${c.parcels.length})`,
          })
        )),
      ])
    : null;

  replace(container, h('div', { class: 'mr-hero-crop-alert mr-hero-crop-alert--active', role: 'status' }, [
    icon('leaf', 'mr-hero-crop-alert__ico'),
    h('div', { class: 'mr-hero-crop-alert__text' }, [
      h('span', { class: 'mr-hero-crop-alert__prefix', text: 'Dodajesz działki do uprawy:' }),
      h('strong', { class: 'mr-hero-crop-alert__name', text: activeCat.name }),
      h('span', { class: 'mr-hero-crop-alert__count', text: `(${n} ${dzWord})` }),
    ]),
    switchControls,
  ]));
}

/**
 * Renderuje wskaźnik aktywnej uprawy w pasku narzędzi mapy (mapbar).
 */
export function renderMapActiveCrop(container) {
  if (!container) return;

  const count = state.cropCategories.length;
  if (count === 0) {
    replace(container, h('span', { class: 'mr-mapbar__crop-badge mr-mapbar__crop-badge--empty', title: 'Utwórz kategorię uprawy po prawej stronie' }, [
      icon('info'),
      h('span', { text: 'Najpierw utwórz uprawę' }),
    ]));
    return;
  }

  const activeCat = getActiveCategory();
  if (!activeCat) {
    replace(container, h('span', { class: 'mr-mapbar__crop-badge mr-mapbar__crop-badge--empty' }, [
      icon('info'),
      h('span', { text: 'Wybierz aktywną uprawę' }),
    ]));
    return;
  }

  replace(container, h('span', {
    class: 'mr-mapbar__crop-badge mr-mapbar__crop-badge--active',
    title: `Działki kliknięte na mapie zostaną przypisane do uprawy „${activeCat.name}”`,
  }, [
    icon('leaf'),
    h('span', { text: `Uprawa: ${activeCat.name}` }),
  ]));
}

/**
 * Inicjalizuje komponent wyszukiwalnej listy rozwijanej upraw (searchable combobox).
 */
export function initCropCombobox(container) {
  if (!container || typeof document === 'undefined') return;

  const input = container.querySelector('#mr-crop-combobox-input');
  const toggleBtn = container.querySelector('#mr-crop-combobox-toggle');
  const dropdown = container.querySelector('#mr-crop-combobox-dropdown');
  const listEl = container.querySelector('#mr-crop-combobox-list');
  const hiddenId = container.closest('form') ? container.closest('form').querySelector('#mr-selected-crop-id') : null;

  if (!input || !dropdown || !listEl) return;

  let isOpen = false;
  let activeIndex = -1;
  let currentItems = [];

  function unhighlightAll() {
    const activeEls = listEl.querySelectorAll('.mr-combobox__item--active');
    activeEls.forEach((el) => el.classList.remove('mr-combobox__item--active'));
  }

  function highlightItem(index) {
    unhighlightAll();
    if (index < 0 || index >= currentItems.length) {
      activeIndex = -1;
      input.removeAttribute('aria-activedescendant');
      return;
    }
    activeIndex = index;
    const item = currentItems[index];
    const itemEl = listEl.querySelector(`[data-crop-id="${item.id}"]`);
    if (itemEl) {
      itemEl.classList.add('mr-combobox__item--active');
      input.setAttribute('aria-activedescendant', itemEl.id);
      itemEl.scrollIntoView({ block: 'nearest' });
    }
  }

  function selectCrop(crop) {
    if (!crop) return;
    input.value = crop.name;
    if (hiddenId) hiddenId.value = crop.id;
    close();
    input.focus();
    announce(`Wybrano uprawę: ${crop.name}. Kliknij Dodaj, aby utworzyć kategorię.`);
  }

  function renderList(crops) {
    currentItems = crops || [];
    activeIndex = -1;
    replace(listEl, []);

    if (!crops || crops.length === 0) {
      const emptyItem = h('li', {
        class: 'mr-combobox__empty',
        role: 'presentation',
        text: 'Nie znaleziono uprawy o takiej nazwie.',
      });
      listEl.appendChild(emptyItem);
      return;
    }

    // Pogrupuj uprawy według grup retencyjnych
    const grouped = new Map();
    for (const crop of crops) {
      const gId = crop.retention_group_id || crop.group_id || 'other';
      if (!grouped.has(gId)) {
        grouped.set(gId, []);
      }
      grouped.get(gId).push(crop);
    }

    let itemIdx = 0;
    grouped.forEach((groupCrops, gId) => {
      const groupInfo = RETENTION_GROUPS[gId];
      const groupHeader = h('li', {
        class: 'mr-combobox__group-header',
        role: 'presentation',
      }, [
        h('span', { class: 'mr-combobox__group-title', text: groupInfo ? groupInfo.name : 'Pozostałe uprawy' }),
        groupInfo ? h('span', { class: 'mr-combobox__group-badge', text: `${groupCrops.length}` }) : null,
      ]);
      listEl.appendChild(groupHeader);

      for (const crop of groupCrops) {
        const thisIdx = itemIdx++;
        const itemId = `mr-crop-opt-${crop.id}`;
        const optionEl = h('li', {
          id: itemId,
          class: 'mr-combobox__item',
          role: 'option',
          'data-crop-id': crop.id,
          'aria-selected': 'false',
          onmousedown: (e) => {
            // zapobiega utracie fokusu przed wyborem
            e.preventDefault();
          },
          onclick: () => {
            selectCrop(crop);
          },
          onmouseenter: () => {
            highlightItem(thisIdx);
          },
        }, [
          h('div', { class: 'mr-combobox__item-main' }, [
            icon('leaf', 'mr-combobox__item-ico'),
            h('span', { class: 'mr-combobox__item-name', text: crop.name }),
          ]),
          h('span', { class: 'mr-combobox__item-sub', text: crop.category }),
        ]);
        listEl.appendChild(optionEl);
      }
    });
  }

  function open() {
    if (isOpen) return;
    isOpen = true;
    dropdown.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    renderList(filterCrops(input.value));
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    dropdown.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    activeIndex = -1;
    input.removeAttribute('aria-activedescendant');
    unhighlightAll();
  }

  input.addEventListener('focus', () => {
    open();
  });

  input.addEventListener('input', () => {
    if (hiddenId) hiddenId.value = '';
    open();
    renderList(filterCrops(input.value));
  });

  input.addEventListener('keydown', (e) => {
    if (!isOpen) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        open();
        return;
      }
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (currentItems.length > 0) {
        const next = activeIndex + 1 >= currentItems.length ? 0 : activeIndex + 1;
        highlightItem(next);
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (currentItems.length > 0) {
        const prev = activeIndex - 1 < 0 ? currentItems.length - 1 : activeIndex - 1;
        highlightItem(prev);
      }
    } else if (e.key === 'Enter') {
      if (isOpen && activeIndex >= 0 && activeIndex < currentItems.length) {
        e.preventDefault();
        selectCrop(currentItems[activeIndex]);
      }
    } else if (e.key === 'Escape') {
      if (isOpen) {
        e.preventDefault();
        close();
      }
    }
  });

  if (toggleBtn) {
    toggleBtn.addEventListener('click', (e) => {
      e.preventDefault();
      if (isOpen) {
        close();
      } else {
        open();
        input.focus();
      }
    });
  }

  document.addEventListener('click', (e) => {
    if (!container.contains(e.target)) {
      close();
    }
  });
}


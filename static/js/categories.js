// KROK 2: Wybór upraw dla wybranych działek (baza upraw w Polsce wg grup retencyjnych).
import { h, icon, replace, nextId } from './dom.js';
import { state, emit, announce } from './state.js';
import { flyTo } from './map.js';
import { RETENTION_GROUPS, filterCrops, findCrop, getCropRetentionGroup } from './crops_retention.js';

let bulkDraftName = '';
let bulkDraftCropId = '';

// Pojedynczy globalny listener zamykania otwartych list rozwijanych (bez wycieków pamięci przy przerenderowaniu).
const openComboboxes = new Set();
let docListenerBound = false;

function ensureDocClickListener() {
  if (docListenerBound || typeof document === 'undefined') return;
  docListenerBound = true;
  document.addEventListener('click', (e) => {
    openComboboxes.forEach((entry) => {
      if (!entry.el.isConnected) {
        openComboboxes.delete(entry);
        return;
      }
      if (!entry.el.contains(e.target)) {
        entry.close();
      }
    });
  });
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
 * Rozwiązuje dane uprawy (z bazy POLISH_CROPS lub nazwę wpisaną ręcznie).
 */
export function resolveCropInfo(input) {
  if (!input) return null;
  let crop = null;
  let name = '';
  if (typeof input === 'object' && input !== null) {
    crop = input;
    name = String(crop.name || '').trim();
  } else {
    const str = String(input).trim();
    if (!str) return null;
    crop = findCrop(str);
    name = crop ? crop.name : str;
  }
  if (!name) return null;
  const retGroup = crop
    ? getCropRetentionGroup(crop.retention_group_id || crop.group_id || crop.name)
    : getCropRetentionGroup(name);
  return {
    crop,
    cropId: crop ? crop.id : null,
    name,
    retentionGroupId: retGroup ? retGroup.id : null,
    retentionGroupName: retGroup ? retGroup.name : null,
    retentionGroupShort: retGroup ? retGroup.short_label : null,
  };
}

/**
 * Synchronizuje state.cropCategories na podstawie przypisań w state.customParcels.
 */
export function syncCropCategoriesFromParcels() {
  const byKey = new Map();
  state.customParcels.forEach((p) => {
    const cropName = p && p.crop ? String(p.crop).trim() : '';
    if (!cropName) return;
    const key = cropName.toLowerCase();
    if (!byKey.has(key)) {
      const info = resolveCropInfo(p.cropId || cropName);
      byKey.set(key, {
        id: `cat_${key.replace(/[^a-z0-9]+/g, '_')}`,
        cropId: info ? info.cropId : null,
        name: info ? info.name : cropName,
        retentionGroupId: info ? info.retentionGroupId : null,
        retentionGroupName: info ? info.retentionGroupName : null,
        parcels: [],
      });
    }
    byKey.get(key).parcels.push(p);
  });
  state.cropCategories = Array.from(byKey.values());
  state.activeCategoryId = state.cropCategories.length ? state.cropCategories[0].id : null;
}

/**
 * Czyści stan kategorii upraw i szkic szybkiego przypisania.
 */
export function clearAllCategories() {
  state.cropCategories = [];
  state.activeCategoryId = null;
  bulkDraftName = '';
  bulkDraftCropId = '';
}

/**
 * Zwraca liczbę wybranych działek z przypisaną uprawą.
 */
export function countAssignedParcels() {
  return state.customParcels.filter((p) => Boolean(p && p.crop && String(p.crop).trim())).length;
}

/**
 * Zwraca true, jeśli istnieje co najmniej jedna działka i każda ma przypisaną uprawę.
 */
export function areAllParcelsAssigned() {
  return state.customParcels.length > 0 && countAssignedParcels() === state.customParcels.length;
}

/**
 * Przypisuje uprawę do konkretnej działki w state.customParcels.
 */
export function setParcelCrop(parcelIndex, cropInput) {
  const p = state.customParcels[parcelIndex];
  if (!p) return false;
  const info = resolveCropInfo(cropInput);
  if (!info) return false;

  p.crop = info.name;
  p.cropId = info.cropId || undefined;
  p.retentionGroupId = info.retentionGroupId || undefined;
  p.retentionGroupName = info.retentionGroupName || undefined;

  syncCropCategoriesFromParcels();
  emit('categories:changed');
  const title = p.id_tag || (p.parcel_id ? `Dz. ${p.parcel_id.split('.').pop()}` : `Działka ${parcelIndex + 1}`);
  announce(`Przypisano uprawę ${info.name} do działki ${title}.`);
  return true;
}

/**
 * Usuwa przypisaną uprawę z pojedynczej działki.
 */
export function clearParcelCrop(parcelIndex) {
  const p = state.customParcels[parcelIndex];
  if (!p) return;
  p.crop = undefined;
  p.cropId = undefined;
  p.retentionGroupId = undefined;
  p.retentionGroupName = undefined;

  syncCropCategoriesFromParcels();
  emit('categories:changed');
  const title = p.id_tag || (p.parcel_id ? `Dz. ${p.parcel_id.split('.').pop()}` : `Działka ${parcelIndex + 1}`);
  announce(`Usunięto przypisaną uprawę dla działki ${title}.`);
}

/**
 * Przypisuje wybraną uprawę do wszystkich działek (lub tylko nieprzypisanych).
 */
export function assignCropToAll(cropInput, onlyUnassigned = false) {
  const info = resolveCropInfo(cropInput);
  if (!info) {
    emit('notice', 'Wybierz lub wpisz nazwę uprawy.');
    return 0;
  }

  let updated = 0;
  state.customParcels.forEach((p) => {
    if (onlyUnassigned && p.crop && String(p.crop).trim()) return;
    p.crop = info.name;
    p.cropId = info.cropId || undefined;
    p.retentionGroupId = info.retentionGroupId || undefined;
    p.retentionGroupName = info.retentionGroupName || undefined;
    updated += 1;
  });

  if (updated > 0) {
    syncCropCategoriesFromParcels();
    emit('categories:changed');
    announce(`Przypisano uprawę ${info.name} do ${updated} ${plDzialek(updated)}.`);
  }
  return updated;
}

/**
 * Buduje wielokrotnego użytku wyszukiwalny combobox upraw z bazy upraw w Polsce.
 */
export function createCropCombobox(opts = {}) {
  ensureDocClickListener();

  const uid = opts.idPrefix || nextId('mr-crop-cb');
  const inputId = `${uid}-input`;
  const listId = `${uid}-list`;

  let isOpen = false;
  let activeIndex = -1;
  let currentItems = [];
  let selectedCropId = opts.initialCropId || '';

  const listEl = h('ul', {
    class: 'mr-combobox__list',
    id: listId,
    role: 'listbox',
    'aria-label': opts.listAriaLabel || 'Lista dostępnych upraw w Polsce',
  });

  const dropdown = h('div', {
    class: 'mr-combobox__dropdown',
    hidden: true,
  }, [listEl]);

  const input = h('input', {
    type: 'text',
    id: inputId,
    class: `mr-input mr-combobox__input${opts.compact ? ' mr-combobox__input--compact' : ''}`,
    value: opts.initialValue || '',
    placeholder: opts.placeholder || 'Wpisz lub wybierz uprawę...',
    autocomplete: 'off',
    spellcheck: 'false',
    role: 'combobox',
    'aria-autocomplete': 'list',
    'aria-expanded': 'false',
    'aria-haspopup': 'listbox',
    'aria-controls': listId,
    'aria-label': opts.ariaLabel || 'Wybierz lub wyszukaj uprawę z bazy upraw w Polsce',
  });

  const toggleBtn = h('button', {
    type: 'button',
    class: 'mr-combobox__toggle-btn',
    tabindex: '-1',
    'aria-label': 'Rozwiń listę upraw',
    title: 'Rozwiń listę upraw',
  }, [icon('chevronDown')]);

  const rootEl = h('div', { class: `mr-combobox${opts.compact ? ' mr-combobox--inline' : ''}` }, [
    h('div', { class: 'mr-combobox__control' }, [input, toggleBtn]),
    dropdown,
  ]);

  function unhighlightAll() {
    listEl.querySelectorAll('.mr-combobox__item--active').forEach((el) => {
      el.classList.remove('mr-combobox__item--active');
    });
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

  function close() {
    if (!isOpen) return;
    isOpen = false;
    dropdown.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    activeIndex = -1;
    input.removeAttribute('aria-activedescendant');
    unhighlightAll();
    openComboboxes.delete(entry);
  }

  const entry = { el: rootEl, close };

  function selectCrop(crop) {
    if (!crop) return;
    input.value = crop.name;
    selectedCropId = crop.id;
    close();
    if (typeof opts.onSelect === 'function') {
      opts.onSelect(crop);
    }
  }

  function renderList(crops) {
    currentItems = crops || [];
    activeIndex = -1;
    replace(listEl, []);

    if (!crops || crops.length === 0) {
      listEl.appendChild(h('li', {
        class: 'mr-combobox__empty',
        role: 'presentation',
        text: 'Nie znaleziono uprawy – naciśnij Enter, aby użyć wpisanej nazwy.',
      }));
      return;
    }

    const grouped = new Map();
    for (const crop of crops) {
      const gId = crop.retention_group_id || crop.group_id || 'other';
      if (!grouped.has(gId)) grouped.set(gId, []);
      grouped.get(gId).push(crop);
    }

    let itemIdx = 0;
    grouped.forEach((groupCrops, gId) => {
      const groupInfo = RETENTION_GROUPS[gId];
      listEl.appendChild(h('li', {
        class: 'mr-combobox__group-header',
        role: 'presentation',
      }, [
        h('span', { class: 'mr-combobox__group-title', text: groupInfo ? groupInfo.name : 'Pozostałe uprawy' }),
        groupInfo ? h('span', { class: 'mr-combobox__group-badge', text: String(groupCrops.length) }) : null,
      ]));

      for (const crop of groupCrops) {
        const thisIdx = itemIdx++;
        const itemId = `${uid}-opt-${crop.id}`;
        const isCurrent = selectedCropId === crop.id || input.value.trim().toLowerCase() === crop.name.toLowerCase();
        listEl.appendChild(h('li', {
          id: itemId,
          class: 'mr-combobox__item',
          role: 'option',
          'data-crop-id': crop.id,
          'aria-selected': String(isCurrent),
          onmousedown: (e) => {
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
        ]));
      }
    });
  }

  function open(showAll = false) {
    if (!isOpen) {
      isOpen = true;
      dropdown.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      openComboboxes.add(entry);
    }
    const query = showAll ? '' : input.value;
    renderList(filterCrops(query));
  }

  input.addEventListener('focus', () => {
    if (typeof opts.onFocus === 'function') opts.onFocus();
    // Jeśli pole ma już wybraną uprawę, pokaż całą listę, aby łatwo ją zmienić
    const exact = Boolean(findCrop(input.value));
    open(exact || !input.value.trim());
  });

  input.addEventListener('input', () => {
    selectedCropId = '';
    if (typeof opts.onInputChange === 'function') opts.onInputChange(input.value);
    open(false);
  });

  input.addEventListener('keydown', (e) => {
    if (!isOpen && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      open(true);
      return;
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (currentItems.length > 0) {
        highlightItem((activeIndex + 1) % currentItems.length);
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (currentItems.length > 0) {
        highlightItem((activeIndex - 1 + currentItems.length) % currentItems.length);
      }
    } else if (e.key === 'Enter') {
      if (isOpen && activeIndex >= 0 && activeIndex < currentItems.length) {
        e.preventDefault();
        selectCrop(currentItems[activeIndex]);
      } else if (typeof opts.onCommitText === 'function') {
        e.preventDefault();
        close();
        opts.onCommitText(input.value.trim(), selectedCropId);
      } else {
        close();
      }
    } else if (e.key === 'Escape') {
      if (isOpen) {
        e.preventDefault();
        close();
      }
    }
  });

  input.addEventListener('blur', () => {
    close();
    if (typeof opts.onBlurCommit === 'function') {
      opts.onBlurCommit(input.value.trim(), selectedCropId);
    }
  });

  toggleBtn.addEventListener('mousedown', (e) => {
    e.preventDefault();
  });

  toggleBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (isOpen) {
      close();
    } else {
      input.focus();
      open(true);
    }
  });

  return {
    el: rootEl,
    input,
    getValue: () => ({ name: input.value.trim(), cropId: selectedCropId }),
    clear: () => {
      input.value = '';
      selectedCropId = '';
    },
    close,
  };
}

/**
 * Podświetla aktywny wiersz działki w Kroku 2 bez przebudowy DOM.
 */
export function highlightCropsParcelRow(selectedIndex) {
  if (typeof document === 'undefined') return;
  const rows = document.querySelectorAll('.mr-crops-parcel-row');
  rows.forEach((row) => {
    const idx = Number(row.dataset.parcelIndex);
    const active = idx === selectedIndex;
    row.classList.toggle('mr-crops-parcel-row--active', active);
    if (active) {
      row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  });
}

/**
 * Zwięzły pasek parametrów wodnych i retencyjnych dla dopasowanej uprawy.
 */
function renderCropTraits(cropNameOrId) {
  const info = resolveCropInfo(cropNameOrId);
  if (!info) return null;

  const parts = [];
  if (info.crop && Array.isArray(info.crop.root_depth_cm)) {
    parts.push(`Korzenie: ${info.crop.root_depth_cm[0]}–${info.crop.root_depth_cm[1]} cm`);
  }
  if (info.crop && info.crop.water_demand) {
    parts.push(`Zapotrzebowanie wodne: ${info.crop.water_demand}`);
  }
  if (info.crop && info.crop.critical_period) {
    parts.push(`Okres krytyczny: ${info.crop.critical_period}`);
  }

  return h('div', { class: 'mr-crops-traits' }, [
    info.retentionGroupName
      ? h('span', {
          class: 'mr-badge mr-badge--retention-group',
          title: `Grupa retencyjna: ${info.retentionGroupName}`,
          text: info.retentionGroupName,
        })
      : h('span', { class: 'mr-badge mr-badge--soil-none', text: 'Uprawa własna' }),
    parts.length ? h('p', { class: 'mr-crops-traits__text', text: parts.join(' · ') }) : null,
  ]);
}

/**
 * Karta szybkiego przypisania jednej uprawy do wielu działek (gdy wybrano > 1 działkę).
 */
function buildBulkAssignCard(count, assignedCount) {
  const unassignedCount = count - assignedCount;
  const previewBox = h('div', { class: 'mr-crops-bulk-preview' });

  const updatePreview = (query) => {
    const traits = query ? renderCropTraits(query) : null;
    replace(previewBox, traits ? [traits] : []);
  };

  const cb = createCropCombobox({
    idPrefix: 'mr-bulk-crop',
    initialValue: bulkDraftName,
    initialCropId: bulkDraftCropId,
    placeholder: 'Wpisz lub wybierz uprawę z bazy (np. Pszenica ozima, Kukurydza)...',
    ariaLabel: 'Wybierz uprawę do przypisania wielu działkom',
    onSelect: (crop) => {
      bulkDraftName = crop.name;
      bulkDraftCropId = crop.id;
      updatePreview(crop);
    },
    onInputChange: (val) => {
      bulkDraftName = val;
      bulkDraftCropId = '';
      updatePreview(val);
    },
    onCommitText: (val, cropId) => {
      bulkDraftName = val;
      bulkDraftCropId = cropId;
      if (val || cropId) {
        assignCropToAll(cropId || val, false);
      }
    },
  });

  updatePreview(bulkDraftCropId || bulkDraftName);

  const onApply = (onlyUnassigned) => {
    const { name, cropId } = cb.getValue();
    if (!name && !cropId) {
      emit('notice', 'Wybierz lub wpisz nazwę uprawy powyżej.');
      cb.input.focus();
      cb.input.classList.add('mr-input--attention');
      setTimeout(() => cb.input.classList.remove('mr-input--attention'), 1200);
      return;
    }
    assignCropToAll(cropId || name, onlyUnassigned);
  };

  return h('section', { class: 'mr-card mr-crops-bulk-card', 'aria-labelledby': 'mr-crops-bulk-h' }, [
    h('h3', { class: 'mr-card__title', id: 'mr-crops-bulk-h' }, [
      icon('leaf'),
      'Szybkie przypisanie uprawy do wielu działek',
    ]),
    h('p', {
      class: 'mr-muted mr-crops-card-desc',
      text: 'Jeśli na wszystkich lub pozostałych działkach rośnie ta sama roślina, wybierz ją raz i przypisz jednym kliknięciem. Każdą działkę możesz też ustawić osobno poniżej.',
    }),
    h('div', { class: 'mr-crops-bulk-row' }, [
      cb.el,
      h('div', { class: 'mr-crops-bulk-actions' }, [
        h('button', {
          type: 'button',
          class: 'mr-btn mr-btn--primary',
          id: 'mr-btn-assign-all-crops',
          onclick: () => onApply(false),
        }, [icon('check'), `Przypisz wszystkim (${count})`]),
        assignedCount > 0 && unassignedCount > 0
          ? h('button', {
              type: 'button',
              class: 'mr-btn mr-btn--secondary',
              id: 'mr-btn-assign-unassigned-crops',
              onclick: () => onApply(true),
            }, [`Tylko bez uprawy (${unassignedCount})`])
          : null,
      ]),
    ]),
    previewBox,
  ]);
}

/**
 * Główna funkcja renderująca zawartość KROKU 2 (Wybór upraw) w [data-ref="crops-main"].
 */
export function renderCropsStep(container) {
  if (!container) return;

  const parcels = state.customParcels;
  const count = parcels.length;

  if (count === 0) {
    replace(container, h('section', { class: 'mr-card mr-crops-empty' }, [
      icon('info', 'mr-collected-empty__ico'),
      h('h3', { class: 'mr-collected-empty__text', text: 'Nie wybrano jeszcze żadnych działek.' }),
      h('p', { class: 'mr-collected-empty__sub', text: 'Wróć do Kroku 1, aby wybrać działki na mapie lub w wyszukiwarce.' }),
      h('button', {
        type: 'button',
        class: 'mr-btn mr-btn--primary',
        onclick: () => emit('step:go', 1),
      }, 'Wróć do wyboru działek'),
    ]));
    return;
  }

  const assignedCount = countAssignedParcels();
  const allAssigned = assignedCount === count;

  const parcelRows = parcels.map((p, i) => {
    const title = p.id_tag || (p.parcel_id ? `Dz. ${p.parcel_id.split('.').pop()}` : `Działka ${i + 1}`);
    const metaParts = [];
    if (p.parcel_id) metaParts.push(p.parcel_id);
    if (p.commune) metaParts.push(`gm. ${p.commune}`);
    if (p.county && !p.commune) metaParts.push(`pow. ${p.county}`);

    const hasCrop = Boolean(p.crop && String(p.crop).trim());
    const isSel = i === state.selected;

    const focusOnMap = () => {
      emit('parcel:select', i);
      if (p.lat && p.lon) flyTo(p.lat, p.lon, 16);
    };

    const cb = createCropCombobox({
      idPrefix: `mr-parcel-crop-${i}`,
      compact: true,
      initialValue: p.crop || '',
      initialCropId: p.cropId || '',
      placeholder: 'Wybierz lub wpisz uprawę...',
      ariaLabel: `Wybierz uprawę dla działki ${title}`,
      onFocus: () => {
        if (state.selected !== i) {
          emit('parcel:select', i);
          if (p.lat && p.lon) flyTo(p.lat, p.lon, 16);
        }
      },
      onSelect: (crop) => {
        setParcelCrop(i, crop);
      },
      onCommitText: (val, cropId) => {
        if (!val && !cropId) {
          if (hasCrop) clearParcelCrop(i);
          return;
        }
        setParcelCrop(i, cropId || val);
      },
      onBlurCommit: (val, cropId) => {
        const prev = (p.crop || '').trim();
        if (!val) {
          if (prev) clearParcelCrop(i);
          return;
        }
        if (val !== prev) {
          setParcelCrop(i, cropId || val);
        }
      },
    });

    return h('li', {
      class: `mr-crops-parcel-row${isSel ? ' mr-crops-parcel-row--active' : ''}${hasCrop ? ' mr-crops-parcel-row--assigned' : ' mr-crops-parcel-row--missing'}`,
      dataset: { parcelIndex: String(i) },
    }, [
      h('div', { class: 'mr-crops-parcel-row__head' }, [
        h('div', {
          class: 'mr-crops-parcel-row__info',
          role: 'button',
          tabindex: '0',
          'aria-label': `Pokaż działkę ${title} na mapie`,
          onclick: focusOnMap,
          onkeydown: (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              focusOnMap();
            }
          },
        }, [
          h('div', { class: 'mr-crops-parcel-row__title-line' }, [
            h('strong', { class: 'mr-crops-parcel-row__name', text: title }),
            p.area_ha ? h('span', { class: 'mr-badge mr-badge--area', text: `${Number(p.area_ha).toFixed(2).replace('.', ',')} ha` }) : null,
            hasCrop
              ? h('span', { class: 'mr-badge mr-badge--crop-ok' }, [icon('checkCircle'), 'Przypisano'])
              : h('span', { class: 'mr-badge mr-badge--crop-missing', text: 'Wybierz uprawę' }),
          ]),
          metaParts.length ? h('p', { class: 'mr-crops-parcel-row__meta', text: metaParts.join(' · ') }) : null,
        ]),

        h('div', { class: 'mr-crops-parcel-row__control' }, [
          cb.el,
          hasCrop
            ? h('button', {
                type: 'button',
                class: 'mr-btn mr-btn--icon mr-btn--ghost mr-crops-parcel-row__clear',
                title: `Wyczyść uprawę dla działki ${title}`,
                'aria-label': `Wyczyść uprawę dla działki ${title}`,
                onclick: (e) => {
                  e.stopPropagation();
                  clearParcelCrop(i);
                },
              }, [icon('cross')])
            : null,
        ]),
      ]),
      hasCrop ? renderCropTraits(p.cropId || p.crop) : null,
    ]);
  });

  const listCard = h('section', { class: 'mr-card mr-crops-list-card', 'aria-labelledby': 'mr-crops-list-h' }, [
    h('div', { class: 'mr-crops-list-head' }, [
      h('h3', { class: 'mr-card__title', id: 'mr-crops-list-h' }, [
        icon('leaf'),
        count === 1 ? 'Wybierz uprawę dla wybranej działki' : 'Uprawy na wybranych działkach',
      ]),
      h('span', {
        class: `mr-badge ${allAssigned ? 'mr-badge--crop-ok' : 'mr-badge--crop-missing'}`,
        text: `Przypisano: ${assignedCount} / ${count}`,
      }),
    ]),
    h('p', {
      class: 'mr-muted mr-crops-card-desc',
      text: 'Kliknij działkę, aby podświetlić ją na mapie po prawej stronie. Dobór uprawy pozwala uwzględnić głębokość korzeni, zapotrzebowanie wodne i fazę rozwojową w ocenie sytuacji wodnej.',
    }),
    h('ul', { class: 'mr-crops-parcel-list', 'aria-label': 'Lista działek do przypisania uprawy' }, parcelRows),
  ]);

  const footer = h('div', { class: 'mr-crops-footer' }, [
    h('p', {
      class: 'mr-crops-footer__hint',
      text: allAssigned
        ? 'Wszystkie działki mają przypisaną uprawę. Możesz przejść do oceny sytuacji wodnej.'
        : `Wybierz uprawę dla wszystkich działek (pozostało: ${count - assignedCount}), aby przejść do oceny sytuacji wodnej.`,
    }),
    h('div', { class: 'mr-crops-footer__actions' }, [
      h('button', {
        type: 'button',
        class: 'mr-btn mr-btn--secondary',
        onclick: () => emit('step:go', 1),
      }, 'Wróć do wyboru działek'),
      h('button', {
        type: 'button',
        class: 'mr-btn mr-btn--primary mr-cta-btn mr-crops-footer__cta',
        id: 'mr-btn-crops-proceed-bottom',
        disabled: !allAssigned,
        onclick: () => emit('step:go', 3),
      }, [
        'Przejdź do oceny sytuacji wodnej',
        icon('checkCircle'),
      ]),
    ]),
  ]);

  replace(container, h('div', { class: 'mr-crops-step' }, [
    count > 1 ? buildBulkAssignCard(count, assignedCount) : null,
    listCard,
    footer,
  ]));
}

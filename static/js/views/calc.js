// Zakładka „Kalkulatory”: formularze wysyłają dane do API – wzory są tylko po stronie serwera.
import { h, icon, replace, nextId } from '../dom.js';
import { api } from '../api.js';
import { selectedParcel } from '../state.js';
import { num, loadingView, errorView } from '../format.js';
import { profitSection } from './profitcalc.js';

let metaPromise = null;
function loadMeta() {
  if (!metaPromise) metaPromise = api('/api/calculators/meta', { timeout: 20000 }).catch((e) => { metaPromise = null; throw e; });
  return metaPromise;
}

export function renderCalc(panel) {
  if (panel.dataset.ready === '1') { updateArea(panel); return; }
  replace(panel, loadingView('Wczytywanie kalkulatorów…'));
  loadMeta().then((meta) => {
    panel.dataset.ready = '1';
    replace(panel, [
      h('p', { class: 'mr-callout mr-callout--info' }, [icon('info'), 'Wyniki są orientacyjne – nie zastępują planu nawozowego, analizy gleby (OSChR) ani zaleceń hodowcy odmiany.']),
      h('details', { class: 'mr-details mr-details--card' }, [h('summary', null, [icon('leaf'), 'Norma wysiewu']), sowingForm(meta.sowing)]),
      h('details', { class: 'mr-details mr-details--card' }, [h('summary', null, [icon('calc'), 'Nawożenie NPK']), fertForm(meta.fertilizer)]),
      profitSection(),
    ]);
    updateArea(panel);
  }).catch((err) => replace(panel, errorView(err, () => renderCalc(panel), 'Nie udało się wczytać kalkulatorów')));
}

function updateArea(panel) {
  const prof = panel.querySelector('#mr-profit-card');
  if (prof && prof._refresh) prof._refresh();
  const p = selectedParcel();
  if (!p || p.area_ha === null || p.area_ha === undefined) return;
  panel.querySelectorAll('input[name="area_ha"]').forEach((el) => {
    if (!el.dataset.touched) el.value = String(Math.round(p.area_ha * 100) / 100);
  });
}

// --- budowanie pól z etykietą, podpowiedzią i komunikatem błędu (aria-describedby) -----------
function field(form, name, label, input, hint) {
  const id = nextId(`mr-${name}`);
  const errId = `${id}-err`;
  const hintId = hint ? `${id}-hint` : null;
  input.id = id;
  input.name = name;
  input.setAttribute('aria-describedby', [hintId, errId].filter(Boolean).join(' '));
  input.addEventListener('input', () => { input.dataset.touched = '1'; });
  const wrap = h('div', { class: 'mr-field', dataset: { field: name } }, [
    h('label', { for: id, class: 'mr-label', text: label }),
    hint ? h('p', { class: 'mr-hint', id: hintId, text: hint }) : null,
    input,
    h('p', { class: 'mr-error', id: errId, hidden: true }),
  ]);
  form._fields = form._fields || {};
  form._fields[name] = { input, err: wrap.lastChild, wrap };
  return wrap;
}

function numInput(value, min, max, step = 'any', opts = {}) {
  return h('input', { type: 'number', class: 'mr-input', inputmode: 'decimal', min, max, step, value: value === null || value === undefined ? '' : value, placeholder: opts.placeholder || null, required: opts.optional ? null : true });
}

function selectInput(options, value) {
  const s = h('select', { class: 'mr-select' }, Object.entries(options).map(([v, label]) => h('option', { value: v, text: typeof label === 'string' ? label : label.label })));
  if (value !== undefined) s.value = value;
  return s;
}

function clearErrors(form) {
  Object.values(form._fields || {}).forEach((f) => { f.err.hidden = true; f.err.textContent = ''; f.input.removeAttribute('aria-invalid'); });
  form._summary.hidden = true;
}

function showErrors(form, err) {
  const detail = Array.isArray(err.detail) ? err.detail : [];
  let first = null;
  detail.forEach((d) => {
    const name = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : null;
    const f = form._fields && form._fields[name];
    if (f) {
      f.err.textContent = translate(d);
      f.err.hidden = false;
      f.input.setAttribute('aria-invalid', 'true');
      if (!first) first = f.input;
    }
  });
  form._summary.textContent = first ? 'Popraw zaznaczone pola.' : (err.message || 'Nie udało się obliczyć.');
  form._summary.hidden = false;
  (first || form._summary).focus();
}

function translate(d) {
  const c = d.ctx || {};
  switch (d.type) {
    case 'greater_than': return `Wartość musi być większa niż ${c.gt}.`;
    case 'greater_than_equal': return `Wartość musi wynosić co najmniej ${c.ge}.`;
    case 'less_than_equal': return `Wartość może wynosić najwyżej ${c.le}.`;
    case 'less_than': return `Wartość musi być mniejsza niż ${c.lt}.`;
    case 'float_parsing': case 'float_type': case 'missing': return 'Podaj liczbę.';
    case 'literal_error': case 'enum': return 'Wybierz wartość z listy.';
    default: return 'Niepoprawna wartość.';
  }
}

function collect(form, names) {
  const out = {};
  names.forEach((n) => {
    const f = form._fields[n];
    if (!f) return;
    const el = f.input;
    if (el.type === 'checkbox') out[n] = el.checked;
    else if (el.type === 'number') {
      if (el.value.trim() === '') { if (!el.required) out[n] = null; else out[n] = el.value; }
      else out[n] = Number(el.value);
    } else out[n] = el.value;
  });
  return out;
}

async function submitForm(form, path, body, renderResult) {
  clearErrors(form);
  const btn = form.querySelector('button[type="submit"]');
  btn.disabled = true;
  replace(form._result, loadingView('Obliczanie…'));
  try {
    const res = await api(path, { method: 'POST', body, timeout: 20000 });
    replace(form._result, renderResult(res));
    form._result.focus();
  } catch (err) {
    replace(form._result, []);
    if (err.kind === 'validation') showErrors(form, err);
    else replace(form._result, errorView(err, () => form.requestSubmit(), 'Nie udało się obliczyć'));
  } finally {
    btn.disabled = false;
  }
}

function resultShell(title, big, rows, res, extra) {
  return h('div', { class: 'mr-result' }, [
    h('h3', { class: 'mr-result__title', text: title }),
    h('p', { class: 'mr-result__big' }, big),
    rows.length ? h('dl', { class: 'mr-pairs' }, rows.flatMap(([k, v]) => [h('dt', { text: k }), h('dd', { text: v })])) : null,
    extra || null,
    (res.warnings || []).length ? h('ul', { class: 'mr-warnlist' }, res.warnings.map((w) => h('li', null, [icon('warn'), w]))) : null,
    (res.recommendations || (res.recommendation ? [res.recommendation] : [])).filter(Boolean).length
      ? h('ul', { class: 'mr-plainlist' }, (res.recommendations || [res.recommendation]).filter(Boolean).map((r) => h('li', { text: r }))) : null,
    (res.assumptions || []).length ? h('details', { class: 'mr-details' }, [h('summary', { text: 'Założenia i źródła' }), h('ul', { class: 'mr-plainlist' }, res.assumptions.map((a) => h('li', { text: a })))]) : null,
  ]);
}

// --- norma wysiewu ------------------------------------------------------------------------------
function sowingForm(meta) {
  const form = h('form', { class: 'mr-form', novalidate: true });
  const fieldsBox = h('div', { class: 'mr-form__fields' });
  form._summary = h('p', { class: 'mr-error mr-error--summary', role: 'alert', tabindex: '-1', hidden: true });
  form._result = h('div', { class: 'mr-form__result', tabindex: '-1', 'aria-live': 'polite' });
  const catName = nextId('mr-cat');
  const cats = Object.entries(meta.categories);
  const radios = h('fieldset', { class: 'mr-fieldset' }, [
    h('legend', { class: 'mr-label', text: 'Rodzaj siewu' }),
    h('div', { class: 'mr-segmented' }, cats.map(([v, label], i) => {
      const id = `${catName}-${v}`;
      return h('span', { class: 'mr-segmented__opt' }, [
        h('input', { type: 'radio', name: catName, id, value: v, checked: i === 0 }),
        h('label', { for: id, text: label }),
      ]);
    })),
  ]);
  let names = [];
  const build = (cat) => {
    form._fields = {};
    let presets = null;
    const els = [];
    if (cat === 'zboza') presets = meta.cereal_presets;
    if (cat === 'straczkowe') presets = meta.legume_presets;
    if (presets) {
      const sel = selectInput(Object.fromEntries(Object.entries(presets).map(([k, v]) => [k, v.label])));
      sel.addEventListener('change', () => apply(presets[sel.value]));
      els.push(field(form, 'preset', 'Uprawa (wartości typowe)', sel, 'Wybór uprawy wpisuje typową obsadę i MTZ – możesz je zmienić.'));
    }
    const first = presets ? Object.values(presets)[0] : null;
    if (cat === 'zboza') {
      els.push(field(form, 'seeds_m2', 'Obsada [szt. nasion/m²]', numInput(first.seeds_m2, 1, 1000, 1)));
    } else if (cat === 'straczkowe') {
      els.push(field(form, 'plants_m2', 'Docelowa obsada [roślin/m²]', numInput(first.plants_m2, 1, 300, 1)));
    } else {
      const crops = meta.precision_crops;
      const sel = selectInput(Object.fromEntries(Object.entries(crops).map(([k, v]) => [k, v.label])));
      sel.addEventListener('change', () => { const c = crops[sel.value]; apply({ row_spacing_cm: c.row_cm, seed_spacing_cm: c.spacing_cm, mtz_g: c.mtz_g }); });
      els.push(field(form, 'crop', 'Uprawa', sel));
      const c0 = Object.values(crops)[0];
      els.push(h('div', { class: 'mr-row2' }, [
        field(form, 'row_spacing_cm', 'Rozstaw rzędów [cm]', numInput(c0.row_cm, 10, 150, 'any')),
        field(form, 'seed_spacing_cm', 'Odstęp roślin w rzędzie [cm]', numInput(c0.spacing_cm, 1, 100, 'any')),
      ]));
    }
    const mtz0 = first ? first.mtz_g : Object.values(meta.precision_crops)[0].mtz_g;
    els.push(field(form, 'mtz_g', 'Masa tysiąca nasion (MTZ) [g]', numInput(mtz0, 0.1, 2000, 'any'), 'Z etykiety worka z nasionami.'));
    els.push(h('div', { class: 'mr-row2' }, [
      field(form, 'germination_pct', 'Kiełkowanie [%]', numInput(cat === 'straczkowe' ? 90 : 95, 50, 100, 'any')),
      field(form, 'purity_pct', 'Czystość [%]', numInput(cat === 'straczkowe' ? 98 : 99, 50, 100, 'any')),
    ]));
    els.push(field(form, 'area_ha', 'Powierzchnia [ha]', numInput(1, 0.01, 10000, 'any')));
    if (cat === 'zboza') {
      els.push(h('details', { class: 'mr-details' }, [h('summary', { text: 'Korekty: termin, gleba, warunki' }),
        field(form, 'sowing_date', 'Termin siewu', selectInput(meta.sowing_date, 'optymalny')),
        field(form, 'soil', 'Gleba', selectInput(meta.soil, 'srednia')),
        field(form, 'field_conditions', 'Warunki siewu', selectInput(meta.field_conditions, 'przecietne')),
      ]));
    }
    replace(fieldsBox, els);
    names = Object.keys(form._fields).filter((n) => n !== 'preset');
    replace(form._result, []);
  };
  const apply = (vals) => Object.entries(vals).forEach(([k, v]) => {
    const key = k === 'label' ? null : k;
    if (key && form._fields[key]) form._fields[key].input.value = v;
  });
  radios.addEventListener('change', (e) => { if (e.target.name === catName) build(e.target.value); updateArea(form.closest('.mr-tabpanel') || form); });
  build(cats[0][0]);

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const cat = form.querySelector(`input[name="${catName}"]:checked`).value;
    const body = { category: cat, ...collect(form, names) };
    submitForm(form, '/api/calculators/sowing', body, (r) => resultShell(
      'Wynik: norma wysiewu',
      [h('strong', { text: `${num(r.seed_rate_kg_ha, 1)} kg/ha` }), h('span', { text: ` · razem ${num(r.total_seed_kg, 1)} kg (${r.bags_25kg} worków po 25 kg)` })],
      [
        ['Obsada roślin', `${num(r.plants_per_ha, 0)} /ha (${num(r.plants_per_m2, 1)} /m²)`],
        ['Nasion do wysiania', `${num(r.seeds_per_ha, 0)} /ha`],
        r.units_per_ha !== null && r.units_per_ha !== undefined ? ['Jednostki siewne', `${num(r.units_per_ha, 2)} /ha, razem ${num(r.units_total, 1)} (1 j. = ${num(r.seeds_per_unit, 0)} nasion)`] : null,
        r.drill_seed_spacing_cm ? ['Odstęp nasion na siewniku', `${num(r.drill_seed_spacing_cm, 1)} cm`] : null,
      ].filter(Boolean),
      r,
    ));
  });

  append(form, [radios, fieldsBox, form._summary, h('button', { type: 'submit', class: 'mr-btn mr-btn--primary' }, [icon('calc'), 'Oblicz normę']), form._result]);
  return form;
}

// --- nawożenie ----------------------------------------------------------------------------------
function fertForm(meta) {
  const form = h('form', { class: 'mr-form', novalidate: true });
  form._summary = h('p', { class: 'mr-error mr-error--summary', role: 'alert', tabindex: '-1', hidden: true });
  form._result = h('div', { class: 'mr-form__result', tabindex: '-1', 'aria-live': 'polite' });
  form._fields = {};
  const crops = meta.crops;
  const cropSel = selectInput(Object.fromEntries(Object.entries(crops).map(([k, v]) => [k, v.label])));
  const yieldIn = numInput(Object.values(crops)[0].default_yield, 0.1, 150, 'any');
  const yieldField = field(form, 'expected_yield_t_ha', `Oczekiwany plon [${Object.values(crops)[0].yield_unit}]`, yieldIn);
  const byProd = h('input', { type: 'checkbox', class: 'mr-check' });
  const byWrap = h('div', { class: 'mr-field mr-field--check' }, [byProd, h('label', { for: '', text: '' })]);
  const byId = nextId('mr-by');
  byProd.id = byId; byProd.name = 'by_product_removed';
  byWrap.lastChild.setAttribute('for', byId);
  form._fields.by_product_removed = { input: byProd, err: h('p', { hidden: true }), wrap: byWrap };
  const syncCrop = () => {
    const c = crops[cropSel.value];
    yieldIn.value = c.default_yield;
    yieldField.querySelector('label').textContent = `Oczekiwany plon [${c.yield_unit}]`;
    byWrap.hidden = !c.has_by_product;
    byWrap.lastChild.textContent = c.has_by_product ? `Zbieram produkt uboczny z pola (${c.by_product})` : '';
    byProd.checked = false;
  };
  cropSel.addEventListener('change', syncCrop);
  const orgUnit = (k) => (meta.organic[k] && meta.organic[k].unit) || 't';
  const orgSel = selectInput(Object.fromEntries(Object.entries(meta.organic).map(([k, v]) => [k, v.label])), 'brak');
  const orgDose = numInput(0, 0, 100, 'any');
  const orgField = field(form, 'organic_dose', 'Dawka nawozu naturalnego [t/ha]', orgDose);
  orgSel.addEventListener('change', () => { orgField.querySelector('label').textContent = `Dawka nawozu naturalnego [${orgUnit(orgSel.value)}/ha]`; orgField.hidden = orgSel.value === 'brak'; });
  orgField.hidden = true;

  append(form, [
    field(form, 'crop', 'Uprawa', cropSel),
    h('div', { class: 'mr-row2' }, [yieldField, field(form, 'area_ha', 'Powierzchnia [ha]', numInput(1, 0.01, 10000, 'any'))]),
    field(form, 'soil_category', 'Kategoria agronomiczna gleby', selectInput(meta.soil_categories, 'srednia')),
    h('div', { class: 'mr-row2' }, [
      field(form, 'p_class', 'Zasobność w fosfor (P)', selectInput(meta.abundance_classes, 'srednia')),
      field(form, 'k_class', 'Zasobność w potas (K)', selectInput(meta.abundance_classes, 'srednia')),
    ]),
    h('details', { class: 'mr-details' }, [h('summary', { text: 'Więcej: Nmin, przedplon, obornik, pH' }),
      field(form, 'nmin_kg_ha', 'Azot mineralny w glebie wiosną (Nmin) [kg N/ha]', numInput(null, 0, 300, 'any', { optional: true, placeholder: 'np. 50' }), 'Z badania OSChR. Puste – przyjmiemy wartość typową dla gleby.'),
      field(form, 'forecrop', 'Przedplon', selectInput(meta.forecrops, 'zboza')),
      field(form, 'organic_type', 'Nawóz naturalny w tym sezonie', orgSel),
      orgField,
      field(form, 'soil_ph', 'pH gleby (opcjonalnie)', numInput(null, 3.5, 9, 'any', { optional: true, placeholder: 'np. 6,2' })),
    ]),
    byWrap,
    form._summary,
    h('button', { type: 'submit', class: 'mr-btn mr-btn--primary' }, [icon('calc'), 'Oblicz dawki']),
    form._result,
  ]);
  syncCrop();

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const body = collect(form, ['crop', 'expected_yield_t_ha', 'area_ha', 'soil_category', 'p_class', 'k_class', 'nmin_kg_ha', 'forecrop', 'organic_type', 'organic_dose', 'soil_ph', 'by_product_removed']);
    if (body.organic_type === 'brak') body.organic_dose = 0;
    submitForm(form, '/api/calculators/fertilizer', body, (r) => resultShell(
      `Wynik: ${r.crop_label}, plon ${num(r.expected_yield_t_ha, 1)} t/ha`,
      h('span', { class: 'mr-npk' }, [
        npk('N', r.n_kg_ha, r.n_total_kg), npk('P₂O₅', r.p2o5_kg_ha, r.p2o5_total_kg), npk('K₂O', r.k2o_kg_ha, r.k2o_total_kg),
      ]),
      [],
      r,
      [
        (r.n_splits || []).length ? h('table', { class: 'mr-table' }, [
          h('caption', { text: 'Podział dawki azotu' }),
          h('thead', null, h('tr', null, [h('th', { scope: 'col', text: 'Termin' }), h('th', { scope: 'col', text: 'Udział' }), h('th', { scope: 'col', text: 'kg N/ha' })])),
          h('tbody', null, r.n_splits.map((s) => h('tr', null, [h('th', { scope: 'row', text: s.term }), h('td', { text: `${s.share_pct}%` }), h('td', { text: num(s.n_kg_ha, 0) })]))),
        ]) : null,
        productsTable('Wariant 1: nawozy jednoskładnikowe', r.products_simple),
        productsTable('Wariant 2: polifoska + uzupełnienie', r.products_npk),
      ],
    ));
  });
  return form;
}

function npk(label, perHa, total) {
  return h('span', { class: 'mr-npk__item' }, [h('span', { class: 'mr-npk__label', text: label }), h('strong', { text: `${num(perHa, 0)} kg/ha` }), h('span', { class: 'mr-muted', text: `razem ${num(total, 0)} kg` })]);
}

function productsTable(caption, list) {
  if (!Array.isArray(list) || !list.length) return null;
  return h('div', { class: 'mr-tablewrap', tabindex: '0', role: 'region', 'aria-label': caption }, h('table', { class: 'mr-table' }, [
    h('caption', { text: caption }),
    h('thead', null, h('tr', null, ['Nawóz', 'kg/ha', 'Razem kg', 'Worki 50 kg'].map((t) => h('th', { scope: 'col', text: t })))),
    h('tbody', null, list.map((p) => h('tr', null, [h('th', { scope: 'row' }, [p.name, h('span', { class: 'mr-muted', text: ` (${p.content})` })]), h('td', { text: num(p.kg_ha, 0) }), h('td', { text: num(p.kg_total, 0) }), h('td', { text: String(p.bags_50kg) })]))),
  ]));
}

function append(el, kids) {
  kids.forEach((k) => { if (k) el.appendChild(k); });
}

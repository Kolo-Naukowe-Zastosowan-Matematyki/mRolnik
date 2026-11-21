// Kalkulatory → „Kalkulator zysku”: rolnik wybiera uprawy na swoich działkach (i może poprawić plon, cenę, koszty),
// serwer liczy wynik każdej działki i całego gospodarstwa (/api/calculators/profit). Puste pole = wartość z GUS / ODR.
import { h, icon, replace } from '../dom.js';
import { state } from '../state.js';
import { num, provChip, errorView, loadingView } from '../format.js';
import { farmParcels, farmVoivodeship, loadCatalog, loadProfit, zl } from '../profit.js';

let builtFor = null;
const OTHER = '';

function farmKey(a) {
  return a && Array.isArray(a.parcels) ? `${a.generated_at || ''}|${a.parcels.map((p) => `${p.id_tag}:${p.area_ha}:${p.crop}`).join(',')}` : '';
}

function numBox(label, ph, step) {
  return h('input', { type: 'number', class: 'mr-input mr-profit__in', inputmode: 'decimal', min: '0', step, placeholder: ph, 'aria-label': label });
}

function guessKey(catalog, crop) {
  const s = String(crop || '').toLowerCase();
  if (!s) return OTHER;
  const exact = catalog.find((c) => c.name.toLowerCase() === s);
  if (exact) return exact.key;
  const first = s.split(/\s+/)[0].slice(0, 5);
  const jara = s.includes('jar');
  const cands = catalog.filter((c) => c.name.toLowerCase().startsWith(first));
  return (cands.find((c) => c.name.toLowerCase().includes(jara ? 'jar' : 'ozim')) || cands[0] || { key: OTHER }).key;
}

function build(box) {
  const a = state.analysis;
  const parcels = farmParcels(a);
  const voiv = farmVoivodeship(a);
  builtFor = farmKey(a);
  if (!parcels.length) { replace(box, h('p', { class: 'mr-muted', text: 'Najpierw wybierz gospodarstwo albo dodaj działki – kalkulator liczy dla Twoich działek.' })); return; }
  if (!voiv.name) { replace(box, h('p', { class: 'mr-muted', text: 'Nie znamy województwa działek – plony i ceny GUS są wojewódzkie.' })); return; }
  replace(box, loadingView('Wczytywanie plonów i cen GUS oraz kosztów ODR…'));
  const own = h('input', { type: 'checkbox', class: 'mr-check', id: 'mr-profit-own' });
  const draw = (cat) => {
    const byKey = new Map(cat.crops.map((c) => [c.key, c]));
    const result = h('div', { class: 'mr-profit__result', tabindex: '-1', 'aria-live': 'polite' });
    const rows = parcels.map((p) => {
      const sel = h('select', { class: 'mr-select mr-profit__crop', 'aria-label': `Uprawa – ${p.id}` }, [
        ...cat.crops.map((c) => h('option', { value: c.key, text: c.name })),
        h('option', { value: OTHER, text: p.crop && guessKey(cat.crops, p.crop) === OTHER ? `${p.crop} (wpisz wartości)` : 'inna uprawa (wpisz wartości)' }),
      ]);
      sel.value = guessKey(cat.crops, p.crop);
      const y = numBox(`Plon [dt/ha] – ${p.id}`, '', '0.1');
      const pr = numBox(`Cena [zł/dt] – ${p.id}`, '', '0.01');
      const c = numBox(`Koszty [zł/ha] – ${p.id}`, '', '1');
      const sync = () => {
        const ref = byKey.get(sel.value);
        y.placeholder = ref && ref.yield_dt_ha !== null ? num(ref.yield_dt_ha, 1) : 'wpisz';
        pr.placeholder = ref && ref.price_zl_dt !== null ? num(ref.price_zl_dt, 2) : 'wpisz';
        c.placeholder = ref ? zl(ref.cost_zl_ha) : 'wpisz';
      };
      sel.addEventListener('change', sync);
      sync();
      const tr = h('tr', null, [
        h('th', { scope: 'row' }, [p.id, h('span', { class: 'mr-plantable__sub', text: `${num(p.area_ha, 2)} ha` })]),
        h('td', { class: 'mr-profit__cropcell' }, sel), h('td', { dataset: { label: 'Plon [dt/ha]' } }, y),
        h('td', { dataset: { label: 'Cena [zł/dt]' } }, pr), h('td', { dataset: { label: 'Koszty [zł/ha]' } }, c),
      ]);
      return { p, sel, y, pr, c, tr };
    });
    const val = (el) => (el.value.trim() === '' ? null : Number(el.value));
    const calc = () => {
      const body = rows.map((r) => ({
        id: r.p.id, area_ha: r.p.area_ha, crop: r.sel.value ? byKey.get(r.sel.value).name : r.p.crop,
        crop_key: r.sel.value || null, yield_dt_ha: val(r.y), price_zl_dt: val(r.pr), cost_zl_ha: val(r.c),
      }));
      replace(result, loadingView('Liczenie…'));
      loadProfit(body, voiv.name, { services: !own.checked }).then((d) => {
        const sum = d.rows.reduce((acc, r) => ({ rev: acc.rev + (r.revenue_zl || 0), cost: acc.cost + (r.direct_cost_zl || 0) }), { rev: 0, cost: 0 });
        replace(result, [
          h('p', { class: 'mr-result__big' }, [h('strong', { class: d.total_margin_zl < 0 ? 'mr-plantable__neg' : '', text: `${zl(d.total_margin_zl)} zł` }),
            ` – wynik gospodarstwa w sezonie${d.incomplete ? ` (bez ${d.incomplete} dz. bez danych)` : ''}`]),
          h('div', { class: 'mr-tablewrap', role: 'region', 'aria-label': 'Wynik działek', tabindex: '0' }, h('table', { class: 'mr-table mr-plantable' }, [
            h('thead', null, h('tr', null, ['Działka', 'Uprawa', 'Przychód', 'Koszty', 'Wynik'].map((t, i) => h('th', { scope: 'col', class: i > 1 ? 'mr-num' : null, text: t })))),
            h('tbody', null, d.rows.map((r) => h('tr', null, [
              h('th', { scope: 'row', text: String(r.id) }),
              h('td', null, [r.crop || '–', r.note ? h('span', { class: 'mr-plantable__sub', text: r.note }) : null]),
              h('td', { class: 'mr-num', text: r.revenue_zl === undefined ? '–' : `${zl(r.revenue_zl)} zł` }),
              h('td', { class: 'mr-num', text: r.direct_cost_zl === undefined ? '–' : `${zl(r.direct_cost_zl)} zł` }),
              h('td', { class: `mr-num${r.margin_zl < 0 ? ' mr-plantable__neg' : ''}`, text: r.margin_zl === undefined ? '–' : `${zl(r.margin_zl)} zł` }),
            ]))),
            h('tfoot', null, h('tr', null, [h('th', { scope: 'row', text: 'Razem' }), h('td'),
              h('td', { class: 'mr-num', text: `${zl(sum.rev)} zł` }), h('td', { class: 'mr-num', text: `${zl(sum.cost)} zł` }),
              h('td', { class: 'mr-num' }, h('strong', { text: `${zl(d.total_margin_zl)} zł` }))])),
          ])),
        ]);
        result.focus();
      }).catch((e) => replace(result, errorView(e, calc, 'Nie udało się policzyć')));
    };
    own.onchange = () => {
      loadCatalog(voiv.name, !own.checked).then((c2) => {
        c2.crops.forEach((x) => byKey.set(x.key, x));
        rows.forEach((r) => r.sel.dispatchEvent(new Event('change')));
        calc();
      });
    };
    replace(box, [
      h('p', { class: 'mr-hint', text: `Wybierz uprawę na każdej działce. Puste pola = wartości domyślne: plon GUS (średnia 5 lat, woj. ${voiv.name}), `
        + 'cena skupu GUS z ostatniego roku, koszty z kalkulacji Pomorskiego ODR 2025 (koszty bezpośrednie + usługi). Wpisz własne liczby, jeśli je znasz.' }),
      h('div', { class: 'mr-tablewrap', role: 'region', 'aria-label': 'Uprawy na działkach', tabindex: '0' }, h('table', { class: 'mr-table mr-profit__table' }, [
        h('thead', null, h('tr', null, ['Działka', 'Uprawa', 'Plon [dt/ha]', 'Cena [zł/dt]', 'Koszty [zł/ha]'].map((t) => h('th', { scope: 'col', text: t })))),
        h('tbody', null, rows.map((r) => r.tr)),
      ])),
      h('div', { class: 'mr-field mr-field--check' }, [own, h('label', { for: 'mr-profit-own', text: 'Mam własne maszyny – nie licz kosztów usług (orka, siew, opryski, zbiór)' })]),
      h('div', { class: 'mr-form__actions' }, h('button', { type: 'button', class: 'mr-btn mr-btn--primary', onclick: calc }, [icon('calc'), 'Policz zysk'])),
      result,
      h('div', { class: 'mr-chips' }, [cat.provenance && cat.provenance.yields, cat.provenance && cat.provenance.prices, cat.provenance && cat.provenance.costs]
        .filter(Boolean).map((x) => provChip(x))),
    ]);
    calc();
  };
  loadCatalog(voiv.name, true).then(draw).catch((e) => replace(box, errorView(e, () => build(box), 'Nie udało się wczytać danych')));
}

/** Rozwijana karta „Kalkulator zysku”; buduje się przy otwarciu i po zmianie gospodarstwa. */
export function profitSection() {
  const box = h('div', { class: 'mr-profit' });
  const det = h('details', { class: 'mr-details mr-details--card mr-wide', id: 'mr-profit-card' }, [h('summary', null, [icon('price'), 'Kalkulator zysku']), box]);
  det.addEventListener('toggle', () => { if (det.open && builtFor !== farmKey(state.analysis)) build(box); });
  det._refresh = () => { if (det.open && builtFor !== farmKey(state.analysis)) build(box); };
  return det;
}

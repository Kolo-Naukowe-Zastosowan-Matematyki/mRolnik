// Plony → „Optymalna konfiguracja upraw na Twoich działkach”: na każdej działce inna uprawa niż teraz (zmianowanie),
// warunki normy GAEC 7 (dywersyfikacja upraw) i największy wynik: plon GUS × cena skupu GUS − koszty ODR 2025.
// Obliczenia po stronie serwera (/api/calculators/optimal-plan).
import { h, icon, replace, infoTip } from '../dom.js';
import { num, provChip, errorView } from '../format.js';
import { skeleton } from './field.js';
import { farmParcels, farmVoivodeship, loadPlan, zl, zlSigned } from '../profit.js';

const opts = { services: true, allowed: null };   // wybór rolnika – zostaje po przełączeniu zakładki
const byName = new Intl.Collator('pl', { numeric: true, sensitivity: 'base' });

function checksList(checks) {
  return h('ul', { class: 'mr-plancheck' }, checks.map((c) => h('li', { class: `mr-plancheck__i mr-plancheck__i--${c.ok ? 'ok' : 'bad'}` }, [
    h('span', { class: 'mr-plancheck__mark', 'aria-hidden': 'true', text: c.ok ? '✓' : '✗' }),
    h('span', { class: 'mr-visually-hidden', text: c.ok ? 'spełnione: ' : 'niespełnione: ' }), c.text,
  ])));
}

function cropPicker(catalog, onChange) {
  const allowed = new Set(opts.allowed || catalog.filter((c) => !c.contract).map((c) => c.key));
  const box = h('details', { class: 'mr-details mr-planopts__crops' }, [
    h('summary', { text: `Uprawy brane pod uwagę (${allowed.size} z ${catalog.length})` }),
    h('ul', { class: 'mr-planopts__list' }, catalog.map((c) => {
      const id = `mr-plan-crop-${c.key}`;
      return h('li', null, [
        h('input', { type: 'checkbox', class: 'mr-check', id, checked: allowed.has(c.key),
          onchange: (e) => {
            if (e.target.checked) allowed.add(c.key); else allowed.delete(c.key);
            opts.allowed = Array.from(allowed);
            onChange();
          } }),
        h('label', { for: id }, [c.name, c.contract ? h('span', { class: 'mr-muted', text: ' – wymaga umowy z cukrownią' }) : null]),
      ]);
    })),
  ]);
  return box;
}

function planTable(d) {
  const names = new Map((d.catalog || []).map((c) => [c.key, c]));
  const rows = d.rows.slice().sort((a, b) => byName.compare(String(a.id), String(b.id)));
  return h('div', { class: 'mr-tablewrap', role: 'region', 'aria-labelledby': 'mr-crops-h', tabindex: '0' }, h('table', { class: 'mr-table mr-plantable' }, [
    h('caption', { class: 'mr-visually-hidden', text: 'Proponowane uprawy na działkach i wynik na sezon' }),
    h('thead', null, h('tr', null, [
      h('th', { scope: 'col', text: 'Działka' }), h('th', { scope: 'col', text: 'Teraz' }), h('th', { scope: 'col', text: 'Proponowana uprawa' }),
      h('th', { scope: 'col', class: 'mr-num', text: 'Wynik na 1 ha' }), h('th', { scope: 'col', class: 'mr-num', text: 'Wynik działki' }),
    ])),
    h('tbody', null, rows.map((r) => {
      const c = names.get(r.plan);
      return h('tr', null, [
        h('th', { scope: 'row' }, [String(r.id), h('span', { class: 'mr-plantable__sub', text: `${num(r.area_ha, 2)} ha` })]),
        h('td', { text: r.crop || 'nieznana' }),
        r.kept
          ? h('td', { class: 'mr-muted', text: 'bez zmian (nie grunt orny)' })
          : h('td', null, [h('strong', { text: r.plan_name || r.plan }), c && c.legume ? h('span', { class: 'mr-plantable__tag', text: 'bobowata – wzbogaca glebę w azot' }) : null]),
        h('td', { class: 'mr-num', text: r.margin_zl_ha === null || r.margin_zl_ha === undefined ? '–' : `${zl(r.margin_zl_ha)} zł` }),
        h('td', { class: `mr-num${r.margin_zl < 0 ? ' mr-plantable__neg' : ''}`, text: r.margin_zl === null || r.margin_zl === undefined ? '–' : `${zl(r.margin_zl)} zł` }),
      ]);
    })),
  ]));
}

export function planCard(a, isCurrent) {
  const body = h('div', { class: 'mr-card__body', 'aria-busy': 'true' }, [h('p', { class: 'mr-muted', role: 'status', text: 'Liczenie planu upraw (plony i ceny GUS, koszty ODR)…' }), skeleton(3)]);
  const card = h('section', { class: 'mr-card mr-wide', 'aria-labelledby': 'mr-crops-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-crops-h' }, [icon('leaf'), 'Optymalna konfiguracja upraw na Twoich działkach ',
      infoTip('optymalny plan upraw', 'Na każdej działce proponujemy inną uprawę niż obecna (zmianowanie chroni glebę i ogranicza choroby). '
        + 'Przy gruntach ornych powyżej 10 ha plan spełnia normę GAEC 7: co najmniej 3 uprawy, największa do 65%, dwie największe do 90%. '
        + 'Spośród takich planów wybieramy ten z największym wynikiem: plon (GUS, średnia 5 lat dla województwa) × cena skupu (GUS) '
        + '− koszty bezpośrednie i usługi (kalkulacje Pomorskiego ODR 2025). Bez dopłat.')]),
    body]);
  const parcels = farmParcels(a);
  const voiv = farmVoivodeship(a);
  const run = () => {
    if (!parcels.length) { body.removeAttribute('aria-busy'); replace(body, h('p', { class: 'mr-muted', text: 'Brak powierzchni działek – nie da się policzyć planu.' })); return; }
    if (!voiv.name) { body.removeAttribute('aria-busy'); replace(body, h('p', { class: 'mr-muted', text: 'Nie znamy województwa działek – plony i ceny GUS są wojewódzkie.' })); return; }
    body.setAttribute('aria-busy', 'true');
    loadPlan(parcels, voiv.name, opts).then((d) => {
      if (!isCurrent()) return;
      body.removeAttribute('aria-busy');
      const diff = d.current_margin_zl === null || d.current_margin_zl === undefined ? null : d.total_margin_zl - d.current_margin_zl;
      const services = h('input', { type: 'checkbox', class: 'mr-check', id: 'mr-plan-own', checked: !opts.services,
        onchange: (e) => { opts.services = !e.target.checked; run(); } });
      replace(body, [
        h('div', { class: 'mr-plansum' }, [
          h('p', { class: 'mr-plansum__big' }, [h('span', { class: 'mr-plansum__lbl', text: 'Wynik planu na sezon' }),
            h('strong', { class: d.total_margin_zl < 0 ? 'mr-plantable__neg' : '', text: `${zl(d.total_margin_zl)} zł` })]),
          diff !== null ? h('p', { class: 'mr-plansum__big' }, [h('span', { class: 'mr-plansum__lbl', text: 'Obecne uprawy' }),
            h('strong', { text: `${zl(d.current_margin_zl)} zł` })]) : null,
          diff !== null ? h('p', { class: `mr-plansum__big mr-plansum__diff mr-plansum__diff--${diff >= 0 ? 'up' : 'down'}` }, [h('span', { class: 'mr-plansum__lbl', text: 'Różnica' }),
            h('strong', { text: zlSigned(diff) })]) : null,
        ]),
        planTable(d),
        h('h3', { class: 'mr-metric__title', text: 'Dobre praktyki i warunki (norma GAEC 7)' }),
        checksList(d.checks || []),
        ...(d.notes || []).map((n) => h('p', { class: 'mr-callout mr-callout--warn' }, [icon('warn'), n])),
        h('div', { class: 'mr-planopts' }, [
          h('div', { class: 'mr-field mr-field--check' }, [services, h('label', { for: 'mr-plan-own', text: 'Mam własne maszyny – nie licz kosztów usług (orka, siew, opryski, zbiór)' })]),
          cropPicker(d.catalog || [], run),
        ]),
        h('p', { class: 'mr-hint', text: `Wynik = plon × cena − koszty bezpośrednie${opts.services ? ' − usługi mechaniczne' : ''}, bez dopłat i kosztów ogólnych. `
          + `Plon: GUS, średnia 5 lat dla woj. ${d.voivodeship}; cena: GUS, cena skupu z ostatniego roku (bobowate – cena z kalkulacji ODR); `
          + 'koszty: kalkulacje Pomorskiego ODR 2025 (wariant średniointensywny). Uprawy wieloletnie i trwałe użytki zielone zostają bez zmian.'
          + (voiv.others.length ? ` Część działek leży w innym województwie (${voiv.others.join(', ')}) – liczymy według województwa z największą powierzchnią.` : '') }),
        h('div', { class: 'mr-chips' }, [d.provenance && d.provenance.yields, d.provenance && d.provenance.prices, d.provenance && d.provenance.costs].filter(Boolean).map((x) => provChip(x))),
      ]);
    }).catch((e) => {
      if (!isCurrent()) return;
      body.removeAttribute('aria-busy');
      replace(body, errorView(e, run, 'Nie udało się policzyć planu'));
    });
  };
  run();
  return card;
}

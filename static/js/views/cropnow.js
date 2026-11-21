// Zakładka „Plony” – kafelki u góry:
// 1) „Plony – co zrobić teraz”: siew, nawożenie azotem, opryski, zbiór – tylko z progów, które mają źródło
//    (przepisy, zalecenia ODR, prognoza, zdjęcia satelitarne),
// 2) „Ceny skupu” upraw z działek (GUS BDL, województwo): cena z ostatniego roku, zmiany rok do roku za 5 lat
//    i cena sprzed 5 lat – szeroki kafelek (dwie kolumny siatki).
import { h, icon, replace } from '../dom.js';
import { state } from '../state.js';
import { num, provChip, errorView, fmtDate } from '../format.js';
import { skeleton } from './field.js';
import { loadRules } from './rules.js';
import { priceReference } from '../reference.js';
const isNum = (v) => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
const nameOf = (p, i) => p.id_tag || `Działka ${i + 1}`;
const lc = (s) => String(s || '').toLowerCase();
const AUTUMN = ['ozim', 'rzepak'];
const PERENNIAL = ['tuz', 'trwały użytek', 'łąk', 'pastwisk', 'sad', 'wieloletn'];


/** Uprawy z działek: [{crop, parcels:[p], ha}] w kolejności malejącej powierzchni. */
function crops(parcels) {
  const m = new Map();
  parcels.forEach((p, i) => {
    const c = (p.crop || '').trim() || 'uprawa nieznana';
    const k = c.toLowerCase();
    if (!m.has(k)) m.set(k, { crop: c, parcels: [], names: [], ha: 0 });
    const g = m.get(k);
    g.parcels.push(p); g.names.push(nameOf(p, i)); g.ha += isNum(p.area_ha) ? Number(p.area_ha) : 0;
  });
  return Array.from(m.values()).sort((a, b) => b.ha - a.ha);
}

// --- pomocnicze: stan gleby (dziś) --------------------------------------------------------------
function soilToday(rules) {
  const days = rules && rules.sowing && Array.isArray(rules.sowing.daily) ? rules.sowing.daily : [];
  const past = days.filter((d) => !d.forecast && isNum(d.soil_t_c));
  return past.length ? past[past.length - 1] : null;
}

function saturated(parcels) {
  return parcels.filter((p) => {
    const sm = p.soil_moisture;
    return sm && isNum(sm.surface_pct) && isNum(sm.field_capacity_pct) && Number(sm.surface_pct) >= Number(sm.field_capacity_pct);
  });
}

// --- 1. Siew -------------------------------------------------------------------------------------------
function sowingFor(crop, rules, soil) {
  const c = lc(crop);
  const thr = rules && rules.sowing && Array.isArray(rules.sowing.thresholds) ? rules.sowing.thresholds : [];
  const t = thr.find((x) => (x.crop === 'kukurydza' && c.includes('kukurydz')) || (x.crop === 'soja' && (c.includes('soj') || c.includes('soi'))));
  const m = new Date().getMonth() + 1;
  if (t && (m < 3 || m > 6)) return { cls: 'none', text: `wiosną (wg ODR: ${t.crop === 'kukurydza' ? 'najlepiej ok. 15 kwietnia' : 'koniec kwietnia – początek maja'}), gdy gleba ma co najmniej ${num(t.min_c, 0)}°C` };
  if (t) {
    const days = (rules.sowing.daily || []).filter((d) => d.forecast && isNum(d.soil_t_c) && d.soil_t_c >= t.min_c);
    if (!soil) return { cls: 'none', text: `próg siewu ${num(t.min_c, 0)}°C (ODR), brak temperatury gleby` };
    if (soil.soil_t_c >= t.min_c) return { cls: 'ok', text: `gleba ${num(soil.soil_t_c, 1)}°C – próg siewu ${num(t.min_c, 0)}${t.max_c ? `–${num(t.max_c, 0)}` : ''}°C (ODR) osiągnięty` };
    return { cls: 'warn', text: `gleba ${num(soil.soil_t_c, 1)}°C – za zimno, próg ${num(t.min_c, 0)}${t.max_c ? `–${num(t.max_c, 0)}` : ''}°C (ODR)${days.length ? `; w prognozie od ${fmtDate(days[0].date)}` : '; nie w najbliższych 14 dniach'}` };
  }
  if (AUTUMN.some((k) => c.includes(k))) return { cls: 'none', text: 'jesienią; urzędowego progu siewu nie mamy – sprawdź zalecenia ODR dla swojego regionu' };
  if (PERENNIAL.some((k) => c.includes(k))) return { cls: 'none', text: 'nie dotyczy – uprawa wieloletnia' };
  return { cls: 'none', text: 'w dokumentach ODR nie ma progu temperatury gleby – oceniaj stan gleby na polu' };
}

// --- 2. Nawożenie azotem (program azotanowy) ------------------------------------------------------------
function fertilizing(rules, soil, wet) {
  const n = rules && rules.nitrates;
  if (!n) return { cls: 'none', head: 'Brak danych', text: 'Nie udało się pobrać terminów programu azotanowego.' };
  if (soil && soil.soil_t_c <= 0) return { cls: 'bad', head: 'Nie teraz', text: `Gleba może być zamarznięta (6 cm: ${num(soil.soil_t_c, 1)}°C, model) – na zamarzniętą glebę nawozów nie stosuje się.` };
  if (wet.length && n.status !== 'zakaz') return { cls: 'bad', head: 'Nie teraz', text: 'Gleba nasycona wodą – takiej gleby nie nawozi się (program azotanowy). Poczekaj, aż obeschnie.' };
  const w = Array.isArray(n.windows) && n.windows[0] ? n.windows[0] : null;
  const y = new Date().getFullYear();
  const short = {
    dozwolone: ['ok', 'Można', `Nawozy azotowe na gruntach ornych do ${w ? fmtDate(w.to) : '20 października'}, jeśli gleba nie jest zamarznięta, zalana ani nasycona wodą.`],
    tylko_stale: ['warn', 'Tylko obornik', 'Termin nawozów mineralnych i gnojowicy minął; obornik (nawozy naturalne stałe) – do 31 października.'],
    luty_warunkowo: ['warn', 'Warunkowo', `Od 1 lutego tylko po 5 dniach ze średnią temperaturą powyżej ${num(n.temp_threshold_c, 0)}°C (datę dla powiatu podaje IMGW-PIB).`],
    zakaz: ['bad', 'Zakaz', `Nawozów azotowych na gruntach ornych nie stosuje się do 1 marca ${new Date().getMonth() >= 9 ? y + 1 : y} r. (od 1 lutego – po ociepleniu).`],
  };
  const [cls, head, text] = short[n.status] || ['none', 'Sprawdź', n.text];
  return { cls, head, text };
}

// --- 3. Opryski (§ 3 rozp. MRiRW: wiatr do 4 m/s, z techniką ograniczającą znoszenie do 5 m/s) --------------
function spraying(rules) {
  const hrs = rules && rules.spraying && Array.isArray(rules.spraying.hourly) ? rules.spraying.hourly.filter((x) => x.daylight) : [];
  if (!hrs.length) return { cls: 'none', head: 'Brak prognozy', text: 'Brak prognozy wiatru – sprawdź wiatr przed zabiegiem.' };
  const now = Date.now();
  const in24 = hrs.filter((x) => new Date(x.time).getTime() - now < 24 * 3600e3);
  const ok24 = in24.filter((x) => x.allowed === 'tak').length;
  const ok72 = hrs.filter((x) => x.allowed === 'tak' && new Date(x.time).getTime() - now < 72 * 3600e3).length;
  const first = hrs.find((x) => x.allowed === 'tak');
  const fmt = (iso) => new Intl.DateTimeFormat('pl-PL', { weekday: 'long', hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
  if (ok24) return { cls: 'ok', head: 'Można', text: `Wiatr do 4 m/s przez ${ok24} godz. w dzień w ciągu doby (prognoza). Temperaturę i porę zabiegu podaje etykieta środka.` };
  if (first) return { cls: 'warn', head: 'Później', text: `Najbliższe okno z wiatrem do 4 m/s: ${fmt(first.time)} (w 3 dniach: ${ok72} godz.).` };
  return { cls: 'bad', head: 'Nie teraz', text: 'W najbliższych dniach wiatr powyżej 4 m/s w ciągu dnia – zabieg tylko z techniką ograniczającą znoszenie (do 5 m/s) albo później.' };
}

// --- 4. Zbiór: sygnał z satelity (spadek NDVI po szczycie sezonu), nie termin zbioru ----------------------
function senescence(p) {
  const rows = p.history && Array.isArray(p.history.ndvi_monthly) ? p.history.ndvi_monthly.filter((r) => isNum(r.mean)) : [];
  if (rows.length < 3) return null;
  const last = rows[rows.length - 1];
  const y = last.month.slice(0, 4);
  const season = rows.filter((r) => r.month.slice(0, 4) === y && Number(r.month.slice(5, 7)) >= 4);
  if (!season.length) return null;
  const peak = Math.max(...season.map((r) => Number(r.mean)));
  return { peak, last: Number(last.mean), drop: peak >= 0.6 && Number(last.mean) <= peak * 0.65, month: last.month };
}

function harvest(parcels) {
  const m = new Date().getMonth() + 1;
  if (m <= 5 || m >= 11) return { cls: 'none', head: 'Poza sezonem', text: m >= 11 ? 'Zbiory w tym sezonie za nami; na zdjęciach satelitarnych widać tylko oziminy i międzyplony.' : 'Rośliny rosną – na zbiór za wcześnie.' };
  const dry = parcels.map((p, i) => ({ p, i, s: senescence(p) })).filter((x) => x.s && x.s.drop);
  if (!dry.length) return { cls: 'none', head: 'Jeszcze nie', text: 'Zdjęcia satelitarne nie pokazują zasychania łanu na działkach.' };
  return { cls: 'ok', head: 'Sprawdź na polu', text: `Łan zasycha (NDVI spadło o ponad 1/3 od szczytu sezonu): ${dry.map((x) => nameOf(x.p, x.i)).join(', ')}. To sygnał dojrzewania – o terminie decyduje wilgotność ziarna na polu.` };
}

function statusBox(ico, title, st) {
  return h('li', { class: `mr-todo__item mr-todo__item--${st.cls}` }, [
    h('p', { class: 'mr-todo__head' }, [icon(ico), h('span', { text: title })]),
    h('p', { class: 'mr-todo__state', text: st.head }),
    h('p', { class: 'mr-todo__text', text: st.text }),
  ]);
}

function nowTile(a, sel, isCurrent) {
  const body = h('div', { class: 'mr-card__body', 'aria-busy': 'true' }, [h('p', { class: 'mr-muted', role: 'status', text: 'Wczytywanie prognozy i terminów…' }), skeleton(3)]);
  const card = h('section', { class: 'mr-card', 'aria-labelledby': 'mr-cropnow-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-cropnow-h' }, [icon('leaf'), 'Plony – co zrobić teraz']), body]);
  const run = () => {
    loadRules(sel).then((d) => {
      if (!isCurrent()) return;
      body.removeAttribute('aria-busy');
      const soil = soilToday(d);
      const wet = saturated(a.parcels);
      const groups = crops(a.parcels);
      const sow = groups.map((g) => ({ g, s: sowingFor(g.crop, d, soil) }));
      const sowOk = sow.filter((x) => x.s.cls === 'ok');
      const sowWarn = sow.filter((x) => x.s.cls === 'warn');
      const sowing = wet.length
        ? { cls: 'bad', head: 'Nie teraz', text: 'Gleba nasycona wodą – wjazd ciężkim sprzętem ugniata glebę.' }
        : sowOk.length ? { cls: 'ok', head: 'Można', text: `Gleba dość ciepła dla: ${sowOk.map((x) => x.g.crop).join(', ')}.` }
          : sowWarn.length ? { cls: 'warn', head: 'Za zimno', text: `Gleba za zimna dla: ${sowWarn.map((x) => x.g.crop).join(', ')}.` }
            : { cls: 'none', head: 'Poza sezonem', text: `Teraz nie ma siewu ocenianego przez nas (progi ODR dla kukurydzy i soi – wiosną).${soil ? ` Gleba 6 cm: ${num(soil.soil_t_c, 1)}°C (model).` : ''}` };
      replace(body, [
        h('ul', { class: 'mr-todo' }, [
          statusBox('leaf', 'Siew', sowing),
          statusBox('drop', 'Nawożenie azotem', fertilizing(d, soil, wet)),
          statusBox('wind', 'Opryski', spraying(d)),
          statusBox('sun', 'Zbiór', harvest(a.parcels)),
        ]),
      ]);
    }).catch((e) => {
      if (!isCurrent()) return;
      body.removeAttribute('aria-busy');
      replace(body, errorView(e, run, 'Brak danych'));
    });
  };
  run();
  return card;
}

// --- Kafelek 2: ceny skupu upraw z działek (GUS BDL) i zmiana do roku poprzedniego -------------------------------
const DEFAULT_PRICE_CROPS = ['Pszenica', 'Jęczmień', 'Kukurydza', 'Rzepak', 'Ziemniaki'];
const cap = (s) => (s ? `${s[0].toUpperCase()}${s.slice(1)}` : s);
const zl = (v) => new Intl.NumberFormat('pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(v));

function changeCell(c, row) {
  if (!row.available || !c || !isNum(c.change)) return h('td', { class: 'mr-muted', text: '–' });
  const d = Number(c.change);
  const dir = d > 0 ? 'up' : d < 0 ? 'down' : 'flat';
  const sign = d > 0 ? '+' : d < 0 ? '−' : '±';
  const pct = isNum(c.change_pct) ? `${sign}${num(Math.abs(c.change_pct), 1)}%` : null;
  const zlTxt = `${sign}${zl(Math.abs(d))} zł`;
  return h('td', { class: `mr-pricechg mr-pricechg--${dir}`, title: `${c.year} do ${c.prev_year}: ${zlTxt}${pct ? ` (${pct})` : ''}` }, [
    h('span', { class: 'mr-pricechg__pct' }, [h('span', { 'aria-hidden': 'true', text: dir === 'up' ? '▲ ' : dir === 'down' ? '▼ ' : '' }), pct || zlTxt]),
    pct ? h('span', { class: 'mr-pricechg__zl', text: zlTxt }) : null,
  ]);
}

function priceTable(d) {
  const rows = Array.isArray(d.rows) ? d.rows : [];
  const ok = rows.filter((r) => r.available);
  const last = ok.length ? Math.max(...ok.map((r) => r.year)) : null;
  const stale = ok.some((r) => r.year !== last);
  const n = ok.length ? Math.max(...ok.map((r) => (Array.isArray(r.changes) ? r.changes.length : 0))) : 0;
  const yrs = last ? Array.from({ length: n }, (_, i) => last - i) : [];
  const base = last ? last - n : null;
  const vname = d.voivodeship && d.voivodeship.name;
  const two = (a, b) => h('th', { scope: 'col' }, [a, h('span', { class: 'mr-pricetable__sub', text: b })]);
  return [
    h('div', { class: 'mr-tablewrap', role: 'region', 'aria-labelledby': 'mr-cropprice-h', tabindex: '0' }, h('table', { class: 'mr-table mr-pricetable' }, [
      h('caption', { class: 'mr-visually-hidden', text: `Ceny skupu GUS${vname ? `, województwo ${vname}` : ''}, zł za 1 dt` }),
      h('thead', null, h('tr', null, [
        h('th', { scope: 'col', text: 'Produkt' }),
        two(last ? `Cena ${last}` : 'Cena', '[zł/dt]'),
        ...yrs.map((y) => two(String(y), `do ${y - 1}`)),
        base ? two(`Cena ${base}`, '[zł/dt]') : null,
      ])),
      h('tbody', null, rows.map((r) => {
        const label = r.product ? cap(r.product) : r.crops[0];
        const mine = r.product && r.crops.some((c) => c.toLowerCase() !== r.product.toLowerCase()) ? r.crops.join(', ') : null;
        const byYear = new Map((r.changes || []).map((c) => [c.year, c]));
        const offset = r.available ? last - r.year : 0;   // gdy GUS ma dla tej uprawy starszy ostatni rok
        return h('tr', null, [
          h('th', { scope: 'row' }, [label, mine ? h('span', { class: 'mr-pricetable__crops', text: mine }) : null,
            r.available && r.scope === 'Polska' ? h('span', { class: 'mr-pricetable__crops', text: 'cena dla Polski' }) : null]),
          r.available
            ? h('td', null, [h('strong', { text: offset ? '–' : zl(r.price) })])
            : h('td', { class: 'mr-muted mr-pricetable__na', colspan: String(yrs.length + 2), text: r.variable_id ? 'brak danych w GUS' : 'GUS nie podaje ceny skupu tej uprawy' }),
          ...(r.available ? yrs.map((y) => changeCell(byYear.get(y), r)) : []),
          r.available && base ? h('td', null, isNum(r.base_price) && !offset ? h('strong', { text: zl(r.base_price) }) : h('span', { class: 'mr-muted', text: '–' })) : null,
        ]);
      })),
    ])),
    h('p', { class: 'mr-hint', text: `Średnie roczne ceny skupu wg GUS${vname ? ` dla woj. ${vname}` : ''}, zł za 1 dt (100 kg). `
      + 'Kolumny z latami pokazują zmianę ceny w porównaniu z rokiem poprzednim (w % i w zł). '
      + `${stale ? 'Kreska – GUS nie podał ceny za ten rok. ' : ''}Cena przy sprzedaży z własnego gospodarstwa może być inna.` }),
    provChip(d.provenance),
  ];
}

function pricesTile(a, sel, isCurrent) {
  const body = h('div', { class: 'mr-card__body', 'aria-busy': 'true' }, [h('p', { class: 'mr-muted', role: 'status', text: 'Wczytywanie cen GUS…' }), skeleton(3)]);
  const card = h('section', { class: 'mr-card mr-card--wide2', 'aria-labelledby': 'mr-cropprice-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-cropprice-h' }, [icon('price'), 'Ceny skupu']), body]);
  const names = crops(a.parcels).map((g) => g.crop).filter((c) => c !== 'uprawa nieznana');
  const list = names.length ? names : DEFAULT_PRICE_CROPS;
  const vp = [sel, ...a.parcels].find((p) => p && p.voivodeship);
  const run = () => {
    priceReference(list, vp ? vp.voivodeship : null, sel && sel.centroid).then((d) => {
      if (!isCurrent()) return;
      body.removeAttribute('aria-busy');
      replace(body, [names.length ? null : h('p', { class: 'mr-muted', text: 'Na działkach nie wpisano upraw – pokazujemy główne produkty.' }), ...priceTable(d)]);
    }).catch((e) => {
      if (!isCurrent()) return;
      body.removeAttribute('aria-busy');
      replace(body, errorView(e, run, 'Brak cen'));
    });
  };
  run();
  return card;
}

/** Kafelki u góry zakładki Plony: „co zrobić teraz” i szeroka tabela cen skupu (zajęła miejsce komunikatów). */
export function cropNowTiles(a, isCurrent) {
  const sel = a.parcels[Math.max(0, Math.min(state.selected < 0 ? 0 : state.selected, a.parcels.length - 1))];
  return [nowTile(a, sel, isCurrent), pricesTile(a, sel, isCurrent)];
}

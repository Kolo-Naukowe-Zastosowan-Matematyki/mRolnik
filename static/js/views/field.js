// Zakładka „Pole”: „Dziś na polu” (zalecenia prostym językiem) + lista działek (tekstowa alternatywa mapy).
import { h, icon, replace, infoTip, nextId } from '../dom.js';
import { state, emit, CUSTOM_FARM } from '../state.js';
import { val, num, provChip, errorView, emptyView, fmtDay, fmtDate } from '../format.js';
import { stateIcons, stateLines, plantState, waterState } from '../parcelstate.js';
import { indexHelp, indexTip } from '../indices.js';
import { api } from '../api.js';
import { soilGauge } from './water.js';
import { countySection } from './county.js';

export function skeleton(lines = 3) {
  return h('div', { class: 'mr-skel', 'aria-hidden': 'true' }, Array.from({ length: lines }, (_, i) => h('span', { class: `mr-skel__line mr-skel__line--${i % 3}` })));
}

export function renderField(panel) {
  if (state.loading) {
    replace(panel, [
      h('section', { class: 'mr-card mr-card--today', 'aria-busy': 'true' }, [
        h('h2', { class: 'mr-card__title' }, [icon('sun'), 'Dziś na polu']),
        h('p', { class: 'mr-muted', role: 'status', text: 'Pobieramy zdjęcia satelitarne, pogodę i dane działek… (do 60 s)' }),
        skeleton(4),
      ]),
      h('section', { class: 'mr-card', 'aria-hidden': 'true' }, skeleton(3)),
    ]);
    return;
  }
  if (state.analysisError) {
    replace(panel, errorView(state.analysisError, () => emit('analysis:retry'), 'Nie udało się przeanalizować działek'));
    return;
  }
  const a = state.analysis;
  if (!a || !Array.isArray(a.parcels) || !a.parcels.length) {
    replace(panel, emptyView('Kliknij na mapie, aby dodać pole, albo wybierz gospodarstwo z „Przykładów gospodarstw”.'));
    return;
  }
  // układ dwukolumnowy (≥900 px): po lewej „Dziś na polu”, po prawej liczby i lista działek
  replace(panel, [todayCard(a), h('div', { class: 'mr-colstack' }, [summaryRow(a), parcelList(a)])]);
}

/** 3–5 zaleceń: najpierw ostrzeżenia pogodowe, potem zalecenia z analizy działek. */
function buildAdvice(a) {
  const items = [];
  const w = a.weather || {};
  const pv = w.provenance || {};
  (w.warnings || []).slice(0, 2).forEach((x) => {
    const from = x.valid_from ? fmtDay(String(x.valid_from).slice(0, 10)) : '';
    const to = x.valid_to ? fmtDay(String(x.valid_to).slice(0, 10)) : '';
    const when = from ? ` (${from}${to && to !== from ? `–${to}` : ''})` : '';
    items.push({ level: 'warn', text: `Ostrzeżenie ${x.source || 'IMGW-PIB'}: ${x.event || 'zjawisko'}${when}.`, prov: pv.warnings });
  });
  const daily = Array.isArray(w.daily) ? w.daily.slice(0, 3) : [];
  if (daily.length) {
    const rain = daily.reduce((s, d) => s + (Number(d.rain_mm) || 0), 0);
    const tmin = Math.min(...daily.map((d) => (d.t_min_c === null || d.t_min_c === undefined ? 99 : Number(d.t_min_c))));
    if (tmin < 0) items.push({ level: 'warn', text: `W ciągu 3 dni możliwy przymrozek (do ${num(tmin, 0)}°C) – nie wykonuj zabiegów dolistnych przed nocnym spadkiem temperatury.`, prov: pv.forecast });
    if (rain >= 5) items.push({ level: 'info', text: `W ciągu 3 dni spadnie ok. ${num(rain, 0)} mm deszczu – zaplanuj nawożenie i opryski po opadach.`, prov: pv.forecast });
    else if (rain < 1) items.push({ level: 'ok', text: 'Najbliższe 3 dni prawie bez opadów – dobre warunki do prac polowych.', prov: pv.forecast });
  }
  const cur = w.current || {};
  if (cur.wind_ms !== null && cur.wind_ms !== undefined && Number(cur.wind_ms) > 4) {
    items.push({ level: 'info', text: `Wiatr ${num(cur.wind_ms, 1)} m/s – oprysk sprzętem naziemnym dopuszczalny przy wietrze do 4 m/s (do 5 m/s z techniką ograniczającą znoszenie ≥75%) – rozporządzenie MRiRW.`, prov: pv.current });
  }
  const seen = new Set();
  a.parcels.forEach((p) => {
    (p.advice_bullets || []).forEach((t) => {
      const key = String(t);
      if (seen.has(key)) return;
      seen.add(key);
      items.push({ level: plantState(p).st === 'bad' || waterState(p).st === 'bad' ? 'warn' : 'info', text: key, who: p.id_tag, prov: p.provenance && p.provenance.ndvi });
    });
  });
  return items.slice(0, 5);
}

function todayCard(a) {
  const items = buildAdvice(a);
  const ICON = { warn: 'warn', info: 'info', ok: 'checkCircle' };
  const title = a.is_historical && a.analysis_date ? `Stan pola na dzień ${fmtDate(a.analysis_date)}` : 'Dziś na polu';
  return h('section', { class: 'mr-card mr-card--today', 'aria-labelledby': 'mr-today-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-today-h' }, [icon('sun'), title]),
    items.length
      ? h('ul', { class: 'mr-advice' }, items.map((it) => h('li', { class: `mr-advice__item mr-advice__item--${it.level}` }, [
        icon(ICON[it.level] || 'info', 'mr-advice__ico'),
        h('div', null, [
          h('p', null, [it.who ? h('strong', { text: `${it.who}: ` }) : null, it.text]),
          it.prov ? provChip(it.prov) : null,
        ]),
      ])))
      : h('p', { class: 'mr-muted', text: 'Brak zaleceń – źródła danych nie zwróciły informacji.' }),
  ]);
}

function summaryRow(a) {
  const w = a.weather || {};
  const n = a.parcels.length;
  const kpis = [
    h('div', { class: 'mr-kpi' }, [h('dt', { text: 'Działki' }), h('dd', { text: String(n) })]),
    h('div', { class: 'mr-kpi' }, [h('dt', { text: 'Powierzchnia' }), h('dd', { text: val(a.total_area_ha, 'ha', 2) })]),
  ];
  if (a.is_historical) {
    kpis.push(h('div', { class: 'mr-kpi' }, [h('dt', { text: 'Opad 60 dni' }), h('dd', { text: val(w.precip_60d_mm, 'mm', 1) })]));
    kpis.push(h('div', { class: 'mr-kpi' }, [h('dt', { text: 'KBW 60 dni' }), h('dd', { text: val(w.kbw_60d_mm, 'mm', 1) })]));
  } else {
    kpis.push(h('div', { class: 'mr-kpi' }, [h('dt', { text: 'Opad 7 dni' }), h('dd', { text: val(w.precip_7d_mm, 'mm', 1) })]));
  }
  return h('dl', { class: 'mr-kpis' }, kpis);
}

function ndviWord(v) {
  if (v === null || v === undefined) return 'brak danych';
  if (v < 0.2) return 'gleba odkryta lub brak roślin';
  if (v < 0.4) return 'słaba';
  if (v < 0.6) return 'umiarkowana';
  return 'dobra';
}

// kolejność „naturalna”: Dz. 2/4, 2/5, 3/4, 58/4, 111/5
const byName = new Intl.Collator('pl', { numeric: true, sensitivity: 'base' });

function parcelList(a) {
  const custom = state.farmName === CUSTOM_FARM;
  return h('section', { class: 'mr-parcels', 'aria-labelledby': 'mr-parcels-h' }, [
    h('h2', { class: 'mr-section__title', id: 'mr-parcels-h', text: 'Działki' }),
    h('ul', { class: 'mr-plist' }, a.parcels.map((p, i) => ({ p, i }))
      .filter(({ p }) => !state.parcelFilter || state.parcelFilter(p))   // aktywny „Filtruj działki”
      .sort((x, y) => byName.compare(x.p.id_tag || `Działka ${x.i + 1}`, y.p.id_tag || `Działka ${y.i + 1}`))
      .map(({ p, i }) => parcelItem(p, i, custom))),
  ]);
}

function parcelItem(p, i, custom) {
  const sel = i === state.expanded;
  const bodyId = nextId('mr-pbody');
  const head = h('button', {
    type: 'button', class: 'mr-pitem__head', 'aria-expanded': String(sel), 'aria-controls': bodyId,
    onclick: () => emit('parcel:select', sel ? -1 : i),
  }, [
    h('span', { class: 'mr-pitem__name' }, [h('strong', { text: p.id_tag || `Działka ${i + 1}` }),
      h('span', { class: 'mr-muted', text: [p.crop, p.area_ha !== null && p.area_ha !== undefined ? val(p.area_ha, 'ha', 2) : null].filter(Boolean).join(' · ') || 'uprawa nieznana' })]),
    stateIcons(p),
    icon('plus', 'mr-pitem__chev'),
  ]);
  const prov = p.provenance || {};
  const nd = p.ndvi;
  const sm = p.soil_moisture;
  const body = h('div', { class: 'mr-pitem__body', id: bodyId, hidden: !sel }, [
    stateLines(p),
    (p.warnings || []).length ? h('ul', { class: 'mr-warnlist' }, p.warnings.map((w) => h('li', null, [icon('warn'), String(w)]))) : null,

    countySection(p),

    h('h3', { class: 'mr-metric__title' }, [`Kondycja roślin (NDVI z satelity Sentinel-2${nd && nd.acquired_on ? `, stan na ${fmtDate(nd.acquired_on)}` : ''}) `,
      indexTip('ndvi')]),
    nd ? h('div', { class: 'mr-metric' }, [
      h('p', { class: 'mr-metric__value' }, [h('strong', { text: num(nd.mean, 2) }), ` – kondycja ${ndviWord(nd.mean)}`]),
      shareBar(nd),
    ]) : h('p', { class: 'mr-muted', text: `Brak danych${prov.ndvi && prov.ndvi.note ? ` – ${prov.ndvi.note}` : ''}.` }),
    provChip(prov.ndvi),
    waterIndices(p),
    p.history_status === 'pending' && !p.history
      ? h('p', { class: 'mr-muted', role: 'status', text: 'Kondycja roślin w ostatnich miesiącach – wczytywanie historii zdjęć satelitarnych…' })
      : history(p.history),

    h('h3', { class: 'mr-metric__title' }, [`Wilgotność gleby (model${prov.soil_moisture && prov.soil_moisture.observed_at ? `, stan na ${fmtDate(prov.soil_moisture.observed_at)}` : ''}) `,
      infoTip('wilgotność gleby', 'Procent objętości gleby wypełnionej wodą, z modelu pogodowego (siatka ok. 9 km) – nie jest to pomiar na Twoim polu. Pojemność polowa – ile wody gleba zatrzyma po odcieknięciu nadmiaru; punkt więdnięcia – poniżej tej wilgotności rośliny nie pobiorą wody.')]),
    sm ? h('div', { class: 'mr-pitem__soil' }, soilGauge(sm, prov.soil_moisture)) : h('p', { class: 'mr-muted', text: `Brak danych${prov.soil_moisture && prov.soil_moisture.note ? ` – ${prov.soil_moisture.note}` : ''}.` }),
    sm ? h('dl', { class: 'mr-pairs' }, [
      h('dt', { text: 'Zapas wody dla roślin' }), h('dd', { text: p.water_reserve_days === null || p.water_reserve_days === undefined ? 'Brak danych' : `${p.water_reserve_days} dni` }),
    ]) : null,
    soilHistory(p, sm),
    soilPropsSection(p),
    drought10ySection(p),

    h('h3', { class: 'mr-metric__title', text: 'Działka ewidencyjna' }),
    h('dl', { class: 'mr-pairs' }, [
      h('dt', { text: 'Identyfikator' }), h('dd', { text: p.parcel_id || 'Brak danych' }),
      h('dt', { text: 'Położenie' }), h('dd', { text: [p.commune, p.county ? `pow. ${p.county}` : null, p.voivodeship].filter(Boolean).join(', ') || 'Brak danych' }),
    ]),
    provChip(prov.parcel),
    custom ? h('button', { type: 'button', class: 'mr-btn mr-btn--danger mr-btn--small', onclick: () => emit('parcel:remove', i) }, [icon('trash'), 'Usuń działkę z listy']) : null,
  ]);
  return h('li', { class: `mr-pitem${sel ? ' mr-pitem--selected' : ''}` }, [head, body]);
}

export function shareBar(nd) {
  const parts = [
    ['good', 'zdrowe rośliny', nd.healthy_pct],
    ['warn', 'rośliny w stresie', nd.stress_pct],
    ['bare', 'odkryta gleba', nd.bare_soil_pct],
  ].filter(([, , v]) => v !== null && v !== undefined);
  if (!parts.length) return null;
  const bar = h('div', { class: 'mr-share', 'aria-hidden': 'true' }, parts.map(([k, , v]) => {
    const s = h('span', { class: `mr-share__seg mr-share__seg--${k}` });
    s.style.width = `${Math.max(0, Math.min(100, Number(v)))}%`;
    return s;
  }));
  return h('div', null, [bar, h('ul', { class: 'mr-share__legend' }, parts.map(([k, t, v]) => h('li', null, [h('span', { class: `mr-dot mr-dot--${k}`, 'aria-hidden': 'true' }), `${t}: ${num(v, 0)}%`])))]);
}

// Woda z tego samego zdjęcia Sentinel-2 co NDVI: w roślinach (NDMI) i stojąca na powierzchni (NDWI)
function waterIndices(p) {
  const lay = p.layers || {};
  const nm = lay.ndmi && isFinite(Number(lay.ndmi.mean)) && lay.ndmi.mean !== null ? Number(lay.ndmi.mean) : null;
  const nw = lay.ndwi && isFinite(Number(lay.ndwi.mean)) && lay.ndwi.mean !== null ? lay.ndwi : null;
  if (nm === null && !nw) return null;
  const ndmiWord = (x) => (x < -0.2 ? 'bardzo mało wody w roślinach / odkryta gleba' : x < 0 ? 'rośliny mają za mało wody' : x < 0.2 ? 'umiarkowane uwodnienie' : 'rośliny dobrze uwodnione');
  const ndwiWord = (w) => (Number(w.water_pct) >= 1 ? `woda stojąca na ok. ${num(w.water_pct, 0)}% działki`
    : Number(w.wet_pct) >= 1 ? `podmokło na ok. ${num(w.wet_pct, 0)}% działki` : 'brak wody stojącej na powierzchni');
  return h('div', { class: 'mr-wateridx' }, [
    nm === null ? null : h('p', { class: 'mr-wateridx__row' }, ['Woda w roślinach (NDMI) ', indexTip('ndmi'), ': ', h('strong', { text: num(nm, 2) }), ` – ${ndmiWord(nm)}`]),
    nw ? h('p', { class: 'mr-wateridx__row' }, ['Woda na polu (NDWI) ', indexTip('ndwi'), ': ', h('strong', { text: num(nw.mean, 2) }), ` – ${ndwiWord(nw)}`]) : null,
  ]);
}

// Wilgotność gleby 7–28 cm miesiąc po miesiącu (ERA5-Land, średnia miesięczna) – doczytywana po rozwinięciu
function ymNow(back) {
  const d = new Date();
  const t = d.getFullYear() * 12 + d.getMonth() - back;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, '0')}`;
}

function soilHistory(p, sm) {
  if (!Array.isArray(p.centroid)) return null;
  const box = h('div', { class: 'mr-soilhist', 'aria-live': 'polite' }, h('p', { class: 'mr-muted', text: 'Rozwiń, aby wczytać.' }));
  let loaded = false;
  const fc = sm && Number.isFinite(Number(sm.field_capacity_pct)) && sm.field_capacity_pct !== null ? Number(sm.field_capacity_pct) : null;
  const wp = sm && Number.isFinite(Number(sm.wilting_point_pct)) && sm.wilting_point_pct !== null ? Number(sm.wilting_point_pct) : null;
  const zone = (v) => (fc === null || wp === null ? 'plain' : v <= wp ? 'dry' : v < wp + (fc - wp) * 0.5 ? 'low' : v <= fc ? 'ok' : 'wet');
  const load = () => {
    if (loaded) return;
    loaded = true;
    replace(box, [h('p', { class: 'mr-muted', role: 'status', text: 'Wczytywanie wilgotności gleby z ostatnich 12 miesięcy…' }), skeleton(3)]);
    const q = new URLSearchParams({ lat: String(p.centroid[0]), lon: String(p.centroid[1]), start: ymNow(11), end: ymNow(0) });
    api(`/api/history/climate?${q}`, { timeout: 45000 }).then((d) => {
      const rows = (d && Array.isArray(d.months) ? d.months : []).slice(-12);
      if (!rows.some((r) => r.soil_moisture_pct !== null && r.soil_moisture_pct !== undefined)) {
        replace(box, [h('p', { class: 'mr-muted', text: 'Brak danych o wilgotności gleby dla tego okresu.' }), d && d.provenance ? provChip(d.provenance.soil_moisture || d.provenance) : null]);
        return;
      }
      const max = Math.max(50, fc !== null ? fc + 10 : 0, ...rows.map((r) => Number(r.soil_moisture_pct) || 0));
      replace(box, [
        h('ul', { class: 'mr-hbars' }, rows.map((r) => {
          const v = r.soil_moisture_pct;
          const has = v !== null && v !== undefined && Number.isFinite(Number(v));
          const fill = h('span', { class: `mr-hbars__fill mr-hbars__fill--${has ? zone(Number(v)) : 'plain'}` });
          fill.style.width = `${has ? Math.max(0, Math.min(100, (Number(v) / max) * 100)) : 0}%`;
          return h('li', null, [h('span', { class: 'mr-hbars__label', text: r.month }), h('span', { class: 'mr-hbars__track', 'aria-hidden': 'true' }, fill),
            h('span', { class: 'mr-hbars__val', text: has ? `${num(v, 0)}%` : 'brak' })]);
        })),
        fc !== null && wp !== null ? h('ul', { class: 'mr-gauge__legend' }, [
          ['dry', `poniżej ${num(wp, 0)}% – woda niedostępna`], ['low', 'niedobór'], ['ok', `optymalnie (do ${num(fc, 0)}%)`], ['wet', 'nadmiar'],
        ].map(([k, t]) => h('li', null, [h('span', { class: `mr-dot mr-dot--${k}`, 'aria-hidden': 'true' }), t]))) : null,
        h('p', { class: 'mr-hint', text: `Średnia miesięczna wilgotność w strefie korzeni (7–28 cm), % objętości gleby; skala paska 0–${num(max, 0)}%. Ostatni miesiąc może być niepełny.` }),
        d.provenance ? provChip(d.provenance.soil_moisture || d.provenance) : null,
      ]);
    }).catch((e) => { loaded = false; replace(box, errorView(e && e.message ? e.message : 'Nie udało się wczytać danych.', load)); });
  };
  const det = h('details', { class: 'mr-details' }, [h('summary', { text: 'Wilgotność gleby w ostatnich miesiącach' }), box]);
  det.addEventListener('toggle', () => { if (det.open) load(); });
  return det;
}

function history(hst) {
  if (!hst || !Array.isArray(hst.ndvi_monthly) || !hst.ndvi_monthly.length) return null;
  const rows = hst.ndvi_monthly.slice(-12);
  return h('details', { class: 'mr-details' }, [
    h('summary', { text: 'Kondycja roślin w ostatnich miesiącach (NDVI)' }),
    h('p', { class: 'mr-hint', text: 'NDVI – wskaźnik zieleni roślin ze zdjęć satelity Sentinel-2 (0 – goła ziemia, powyżej 0,5 – dobry łan). '
      + 'Dla każdego miesiąca: mediana bezchmurnych zdjęć, uśredniona po działce; „brak” = cały miesiąc pod chmurami.' }),
    indexHelp('ndvi', { compact: true }),
    h('ul', { class: 'mr-hbars' }, rows.map((r) => {
      const fill = h('span', { class: 'mr-hbars__fill' });
      fill.style.width = `${Math.max(0, Math.min(1, Number(r.mean) || 0)) * 100}%`;
      return h('li', null, [h('span', { class: 'mr-hbars__label', text: r.month }), h('span', { class: 'mr-hbars__track', 'aria-hidden': 'true' }, fill), h('span', { class: 'mr-hbars__val', text: r.mean === null ? 'brak' : num(r.mean, 2) })]);
    })),
    provChip(hst.provenance),
  ]);
}

function soilPropsSection(p) {
  const sg = p.soil_properties;
  if (!sg) return null;
  if (!sg.available) {
    return h('div', { class: 'mr-metric' }, [
      h('h3', { class: 'mr-metric__title', text: 'Właściwości gleby (ISRIC SoilGrids, 0–30 cm)' }),
      h('p', { class: 'mr-muted', text: `Brak danych${sg.provenance && sg.provenance.note ? ` – ${sg.provenance.note}` : ''}.` }),
      sg.provenance ? provChip(sg.provenance) : null,
    ]);
  }
  const frac = [
    sg.sand_pct !== null && sg.sand_pct !== undefined ? `piasek ${num(sg.sand_pct, 0)}%` : null,
    sg.silt_pct !== null && sg.silt_pct !== undefined ? `pył ${num(sg.silt_pct, 0)}%` : null,
    sg.clay_pct !== null && sg.clay_pct !== undefined ? `ił ${num(sg.clay_pct, 0)}%` : null,
  ].filter(Boolean).join(', ');
  const omTxt = sg.organic_matter_pct !== null && sg.organic_matter_pct !== undefined
    ? `${num(sg.organic_matter_pct, 1)}% (${sg.organic_matter_status || 'brak oceny'})`
    : 'brak danych';
  return h('div', { class: 'mr-metric' }, [
    h('h3', { class: 'mr-metric__title', text: 'Właściwości gleby (ISRIC SoilGrids, 0–30 cm)' }),
    h('dl', { class: 'mr-pairs' }, [
      h('dt', { text: 'Typ gleby' }), h('dd', { text: sg.texture_label || 'Brak danych' }),
      h('dt', { text: 'Uziarnienie' }), h('dd', { text: frac || 'Brak danych' }),
      h('dt', { text: 'Próchnica (szacunek)' }), h('dd', { text: omTxt }),
      h('dt', { text: 'Podatność na suszę (IUNG)' }), h('dd', { text: sg.drought_category_label || 'Brak danych' }),
    ]),
    sg.provenance ? provChip(sg.provenance) : null,
  ]);
}

function drought10ySection(p) {
  const dr = p.drought_10y;
  if (!dr) return null;
  if (!dr.available) {
    return h('div', { class: 'mr-metric' }, [
      h('h3', { class: 'mr-metric__title', text: '10-letnia podatność na suszę (ERA5)' }),
      h('p', { class: 'mr-muted', text: `Brak danych${dr.provenance && dr.provenance.note ? ` – ${dr.provenance.note}` : ''}.` }),
      dr.provenance ? provChip(dr.provenance) : null,
    ]);
  }
  const worst = Array.isArray(dr.worst_months) && dr.worst_months.length ? dr.worst_months.join(', ') : 'brak wyraźnego niedoboru';
  return h('div', { class: 'mr-metric' }, [
    h('h3', { class: 'mr-metric__title', text: `10-letnia podatność na suszę (${dr.period || '10 lat'})` }),
    h('dl', { class: 'mr-pairs' }, [
      h('dt', { text: 'Ocena podatności' }), h('dd', { text: dr.vulnerability_level || 'Brak danych' }),
      h('dt', { text: 'Lata z niedoborem wody' }), h('dd', { text: `${dr.drought_years_count} z ${dr.years_analyzed} lat (${dr.drought_frequency_pct}%)` }),
      h('dt', { text: 'Średni KBW (IV–VII)' }), h('dd', { text: val(dr.mean_kbw_apr_jul_mm, 'mm', 0) }),
      h('dt', { text: 'Najbardziej suche miesiące' }), h('dd', { text: worst }),
    ]),
    dr.provenance ? provChip(dr.provenance) : null,
  ]);
}

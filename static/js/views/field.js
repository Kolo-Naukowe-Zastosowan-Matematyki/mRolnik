// Zakładka „Pole”: „Dziś na polu” (zalecenia prostym językiem) + lista działek (tekstowa alternatywa mapy).
import { h, icon, replace, infoTip, nextId } from '../dom.js';
import { state, emit, CUSTOM_FARM } from '../state.js';
import { val, num, provChip, riskBadge, errorView, emptyView, fmtDay } from '../format.js';

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
    replace(panel, emptyView('Dodaj własne pole albo wybierz gospodarstwo z sekcji „Przykłady (demo)”.'));
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
      items.push({ level: p.risk === 'Wysokie' || p.risk === 'Podwyższone' ? 'warn' : 'ok', text: key, who: p.id_tag, prov: p.provenance && p.provenance.ndvi });
    });
  });
  return items.slice(0, 5);
}

function todayCard(a) {
  const items = buildAdvice(a);
  const ICON = { warn: 'warn', info: 'info', ok: 'checkCircle' };
  return h('section', { class: 'mr-card mr-card--today', 'aria-labelledby': 'mr-today-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-today-h' }, [icon('sun'), 'Dziś na polu']),
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
  return h('dl', { class: 'mr-kpis' }, [
    h('div', { class: 'mr-kpi' }, [h('dt', { text: 'Działki' }), h('dd', { text: String(n) })]),
    h('div', { class: 'mr-kpi' }, [h('dt', { text: 'Powierzchnia' }), h('dd', { text: val(a.total_area_ha, 'ha', 2) })]),
    h('div', { class: 'mr-kpi' }, [h('dt', { text: 'Opad 7 dni' }), h('dd', { text: val(w.precip_7d_mm, 'mm', 1) })]),
  ]);
}

function ndviWord(v) {
  if (v === null || v === undefined) return 'brak danych';
  if (v < 0.2) return 'gleba odkryta lub brak roślin';
  if (v < 0.4) return 'słaba';
  if (v < 0.6) return 'umiarkowana';
  return 'dobra';
}

function parcelList(a) {
  const custom = state.farmName === CUSTOM_FARM;
  return h('section', { class: 'mr-parcels', 'aria-labelledby': 'mr-parcels-h' }, [
    h('h2', { class: 'mr-section__title', id: 'mr-parcels-h', text: 'Działki' }),
    h('ul', { class: 'mr-plist' }, a.parcels.map((p, i) => parcelItem(p, i, custom))),
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
    riskBadge(p.risk),
    icon('plus', 'mr-pitem__chev'),
  ]);
  const prov = p.provenance || {};
  const nd = p.ndvi;
  const sm = p.soil_moisture;
  const body = h('div', { class: 'mr-pitem__body', id: bodyId, hidden: !sel }, [
    p.status_label ? h('p', { class: 'mr-pitem__status', text: p.status_label }) : null,
    (p.warnings || []).length ? h('ul', { class: 'mr-warnlist' }, p.warnings.map((w) => h('li', null, [icon('warn'), String(w)]))) : null,

    h('h3', { class: 'mr-metric__title' }, ['Kondycja roślin (NDVI z satelity Sentinel-2) ',
      infoTip('NDVI', 'NDVI to wskaźnik zieleni roślin liczony ze zdjęć satelitarnych: 0 – goła ziemia, 1 – bardzo bujne rośliny. Zimą i po zbiorach wartości są naturalnie niższe.')]),
    nd ? h('div', { class: 'mr-metric' }, [
      h('p', { class: 'mr-metric__value' }, [h('strong', { text: num(nd.mean, 2) }), ` – kondycja ${ndviWord(nd.mean)}`]),
      shareBar(nd),
    ]) : h('p', { class: 'mr-muted', text: `Brak danych${prov.ndvi && prov.ndvi.note ? ` – ${prov.ndvi.note}` : ''}.` }),
    provChip(prov.ndvi),

    h('h3', { class: 'mr-metric__title' }, ['Wilgotność gleby (model) ',
      infoTip('wilgotność gleby', 'Procent objętości gleby wypełnionej wodą, z modelu pogodowego (siatka ok. 9 km) – nie jest to pomiar na Twoim polu.')]),
    sm ? h('dl', { class: 'mr-pairs' }, [
      h('dt', { text: 'Wierzchnia warstwa (0–7 cm)' }), h('dd', { text: val(sm.surface_pct, '%', 0) }),
      h('dt', { text: 'Strefa korzeni (7–28 cm)' }), h('dd', { text: val(sm.root_zone_pct, '%', 0) }),
      h('dt', { text: 'Trend 7 dni' }), h('dd', { text: sm.trend_7d || 'brak danych' }),
      h('dt', { text: 'Zapas wody' }), h('dd', { text: p.water_reserve_days === null || p.water_reserve_days === undefined ? 'Brak danych' : `${p.water_reserve_days} dni` }),
    ]) : h('p', { class: 'mr-muted', text: `Brak danych${prov.soil_moisture && prov.soil_moisture.note ? ` – ${prov.soil_moisture.note}` : ''}.` }),
    provChip(prov.soil_moisture),

    p.history_status === 'pending' && !p.history
      ? h('p', { class: 'mr-muted', role: 'status', text: 'Kondycja roślin w ostatnich miesiącach – wczytywanie historii zdjęć satelitarnych…' })
      : history(p.history),

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

function shareBar(nd) {
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

function history(hst) {
  if (!hst || !Array.isArray(hst.ndvi_monthly) || !hst.ndvi_monthly.length) return null;
  const rows = hst.ndvi_monthly.slice(-12);
  return h('details', { class: 'mr-details' }, [
    h('summary', { text: 'Kondycja roślin w ostatnich miesiącach' }),
    h('ul', { class: 'mr-hbars' }, rows.map((r) => {
      const fill = h('span', { class: 'mr-hbars__fill' });
      fill.style.width = `${Math.max(0, Math.min(1, Number(r.mean) || 0)) * 100}%`;
      return h('li', null, [h('span', { class: 'mr-hbars__label', text: r.month }), h('span', { class: 'mr-hbars__track', 'aria-hidden': 'true' }, fill), h('span', { class: 'mr-hbars__val', text: r.mean === null ? 'brak' : num(r.mean, 2) })]);
    })),
    provChip(hst.provenance),
  ]);
}

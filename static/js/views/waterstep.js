// KROK 3: Ocena sytuacji wodnej –
// 1) Okno bieżącego stanu retencji wody na wybranych działkach
// 2) Wykres historycznej retencji wody (wilgotność gleby 7–28 cm ERA5-Land + opady vs norma IMGW-PIB)
import { h, icon, replace, infoTip } from '../dom.js';
import { api } from '../api.js';
import { state, emit } from '../state.js';
import { num, provChip, errorView, emptyView, fmtDate } from '../format.js';
import { chartFigure, baseScales, baseLegend, refDataset, constLine } from '../charts.js';
import { skeleton } from './field.js';

const MONTHS_SHORT = ['sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru'];
const SOIL_STATE = {
  dry: 'woda niedostępna',
  low: 'niedobór',
  ok: 'optymalnie',
  wet: 'nadmiar',
  none: 'brak danych',
};

const RANGES = [
  { key: '12', label: '1 rok', months: 12 },
  { key: '36', label: '3 lata', months: 36 },
  { key: '60', label: '5 lat', months: 60 },
  { key: '120', label: '10 lat', months: 120 },
];

const historyCache = new Map();
let activeRangeKey = '36';
let renderToken = 0;

function nn(v) {
  return v !== null && v !== undefined && Number.isFinite(Number(v)) ? Number(v) : null;
}

function soilZone(v, fc, wp) {
  if (v === null || fc === null || wp === null) return 'none';
  if (v <= wp) return 'dry';
  if (v < wp + (fc - wp) * 0.5) return 'low';
  if (v <= fc) return 'ok';
  return 'wet';
}

function ymOf(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function ymBack(ym, n) {
  const [y, m] = ym.split('-').map(Number);
  const t = y * 12 + (m - 1) - n;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, '0')}`;
}

function monthLabel(ym) {
  if (!ym || !ym.includes('-')) return ym || '';
  const [y, m] = ym.split('-').map(Number);
  return `${MONTHS_SHORT[(m || 1) - 1]} ${String(y).slice(2)}`;
}

function parcelTitle(p, idx) {
  return p.id_tag || (p.parcel_id ? `Dz. ${p.parcel_id.split('.').pop()}` : `Działka ${idx + 1}`);
}

function parcelCoords(p) {
  if (!p) return null;
  if (Array.isArray(p.centroid) && p.centroid.length === 2) {
    return { lat: Number(p.centroid[0]), lon: Number(p.centroid[1]) };
  }
  if (p.lat !== undefined && p.lon !== undefined) {
    return { lat: Number(p.lat), lon: Number(p.lon) };
  }
  return null;
}

/** Pasek stref wilgotności gleby (wg PTW i PP działki) ze znacznikami 0–7 cm i 7–28 cm. */
function retentionGauge(sm) {
  const fc = nn(sm.field_capacity_pct);
  const wp = nn(sm.wilting_point_pct);
  const top = nn(sm.surface_pct);
  const root = nn(sm.root_zone_pct);
  const max = Math.max(50, (fc || 0) + 10, (top || 0) + 5, (root || 0) + 5);
  const pct = (v) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;
  const track = h('div', { class: 'mr-gauge__track mr-soilrow__track', 'aria-hidden': 'true' });

  if (fc !== null && wp !== null && fc > wp) {
    const mid = wp + (fc - wp) * 0.5;
    [['dry', 0, wp], ['low', wp, mid], ['ok', mid, fc], ['wet', fc, max]].forEach(([k, a, b]) => {
      const z = h('span', { class: `mr-gauge__zone mr-gauge__zone--${k}` });
      z.style.left = pct(a);
      z.style.width = `${((b - a) / max) * 100}%`;
      track.appendChild(z);
    });
  } else {
    track.classList.add('mr-gauge__track--plain');
  }

  [[top, 'top'], [root, 'root']].forEach(([v, k]) => {
    if (v === null) return;
    const m = h('span', { class: `mr-gauge__marker mr-soilrow__marker mr-soilrow__marker--${k}` });
    m.style.left = pct(v);
    track.appendChild(m);
  });

  return track;
}

/** 1. Okno bieżącego stanu retencji wody na wybranych działkach. */
function currentRetentionCard() {
  const card = h('section', { class: 'mr-card mr-waterstep__card', 'aria-labelledby': 'mr-ws-cur-h' }, [
    h('h2', { class: 'mr-card__title', id: 'mr-ws-cur-h' }, [
      icon('drop'),
      'Bieżący stan retencji wody na działkach ',
      infoTip(
        'bieżący stan retencji',
        'Wilgotność gleby z modelu pogodowego (DWD ICON / ERA5-Land) odniesiona do pojemności polowej (PP) i punktu trwałego więdnięcia (PTW) wg FAO-56. Gruba kreska – warstwa 0–7 cm, cienka – strefa korzeni 7–28 cm. Zapas wody oznacza liczbę dni do spadku wody łatwo dostępnej poniżej 50%.'
      ),
    ]),
  ]);

  if (state.loading) {
    card.appendChild(h('p', { class: 'mr-muted', role: 'status', text: 'Pobieramy bieżący stan wilgotności gleby i zapas wody dla wybranych działek…' }));
    card.appendChild(skeleton(4));
    return card;
  }

  if (state.analysisError) {
    card.appendChild(errorView(state.analysisError, () => emit('analysis:retry'), 'Nie udało się pobrać bieżącego stanu retencji'));
    return card;
  }

  const parcels = state.analysis && Array.isArray(state.analysis.parcels) ? state.analysis.parcels : [];
  if (!parcels.length) {
    card.appendChild(emptyView('Brak wybranych działek do oceny stanu retencji.'));
    return card;
  }

  const withData = parcels.filter((p) => p.soil_moisture && nn(p.soil_moisture.surface_pct) !== null);
  if (!withData.length) {
    const prov = parcels.map((p) => p.provenance && p.provenance.soil_moisture).find(Boolean);
    card.appendChild(h('p', { class: 'mr-muted', text: `Brak danych o bieżącej wilgotności gleby${prov && prov.note ? ` – ${prov.note}` : ''}.` }));
    if (prov) card.appendChild(provChip(prov));
    return card;
  }

  const dates = withData
    .map((p) => p.provenance && p.provenance.soil_moisture && p.provenance.soil_moisture.observed_at)
    .filter(Boolean)
    .sort();

  const counts = {};
  withData.forEach((p) => {
    const sm = p.soil_moisture;
    const valRef = nn(sm.root_zone_pct) !== null ? nn(sm.root_zone_pct) : nn(sm.surface_pct);
    const z = soilZone(valRef, nn(sm.field_capacity_pct), nn(sm.wilting_point_pct));
    counts[z] = (counts[z] || 0) + 1;
  });

  const summaryItems = ['wet', 'ok', 'low', 'dry', 'none']
    .filter((k) => counts[k])
    .map((k) => h('li', { class: `mr-soilsum__i mr-soilsum__i--${k}` }, [
      h('span', { class: `mr-dot mr-dot--${k === 'none' ? 'plain' : k}`, 'aria-hidden': 'true' }),
      `${SOIL_STATE[k]}: ${counts[k]}`,
    ]));

  const rows = parcels.map((p, i) => {
    const name = parcelTitle(p, i);
    const sm = p.soil_moisture;
    const isSel = i === state.selected;
    const onSelect = () => emit('parcel:select', i);

    if (!sm || nn(sm.surface_pct) === null) {
      return h('li', {
        class: `mr-soilrow mr-ws-row${isSel ? ' mr-ws-row--active' : ''}`,
        tabindex: '0',
        role: 'button',
        'aria-pressed': String(isSel),
        onclick: onSelect,
        onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(); } },
      }, [
        h('span', { class: 'mr-soilrow__name' }, [
          h('strong', { text: name }),
          p.crop ? h('span', { class: 'mr-muted', text: p.crop }) : null,
        ]),
        h('span', { class: 'mr-muted mr-soilrow__na', text: 'brak danych' }),
      ]);
    }

    const top = nn(sm.surface_pct);
    const root = nn(sm.root_zone_pct);
    const awp = nn(sm.available_water_pct);
    const days = nn(p.water_reserve_days);
    const kbw14 = p.weather_local ? nn(p.weather_local.climatic_water_balance_14d_mm) : null;
    const z = soilZone(root !== null ? root : top, nn(sm.field_capacity_pct), nn(sm.wilting_point_pct));

    const metaParts = [];
    if (top !== null) metaParts.push(`0–7 cm: ${num(top, 0)}%`);
    if (root !== null) metaParts.push(`7–28 cm: ${num(root, 0)}%`);
    if (awp !== null) metaParts.push(`woda użyteczna: ${num(awp, 0)}%`);
    if (days !== null) metaParts.push(`zapas: ${days === 0 ? '0 dni' : `${num(days, 0)} dni`}`);
    if (kbw14 !== null) metaParts.push(`bilans 14d: ${kbw14 >= 0 ? '+' : ''}${num(kbw14, 0)} mm`);

    return h('li', {
      class: `mr-soilrow mr-ws-row${isSel ? ' mr-ws-row--active' : ''}`,
      tabindex: '0',
      role: 'button',
      'aria-pressed': String(isSel),
      onclick: onSelect,
      onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(); } },
    }, [
      h('span', { class: 'mr-soilrow__name' }, [
        h('strong', { text: name }),
        p.crop ? h('span', { class: 'mr-muted', text: p.crop }) : null,
      ]),
      retentionGauge(sm),
      h('span', { class: 'mr-ws-row__metrics', text: metaParts.join(' · ') }),
      h('span', { class: `mr-badge mr-badge--soil-${z}`, text: SOIL_STATE[z] }),
    ]);
  });

  const provSoil = withData.map((p) => p.provenance && p.provenance.soil_moisture).find(Boolean);
  const provWater = withData.map((p) => p.provenance && p.provenance.water_reserve).find(Boolean);

  card.append(
    h('p', { class: 'mr-soil__when', text: dates.length ? `Stan na ${fmtDate(dates[dates.length - 1])} (kliknij działkę, aby zobaczyć jej wykres historyczny):` : 'Kliknij działkę, aby zobaczyć jej wykres historyczny:' }),
    h('ul', { class: 'mr-soilsum' }, summaryItems),
    h('ul', { class: 'mr-soilrows' }, rows),
    h('div', { class: 'mr-ws-provs' }, [
      provSoil ? provChip(provSoil) : null,
      provWater ? provChip(provWater) : null,
    ].filter(Boolean)),
  );
  return card;
}

/** 2. Wykres historycznej retencji wody (wilgotność gleby 7–28 cm + opad vs norma). */
function historicalRetentionCard(token) {
  const analysisParcels = state.analysis && Array.isArray(state.analysis.parcels) ? state.analysis.parcels : [];
  const rawParcels = analysisParcels.length ? analysisParcels : state.customParcels;
  const idx = Math.max(0, Math.min(state.selected || 0, Math.max(0, rawParcels.length - 1)));
  const activeParcel = rawParcels[idx] || null;
  const coords = parcelCoords(activeParcel);
  const title = activeParcel ? parcelTitle(activeParcel, idx) : 'działka';

  const body = h('div', { class: 'mr-card__body', 'aria-busy': 'true' }, [
    h('p', { class: 'mr-muted', role: 'status', text: 'Wczytywanie historii wilgotności gleby i opadów (ERA5-Land)…' }),
    skeleton(4),
  ]);

  const rangeBar = h('div', { class: 'mr-ws-ranges', role: 'group', 'aria-label': 'Zakres historii retencji' }, RANGES.map((r) => h('button', {
    type: 'button',
    class: `mr-btn mr-btn--small ${activeRangeKey === r.key ? 'mr-btn--primary' : 'mr-btn--ghost'}`,
    'aria-pressed': String(activeRangeKey === r.key),
    onclick: () => {
      if (activeRangeKey === r.key) return;
      activeRangeKey = r.key;
      const host = state.root && state.root.querySelector('[data-ref="water-main"]');
      if (host) renderWaterStep(host);
    },
  }, r.label)));

  const card = h('section', { class: 'mr-card mr-waterstep__card', 'aria-labelledby': 'mr-ws-hist-h' }, [
    h('div', { class: 'mr-ws-cardhead' }, [
      h('h2', { class: 'mr-card__title', id: 'mr-ws-hist-h' }, [
        icon('waves'),
        `Historyczna retencja wody – ${title}`,
      ]),
      rangeBar,
    ]),
    body,
  ]);

  if (!coords || !Number.isFinite(coords.lat) || !Number.isFinite(coords.lon)) {
    body.removeAttribute('aria-busy');
    replace(body, emptyView('Brak współrzędnych działki do pobrania historii retencji.'));
    return card;
  }

  const rangeSpec = RANGES.find((r) => r.key === activeRangeKey) || RANGES[1];
  const endYm = ymOf(new Date());
  const startYm = ymBack(endYm, rangeSpec.months - 1);
  const cacheKey = `${coords.lat.toFixed(2)},${coords.lon.toFixed(2)}:${startYm}:${endYm}`;

  const load = () => {
    body.setAttribute('aria-busy', 'true');
    replace(body, [
      h('p', { class: 'mr-muted', role: 'status', text: 'Wczytywanie historii wilgotności gleby i opadów (ERA5-Land)…' }),
      skeleton(4),
    ]);

    let req = historyCache.get(cacheKey);
    if (!req) {
      const qs = new URLSearchParams({
        lat: String(coords.lat),
        lon: String(coords.lon),
        start: startYm,
        end: endYm,
      });
      req = api(`/api/history/climate?${qs}`, { timeout: 35000 }).then((d) => {
        if (!d || !Array.isArray(d.months) || !d.months.length) historyCache.delete(cacheKey);
        return d;
      }).catch((e) => {
        historyCache.delete(cacheKey);
        throw e;
      });
      historyCache.set(cacheKey, req);
    }

    req.then((d) => {
      if (token !== renderToken) return;
      body.removeAttribute('aria-busy');
      replace(body, renderHistoricalChart(d, activeParcel));
    }).catch((err) => {
      if (token !== renderToken) return;
      body.removeAttribute('aria-busy');
      replace(body, errorView(err, load, 'Nie udało się pobrać historii retencji wody'));
    });
  };

  load();
  return card;
}

function renderHistoricalChart(d, parcel) {
  const rows = Array.isArray(d && d.months) ? d.months : [];
  if (!rows.length) {
    return [
      h('p', { class: 'mr-muted', text: (d && d.provenance && d.provenance.precip && d.provenance.precip.note) || 'Brak historycznych danych o wilgotności gleby i opadach.' }),
      d && d.provenance && d.provenance.precip ? provChip(d.provenance.precip) : null,
    ].filter(Boolean);
  }

  const volVals = rows.map((r) => nn(r.soil_moisture_pct));
  const wetVals = rows.map((r) => nn(r.soil_wetness_pct));
  const hasVol = volVals.some((v) => v !== null);
  const isFallback = !hasVol && (d.source === 'nasa_power' || wetVals.some((v) => v !== null));
  const moistVals = isFallback ? wetVals : volVals;
  const validMoist = moistVals.filter((v) => v !== null);
  const hasNorm = rows.some((r) => nn(r.precip_normal_mm) !== null);
  const sumP = rows.filter((r) => r.complete).reduce((a, r) => a + (nn(r.precip_mm) || 0), 0);
  const sumN = rows.filter((r) => r.complete).reduce((a, r) => a + (nn(r.precip_normal_mm) || 0), 0);

  const avgMoist = validMoist.length ? validMoist.reduce((a, b) => a + b, 0) / validMoist.length : null;
  const minMoist = validMoist.length ? Math.min(...validMoist) : null;
  const maxMoist = validMoist.length ? Math.max(...validMoist) : null;

  // Pojemność polowa (PP) i punkt więdnięcia (PTW) dotyczą wyłącznie wilgotności objętościowej [% obj.] z ERA5-Land;
  // przy zapasowym NASA POWER (względne nasycenie GWETROOT 0–100%) nie rysujemy PP/PTW, by nie porównywać różnych jednostek.
  const sm = !isFallback && parcel && parcel.soil_moisture ? parcel.soil_moisture : null;
  const fc = sm ? nn(sm.field_capacity_pct) : null;
  const wp = sm ? nn(sm.wilting_point_pct) : null;

  const summaryParts = [];
  if (avgMoist !== null) {
    if (isFallback) {
      summaryParts.push(`Średnie względne nasycenie strefy korzeniowej (NASA POWER): ${num(avgMoist, 1)}% (zakres ${num(minMoist, 1)}–${num(maxMoist, 1)}%).`);
    } else {
      summaryParts.push(`Średnia wilgotność gleby (7–28 cm): ${num(avgMoist, 1)}% obj. (zakres ${num(minMoist, 1)}–${num(maxMoist, 1)}%).`);
    }
  }
  if (hasNorm && sumN > 0) {
    summaryParts.push(`Opad w pełnych miesiącach: ${num(sumP, 0)} mm (${num((sumP / sumN) * 100, 0)}% normy IMGW-PIB 1991–2020).`);
  } else if (sumP > 0) {
    summaryParts.push(`Suma opadów w pełnych miesiącach: ${num(sumP, 0)} mm.`);
  }
  const summary = summaryParts.join(' ') || (isFallback ? 'Historyczna retencja wody (źródło zapasowe NASA POWER).' : 'Historyczna retencja wody (ERA5-Land).');

  const many = rows.length > 48;
  const labels = rows.map((r) => monthLabel(r.month));
  const yUnit = isFallback ? '% nasycenia' : '% obj.';
  const moistColLabel = isFallback ? 'Nasycenie strefy korzeniowej [%] (NASA POWER)' : 'Wilgotność gleby 7–28 cm [% obj.]';
  const moistSeriesLabel = isFallback ? 'Nasycenie strefy korzeniowej [%] (NASA POWER)' : 'Wilgotność gleby 7–28 cm [% obj.] (ERA5-Land)';
  const precipSeriesLabel = isFallback ? 'Opad miesięczny [mm] (NASA POWER)' : 'Opad miesięczny [mm] (ERA5)';
  const chartTitle = isFallback
    ? `Nasycenie strefy korzeniowej i opady miesięczne (NASA POWER)${d.normals_station ? ` · norma IMGW-PIB ${d.normals_station}` : ''}`
    : `Wilgotność gleby (7–28 cm) i opady miesięczne${d.normals_station ? ` · norma IMGW-PIB ${d.normals_station}` : ''}`;
  const chartNote = isFallback
    ? 'Archiwum ERA5-Land było chwilowo niedostępne – wyświetlono zapasową reanalizę NASA POWER (MERRA-2 / GEOS-IT, siatka ~50 km). '
      + 'Linia ciągła (oś lewa) – względne nasycenie strefy korzeniowej GWETROOT [0–100%], nie % objętości (linie PP/PTW pominięte). '
      + 'Słupki (oś prawa) – suma opadów [mm] na tle normy IMGW-PIB 1991–2020.'
    : 'Linia ciągła (oś lewa) – miesięczna wilgotność gleby w warstwie korzeniowej 7–28 cm z reanalizy ERA5-Land [% obj.]. '
      + (fc !== null && wp !== null ? `Poziome linie przerywane – pojemność polowa (${num(fc, 0)}%) i punkt więdnięcia (${num(wp, 0)}%) wg FAO-56. ` : '')
      + 'Słupki (oś prawa) – suma opadów ERA5 [mm] na tle normy IMGW-PIB 1991–2020.';

  return [
    isFallback ? h('p', { class: 'mr-callout mr-callout--warn' }, [
      icon('warn'),
      `Archiwum ERA5 (Open-Meteo) chwilowo niedostępne${d.fallback_reason ? ` (${d.fallback_reason})` : ''} – użyto zapasowej reanalizy NASA POWER (grubsza siatka ~50 km; wilgotność jako względne nasycenie, nie % objętości).`,
    ]) : null,
    h('p', { class: 'mr-callout mr-callout--info' }, [icon('drop'), summary]),
    chartFigure({
      title: chartTitle,
      summary,
      chip: [
        d.provenance && d.provenance.precip ? provChip(d.provenance.precip) : null,
        d.provenance && d.provenance.normals ? provChip(d.provenance.normals) : null,
      ].filter(Boolean),
      note: chartNote,
      columns: [
        { label: 'Miesiąc' },
        { label: moistColLabel },
        { label: 'Opad [mm]' },
        ...(hasNorm ? [{ label: 'Norma 1991–2020 [mm]' }] : []),
      ],
      rows: rows.map((r, i) => [
        monthLabel(r.month) + (r.complete ? '' : ' (niepełny)'),
        moistVals[i] !== null ? num(moistVals[i], 1) : 'brak',
        nn(r.precip_mm) !== null ? num(r.precip_mm, 1) : 'brak',
        ...(hasNorm ? [nn(r.precip_normal_mm) !== null ? num(r.precip_normal_mm, 1) : 'brak'] : []),
      ]),
      build: (Chart, canvas, t) => ({
        type: 'bar',
        data: {
          labels,
          datasets: [
            {
              type: 'line',
              label: moistSeriesLabel,
              data: moistVals,
              borderColor: t.balance,
              backgroundColor: t.balance,
              borderWidth: 2.5,
              pointRadius: many ? 0 : 2,
              pointHoverRadius: 4,
              tension: 0.3,
              yAxisID: 'y',
              order: 1,
            },
            ...(fc !== null ? [constLine(`Pojemność polowa (PP: ${num(fc, 0)}%)`, rows.length, fc, t.rain, { yAxisID: 'y', borderDash: [6, 4], order: 1 })] : []),
            ...(wp !== null ? [constLine(`Punkt więdnięcia (PTW: ${num(wp, 0)}%)`, rows.length, wp, t.et0, { yAxisID: 'y', borderDash: [4, 4], order: 1 })] : []),
            {
              type: 'bar',
              label: precipSeriesLabel,
              data: rows.map((r) => nn(r.precip_mm)),
              backgroundColor: rows.map((r) => (r.complete ? t.rain : t.rainForecast)),
              borderRadius: 2,
              yAxisID: 'y1',
              order: 3,
            },
            ...(hasNorm ? [refDataset('Norma opadu 1991–2020 (IMGW-PIB) [mm]', rows.map((r) => nn(r.precip_normal_mm)), t.ref, { yAxisID: 'y1', stepped: 'middle', order: 2 })] : []),
          ],
        },
        options: {
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: baseLegend(t) },
          scales: {
            ...baseScales(t, yUnit),
            x: Object.assign({}, baseScales(t, yUnit).x, {
              ticks: Object.assign({}, (baseScales(t, yUnit).x || {}).ticks, {
                autoSkip: true,
                maxTicksLimit: many ? 14 : 18,
                maxRotation: 0,
              }),
            }),
            y1: {
              position: 'right',
              ticks: { color: t.muted },
              grid: { display: false },
              title: { display: true, text: 'opad [mm / mies.]', color: t.muted },
            },
          },
        },
      }),
    }),
  ].filter(Boolean);
}

/** Główna funkcja renderująca zawartość KROKU 3 w [data-ref="water-main"]. */
export function renderWaterStep(panel) {
  if (!panel) return;
  const token = ++renderToken;
  replace(panel, [
    h('div', { class: 'mr-waterstep' }, [
      currentRetentionCard(),
      historicalRetentionCard(token),
    ]),
  ]);
}

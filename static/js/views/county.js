// „Działka na tle powiatu”: NDVI działki na tle pól uprawnych całego powiatu z tego samego dnia (Sentinel-2).
// Dane doczytywane dopiero, gdy sekcja jest widoczna (pierwsze liczenie powiatu trwa do ok. 2 min, potem z pamięci).
import { h, icon, replace, infoTip } from '../dom.js';
import { num, provChip, errorView, fmtDate } from '../format.js';
import { api } from '../api.js';

const cache = new Map();   // klucz → Promise wyniku

function load(key, q) {
  if (!cache.has(key)) {
    const pr = api(`/api/county/ndvi?${q}`, { timeout: 180000 });
    pr.catch(() => cache.delete(key));
    cache.set(key, pr);
  }
  return cache.get(key);
}

function countyName(c) {
  const n = (c && c.name) || '';
  return n.startsWith('powiat') ? n : `powiat ${n}`;
}

function strip(d) {
  const bar = h('div', { class: 'mr-county__bar', 'aria-hidden': 'true' }, d.segments.map((s) => {
    const seg = h('span', { class: 'mr-county__seg', title: `${s.from_pct}–${s.to_pct}% pól: NDVI ok. ${num(s.ndvi, 2)}` });
    seg.style.background = s.color;
    return seg;
  }));
  const med = h('span', { class: 'mr-county__median' }, [h('span', { class: 'mr-county__mlabel', text: `typowe pole ${num(d.median, 2)}` })]);
  med.style.left = '50%';
  bar.append(med);
  if (d.rank_pct !== null && d.rank_pct !== undefined) {
    const pos = Math.max(1, Math.min(99, Number(d.rank_pct)));
    const you = h('span', { class: `mr-county__you${pos > 80 ? ' mr-county__you--right' : pos < 20 ? ' mr-county__you--left' : ''}` },
      [h('span', { class: 'mr-county__ylabel', text: `Twoja działka ${num(d.ndvi, 2)}` })]);
    you.style.left = `${pos}%`;
    bar.append(you);
  }
  return h('div', { class: 'mr-county__strip' }, [
    bar,
    h('div', { class: 'mr-county__ends', 'aria-hidden': 'true' }, [
      h('span', { text: `← najsłabsze pola (NDVI ${num(d.segments[0].ndvi, 2)})` }),
      h('span', { text: `najlepsze pola (NDVI ${num(d.segments[d.segments.length - 1].ndvi, 2)}) →` }),
    ]),
  ]);
}

function view(d) {
  if (!d || d.status !== 'ok' || !Array.isArray(d.segments) || !d.segments.length) {
    const note = d && d.provenance && d.provenance.note;
    return [h('p', { class: 'mr-muted', text: `Brak porównania${note ? ` – ${note}` : '.'}` }), d && d.provenance ? provChip(d.provenance) : null];
  }
  return [
    strip(d),
    h('p', { class: 'mr-hint', text: `${countyName(d.county)}, zdjęcie z ${fmtDate(d.day)}. Pasek to wszystkie pola uprawne powiatu (${num(d.compared_km2, 0)} km² bez chmur) `
      + 'ułożone od najsłabszych do najlepszych; kolor = NDVI jak na mapie. Porównujemy ten sam dzień, więc pogoda i pora roku są te same, '
      + 'ale różne uprawy rosną w różnym tempie (np. jesienią oziminy są zielone, a pola pod uprawy jare – gołe).' }),
    provChip(d.provenance),
  ];
}

export function countySection(p) {
  const nd = p.ndvi;
  const day = nd && nd.acquired_on ? String(nd.acquired_on).slice(0, 10) : null;
  const title = h('h3', { class: 'mr-metric__title' }, ['Działka na tle powiatu ',
    infoTip('działka na tle powiatu', 'Porównujemy NDVI (zieleń roślin) Twojej działki z polami uprawnymi całego powiatu na tym samym zdjęciu Sentinel-2. '
      + 'Pola uprawne wykrywamy automatycznie ze zdjęć z całego sezonu (miejsca, które bywały i gołe, i zielone), w kwadratach 1 ha; '
      + 'łąki, lasy, woda i zabudowa są pomijane.')]);
  if (!Array.isArray(p.centroid) || !day || nd.mean === null || nd.mean === undefined) {
    return h('div', { class: 'mr-county' }, [title, h('p', { class: 'mr-muted', text: 'Brak danych – najpierw potrzebne jest bezchmurne zdjęcie działki.' })]);
  }
  const q = new URLSearchParams({ lat: String(p.centroid[0]), lon: String(p.centroid[1]), day, ndvi: String(nd.mean) });
  const key = q.toString();
  const box = h('div', { class: 'mr-county__body', 'aria-live': 'polite' },
    h('p', { class: 'mr-muted', role: 'status' }, [icon('info'), ' Liczymy pola w powiecie… pierwszy raz to może potrwać do 2 minut.']));
  const run = () => {
    load(key, q).then((d) => replace(box, view(d)))
      .catch((e) => replace(box, errorView(e && e.message ? e.message : 'Nie udało się pobrać danych.', run)));
  };
  const root = h('div', { class: 'mr-county' }, [title, box]);
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver((ents) => {
      if (ents.some((e) => e.isIntersecting)) { io.disconnect(); run(); }
    });
    io.observe(root);
  } else {
    run();
  }
  return root;
}

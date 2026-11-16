// Formatowanie liczb i dat (pl-PL), etykiety proweniencji, stany ładowania/błędu.
import { h, icon, disclosure, nextId } from './dom.js';

const nf = (d) => new Intl.NumberFormat('pl-PL', { maximumFractionDigits: d, minimumFractionDigits: 0 });

export function num(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return null;
  return nf(digits).format(Number(v));
}

/** Liczba z jednostką lub „Brak danych”. */
export function val(v, unit = '', digits = 1) {
  const s = num(v, digits);
  if (s === null) return 'Brak danych';
  return unit ? `${s} ${unit}` : s;
}

export function parseDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtDate(iso, withTime = false) {
  const d = parseDate(iso);
  if (!d) return '';
  const opts = { day: '2-digit', month: '2-digit', year: 'numeric' };
  if (withTime) Object.assign(opts, { hour: '2-digit', minute: '2-digit' });
  return new Intl.DateTimeFormat('pl-PL', opts).format(d);
}

export function fmtTime(iso) {
  const d = parseDate(iso);
  return d ? new Intl.DateTimeFormat('pl-PL', { hour: '2-digit', minute: '2-digit' }).format(d) : '';
}

export function fmtDay(isoDate) {
  const d = parseDate(isoDate + (isoDate && isoDate.length === 10 ? 'T12:00:00' : ''));
  return d ? new Intl.DateTimeFormat('pl-PL', { weekday: 'short', day: 'numeric', month: 'numeric' }).format(d) : isoDate;
}

const KIND_LABEL = {
  pomiar: 'pomiar',
  prognoza: 'prognoza',
  model: 'model',
  reanaliza: 'reanaliza',
  szacunek: 'szacunek',
  statystyka: 'statystyka publiczna / średnia wieloletnia',
  tabelaryczna: 'wartość tabelaryczna (nie pomiar)',
  przepis: 'przepis prawa (próg / termin)',
  zalecenie: 'zalecenie doradztwa rolniczego (ODR) – nie przepis',
  demo: 'DEMO',
  brak: 'brak danych',
};

/** Krótka nazwa źródła do etykiety (pełna w rozwinięciu). */
export function shortSource(src) {
  if (!src) return 'Źródło nieznane';
  if (/sentinel-2/i.test(src) && /wieloletni/i.test(src)) return 'Sentinel-2 · średnia 2019–2025';
  if (/sentinel-2/i.test(src)) return 'Sentinel-2';
  if (/sentinel-1/i.test(src)) return 'Sentinel-1';
  if (/ULDK/i.test(src)) return 'GUGiK ULDK';
  if (/BDOT10k/i.test(src)) return 'GUGiK BDOT10k';
  if (/IMGW/i.test(src) && /norma/i.test(src)) return 'IMGW-PIB · norma 1991–2020';
  if (/IMGW/i.test(src)) return 'IMGW-PIB';
  if (/GUS/i.test(src)) return 'GUS BDL';
  if (/Program azotanowy/i.test(src)) return 'Program azotanowy (Dz.U. 2023/244)';
  if (/doradztwa rolniczego \(ODR\)/i.test(src)) return 'Zalecenie doradztwa (ODR)';
  if (/środków ochrony roślin/i.test(src)) return 'Rozp. MRiRW – środki ochrony roślin';
  if (/FAO-56/i.test(src)) return 'FAO-56 · wartość tabelaryczna';
  if (src.length <= 34) return src;
  const noParen = src.replace(/\s*\(.*?\)\s*/g, ' ').trim();
  return noParen.length <= 34 && noParen ? noParen : noParen.slice(0, 32) + '…';
}

/**
 * Etykieta proweniencji: „Sentinel-2 · 12.11.2026 · zachm. 8%”.
 * kind "brak" → „Brak danych”, demo → plakietka DEMO. Kliknięcie rozwija szczegóły.
 */
export function provChip(p, opts = {}) {
  if (!p) {
    return h('span', { class: 'mr-chip mr-chip--brak' }, [icon('minus'), 'Źródło nieznane']);
  }
  const kind = p.kind || 'brak';
  const parts = [];
  if (kind === 'brak') {
    parts.push('Brak danych');
  } else {
    let name = opts.label || shortSource(p.source);
    if (opts.station) name += ` ${opts.station}`;
    parts.push(name);
    if (p.observed_at) {
      const isToday = new Date().toDateString() === new Date(p.observed_at).toDateString();
      if (opts.time) parts.push(isToday ? fmtTime(p.observed_at) : fmtDate(p.observed_at, true));
      else parts.push(fmtDate(p.observed_at));
    } else if (kind === 'prognoza' && p.fetched_at) {
      parts.push(`z ${fmtDate(p.fetched_at, true)}`);
    }
    if (p.cloud_cover_pct !== null && p.cloud_cover_pct !== undefined) parts.push(`zachm. ${num(p.cloud_cover_pct, 0)}%`);
  }
  const chipText = parts.join(' · ');
  const details = h('dl', { class: 'mr-provdl' }, [
    h('dt', { text: 'Źródło' }), h('dd', null, p.source_url && /^https:\/\//.test(p.source_url)
      ? h('a', { href: p.source_url, target: '_blank', rel: 'noopener noreferrer' }, [p.source || p.source_url, h('span', { class: 'mr-visually-hidden', text: ' (otwiera się w nowej karcie)' })])
      : (p.source || 'nieznane')),
    h('dt', { text: 'Rodzaj' }), h('dd', { text: KIND_LABEL[kind] || kind }),
    p.observed_at ? [h('dt', { text: 'Stan na' }), h('dd', { text: fmtDate(p.observed_at, true) })] : null,
    p.fetched_at ? [h('dt', { text: 'Pobrano' }), h('dd', { text: fmtDate(p.fetched_at, true) })] : null,
    p.resolution ? [h('dt', { text: 'Rozdzielczość' }), h('dd', { text: p.resolution })] : null,
    p.cloud_cover_pct !== null && p.cloud_cover_pct !== undefined ? [h('dt', { text: 'Zachmurzenie' }), h('dd', { text: `${num(p.cloud_cover_pct, 1)}%` })] : null,
    p.note ? [h('dt', { text: 'Uwagi' }), h('dd', { text: p.note })] : null,
  ]);
  const content = [
    kind === 'demo' ? h('span', { class: 'mr-badge mr-badge--demo', text: 'DEMO' }) : null,
    kind === 'brak' ? icon('minus') : null,
    h('span', { text: chipText }),
    h('span', { class: 'mr-visually-hidden', text: ` (${KIND_LABEL[kind] || kind}; pokaż szczegóły źródła)` }),
  ];
  return disclosure(content, details, { buttonClass: `mr-chip mr-chip--${kind}`, wrapClass: 'mr-chipwrap' });
}

export function demoBadge(text = 'DEMO') {
  return h('span', { class: 'mr-badge mr-badge--demo', text });
}

const RISK = {
  Niskie: { cls: 'ok', icon: 'checkCircle', text: 'Ryzyko niskie' },
  Średnie: { cls: 'mid', icon: 'alert', text: 'Ryzyko średnie' },
  Podwyższone: { cls: 'warn', icon: 'warn', text: 'Ryzyko podwyższone' },
  Wysokie: { cls: 'bad', icon: 'warn', text: 'Ryzyko wysokie' },
  'Brak danych': { cls: 'none', icon: 'minus', text: 'Brak danych' },
};
export function riskInfo(risk) {
  return RISK[risk] || RISK['Brak danych'];
}
export function riskBadge(risk) {
  const r = riskInfo(risk);
  return h('span', { class: `mr-status mr-status--${r.cls}` }, [icon(r.icon), r.text]);
}

/** Stan ładowania (z tekstem – nie tylko animacja). */
export function loadingView(text = 'Ładowanie danych…') {
  return h('div', { class: 'mr-state mr-state--loading', role: 'status' }, [
    h('span', { class: 'mr-spinner', 'aria-hidden': 'true' }),
    h('span', { text }),
  ]);
}

/** Stan błędu z przyciskiem „Spróbuj ponownie”. */
export function errorView(err, onRetry, title = 'Nie udało się pobrać danych') {
  const msg = err && err.message ? err.message : String(err || 'Nieznany błąd');
  return h('div', { class: 'mr-state mr-state--error', role: 'alert' }, [
    icon('alert', 'mr-state__ico'),
    h('div', null, [
      h('p', { class: 'mr-state__title', text: title }),
      h('p', { text: msg }),
      onRetry ? h('button', { type: 'button', class: 'mr-btn mr-btn--secondary', onclick: onRetry }, [icon('refresh'), 'Spróbuj ponownie']) : null,
    ]),
  ]);
}

export function emptyView(text, extra) {
  return h('div', { class: 'mr-state mr-state--empty' }, [icon('info', 'mr-state__ico'), h('div', null, [h('p', { text }), extra || null])]);
}

// --- „Źródło”: etykiety źródeł schowane za jednym przyciskiem ---------------------------------------------
// Sąsiednie etykiety (albo kontener .mr-chips) zamieniamy na przycisk „Źródło” / „Źródła (n)”, który rozwija listę
// etykiet; każda etykieta dalej rozwija pełny opis źródła. Etykieta DEMO jest widoczna na samym przycisku.
// Zakładka „Źródła” zostaje bez zmian (tam etykiety są treścią).
function srcLabel(box) {
  const chips = box.querySelectorAll('.mr-src__panel .mr-chipwrap');
  const btn = box.querySelector('.mr-src__btn');
  box.hidden = chips.length === 0;
  if (!btn) return;
  const demo = Array.from(chips).some((c) => c.querySelector('.mr-chip--demo'));
  const text = chips.length > 1 ? `Źródła (${chips.length})` : 'Źródło';
  if (btn.dataset.label === `${text}|${demo}`) return;
  btn.dataset.label = `${text}|${demo}`;
  btn.replaceChildren(...[icon('info'), h('span', { text }), demo ? demoBadge() : null, icon('chevronDown', 'mr-src__chev')].filter(Boolean));
}

export function collapseSources(root) {
  if (!root) return;
  root.querySelectorAll('.mr-src').forEach(srcLabel);          // treść mogła się zmienić (np. nowa klatka w Historii)
  const done = new Set();
  root.querySelectorAll('.mr-chipwrap').forEach((w) => {
    if (done.has(w) || w.closest('.mr-src, #mr-tp-zrodla, [data-sources="open"]')) return;
    const parent = w.parentElement;
    if (!parent) return;
    let group;
    if (parent.classList.contains('mr-chips')) {
      group = [parent];
      parent.querySelectorAll('.mr-chipwrap').forEach((x) => done.add(x));
    } else {
      group = [w];
      for (let n = w.nextElementSibling; n && n.classList.contains('mr-chipwrap'); n = n.nextElementSibling) group.push(n);
      group.forEach((x) => done.add(x));
    }
    const id = nextId('mr-src');
    const panel = h('span', { class: 'mr-src__panel', id, hidden: true });
    const btn = h('button', { type: 'button', class: 'mr-src__btn', 'aria-expanded': 'false', 'aria-controls': id,
      onclick: () => {
        const open = btn.getAttribute('aria-expanded') === 'true';
        btn.setAttribute('aria-expanded', String(!open));
        panel.hidden = open;
      } });
    const box = h('span', { class: 'mr-src' }, [btn, panel]);
    group[0].before(box);
    if (group[0].classList.contains('mr-chips')) panel.append(group[0]);        // kontener zostaje (widoki trzymają do niego odwołanie)
    else panel.append(h('span', { class: 'mr-chips' }, group));
    srcLabel(box);
  });
}

/** Obserwuje aplikację i chowa nowe etykiety źródeł za przyciskiem „Źródło”. */
export function watchSources(root) {
  if (!root || typeof MutationObserver === 'undefined') return;
  let queued = false;
  const run = () => { queued = false; collapseSources(root); };
  new MutationObserver(() => { if (!queued) { queued = true; queueMicrotask(run); } }).observe(root, { childList: true, subtree: true });
  collapseSources(root);
}

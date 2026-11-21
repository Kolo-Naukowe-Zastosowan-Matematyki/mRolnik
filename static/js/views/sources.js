// Zakładka „Źródła”: stan usług (/api/sources/status), źródła użyte w analizie, podkłady mapy, prywatność.
import { h, icon, replace } from '../dom.js';
import { api } from '../api.js';
import { state } from '../state.js';
import { fmtDate, loadingView, errorView, demoBadge } from '../format.js';

const KIND = { pomiar: 'pomiar', prognoza: 'prognoza', model: 'model', reanaliza: 'reanaliza', szacunek: 'szacunek', statystyka: 'statystyka publiczna', tabelaryczna: 'wartość tabelaryczna', przepis: 'przepis prawa', zalecenie: 'zalecenie doradztwa (ODR)', demo: 'dane demonstracyjne', brak: 'brak danych' };

function link(url, text) {
  if (!url || !/^https:\/\//.test(url)) return h('span', { text: text || '' });
  return h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, [text || url, h('span', { class: 'mr-visually-hidden', text: ' (otwiera się w nowej karcie)' })]);
}

// --- Źródła użyte w analizie: proweniencja z bieżącej analizy (gospodarstwo) i wyniku filtra działek ------------------
/** Wszystkie obiekty proweniencji ({source, source_url, kind, observed_at, fetched_at}) w danych – zgrupowane po źródle. */
export function collectProvenance(...roots) {
  const by = new Map();
  const seen = new Set();
  const walk = (o, depth) => {
    if (!o || typeof o !== 'object' || depth > 12 || seen.has(o)) return;
    seen.add(o);
    if (Array.isArray(o)) { o.forEach((v) => walk(v, depth + 1)); return; }
    if (typeof o.source === 'string' && typeof o.kind === 'string' && 'source_url' in o) {
      const key = `${o.source}|${o.source_url || ''}`;
      const g = by.get(key) || { name: o.source, url: o.source_url, kinds: new Set(), observed: [], fetched: [], count: 0 };
      g.count += 1;
      g.kinds.add(o.kind);
      if (o.observed_at) g.observed.push(String(o.observed_at));
      if (o.fetched_at) g.fetched.push(String(o.fetched_at));
      by.set(key, g);
      return;
    }
    Object.values(o).forEach((v) => walk(v, depth + 1));
  };
  roots.forEach((r) => walk(r, 0));
  return [...by.values()].map((g) => {
    const obs = g.observed.sort();
    const fet = g.fetched.sort();
    const kinds = [...g.kinds];
    const real = kinds.filter((k) => k !== 'brak');
    return { name: g.name, url: g.url, kind: real[0] || 'brak', missing: !real.length,
      from: obs[0] || null, to: obs[obs.length - 1] || null, fetched: fet[fet.length - 1] || null, count: g.count };
  }).sort((a, b) => Number(a.missing) - Number(b.missing) || a.name.localeCompare(b.name, 'pl'));
}

function dataDate(s) {
  if (s.missing) return 'brak danych';
  if (s.from && s.to && s.from.slice(0, 10) !== s.to.slice(0, 10)) return `dane z ${fmtDate(s.from)} – ${fmtDate(s.to)}`;
  if (s.from) return `dane z ${fmtDate(s.from)}`;
  return s.fetched ? `pobrano ${fmtDate(s.fetched, true)}` : 'data danych nieznana';
}

// --- Kropka stanu usług (karta „Stan usług danych” i nagłówek aplikacji) --------------------------------------------
const KEY_SOURCES = /ULDK|Copernicus|CDSE|EGiB|BDOT10k – obiekty/i;   // bez nich nie działa analiza działek ani filtr

/** {level: ok|warn|bad|none, text, bad: [źródła z błędem]} z listy /api/sources/status. */
export function statusSummary(list) {
  const checked = (list || []).filter((s) => s.configured && s.checked);
  const bad = checked.filter((s) => !s.ok);
  if (!checked.length) return { level: 'none', text: 'Stan usług nie był jeszcze sprawdzany', bad };
  if (bad.some((s) => KEY_SOURCES.test(s.name || ''))) return { level: 'bad', text: 'Brak połączenia z kluczowymi usługami', bad };
  if (bad.length) return { level: 'warn', text: `Część usług z problemami (${bad.length})`, bad };
  return { level: 'ok', text: 'Wszystko działa', bad };
}

let lastStatus = null;
const dotListeners = new Set();
function publishStatus(list) {
  lastStatus = statusSummary(list);
  dotListeners.forEach((fn) => fn(lastStatus));
}

/** Mała kropka w nagłówku aplikacji (przed `slot`); kliknięcie prowadzi do karty „Stan usług danych”. */
export function initStatusDot(slot, goto) {
  if (!slot) return;
  const label = h('span', { class: 'mr-visually-hidden', text: 'Stan usług danych' });
  const btn = h('button', { type: 'button', class: 'mr-statusdot mr-statusdot--none', title: 'Stan usług danych', onclick: goto },
    [h('span', { class: 'mr-statusdot__dot', 'aria-hidden': 'true' }), label]);
  slot.before(btn);
  const draw = (st) => {
    btn.className = `mr-statusdot mr-statusdot--${st.level}`;
    btn.title = `Stan usług danych: ${st.text}`;
    label.textContent = `Stan usług danych: ${st.text}`;
  };
  dotListeners.add(draw);
  if (lastStatus) draw(lastStatus);
  api('/api/sources/status', { timeout: 20000 }).then((d) => publishStatus(Array.isArray(d) ? d : (d && d.sources) || [])).catch(() => {});
}

export function renderSources(panel) {
  const statusBox = h('div', { class: 'mr-card__body' }, loadingView('Sprawdzanie stanu źródeł…'));
  const checkBtn = h('button', { type: 'button', class: 'mr-btn mr-btn--secondary mr-btn--small', onclick: () => load(true) }, [icon('refresh'), 'Sprawdź teraz']);
  const stamp = h('p', { class: 'mr-hint' });
  const msgs = h('div', { class: 'mr-src__msgs', id: 'mr-src-msgs', hidden: true });
  const big = h('button', { type: 'button', class: 'mr-bigdot mr-bigdot--none', 'aria-expanded': 'false', 'aria-controls': 'mr-src-msgs',
    onclick: () => { const open = big.getAttribute('aria-expanded') === 'true'; big.setAttribute('aria-expanded', String(!open)); msgs.hidden = open; } },
  [h('span', { class: 'mr-bigdot__dot', 'aria-hidden': 'true' }), h('span', { class: 'mr-bigdot__txt', text: 'Sprawdzam…' })]);
  const drawBig = (list) => {
    const st = statusSummary(list);
    big.className = `mr-bigdot mr-bigdot--${st.level}`;
    big.querySelector('.mr-bigdot__txt').textContent = st.text;
    const rows = st.bad.map((x) => h('li', null, [h('strong', { text: x.name }), h('span', { class: 'mr-muted',
      text: ` – ${x.message || 'błąd'}${x.last_success ? ` (ostatnio OK: ${fmtDate(x.last_success, true)})` : ' (brak udanego pobrania w tej sesji serwera)'}` })]));
    replace(msgs, rows.length ? h('ul', { class: 'mr-plainlist' }, rows)
      : h('p', { class: 'mr-muted', text: 'Brak komunikatów o błędach – wszystkie sprawdzone usługi odpowiadają.' }));
    publishStatus(list);
  };
  let autoChecked = false;

  const draw = (list, checking) => {
    replace(statusBox, list.length ? h('ul', { class: 'mr-srclist' }, list.map((s) => {
      // trzy stany: działa / błąd (ostatnie zapytanie się nie udało) / nie sprawdzono (brak zapytań w tej sesji serwera)
      let st;
      if (!s.configured) st = ['none', 'minus', 'nie skonfigurowano'];
      else if (!s.checked) st = checking ? ['none', 'refresh', 'sprawdzam…'] : ['none', 'minus', 'nie sprawdzono'];
      else if (s.ok) st = ['ok', 'checkCircle', 'działa'];
      else st = ['bad', 'warn', 'błąd'];
      const msg = !s.checked && s.configured
        ? (checking ? 'Trwa zapytanie testowe…' : 'Nie było jeszcze zapytania w tej sesji serwera (dane mogły pochodzić z pamięci podręcznej).')
        : [s.last_success ? `ostatnio OK: ${fmtDate(s.last_success, true)}` : 'brak udanego pobrania', s.message && s.message !== 'OK' ? s.message : null].filter(Boolean).join(' · ');
      return h('li', { class: 'mr-src' }, [
        h('div', { class: 'mr-src__head' }, [h('strong', { text: s.name || 'Źródło' }), h('span', { class: `mr-status mr-status--${st[0]}` }, [icon(st[1]), st[2]])]),
        h('p', { class: 'mr-muted', text: msg }),
      ]);
    })) : h('p', { class: 'mr-muted', text: 'Serwer nie zwrócił listy źródeł.' }));
  };

  const load = (check = false) => {
    checkBtn.disabled = true;
    if (check) stamp.textContent = 'Wykonuję zapytania testowe do wszystkich źródeł (do ok. 15 s)…';
    else replace(statusBox, loadingView('Sprawdzanie stanu źródeł…'));
    api(`/api/sources/status${check ? '?check=true' : ''}`, { timeout: check ? 30000 : 20000 }).then((d) => {
      const list = Array.isArray(d) ? d : (d && Array.isArray(d.sources) ? d.sources : []);
      const unchecked = list.some((s) => s.configured && !s.checked);
      // pierwsze wejście: jeśli coś nie było jeszcze odpytywane, od razu sprawdź wszystkie źródła
      const auto = !check && unchecked && !autoChecked;
      draw(list, auto);
      if (!auto) drawBig(list);
      checkBtn.disabled = false;
      if (check) stamp.textContent = `Sprawdzono: ${new Intl.DateTimeFormat('pl-PL', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date())}.`;
      if (auto) { autoChecked = true; load(true); }
    }).catch((err) => { checkBtn.disabled = false; stamp.textContent = ''; replace(statusBox, errorView(err, () => load(true), 'Nie udało się sprawdzić źródeł')); });
  };
  load();

  const used = state.analysis && Array.isArray(state.analysis.sources) ? state.analysis.sources : [];
  const prov = collectProvenance(state.analysis, state.filterResult);
  const what = [state.analysis ? 'analizy działek' : null, state.filterResult ? 'wyniku filtra działek' : null].filter(Boolean).join(' i ');
  replace(panel, [
    h('section', { class: 'mr-card', 'aria-labelledby': 'mr-src-st' }, [
      h('h2', { class: 'mr-card__title', id: 'mr-src-st' }, [icon('refresh'), 'Stan usług danych']),
      h('div', { class: 'mr-src__bar' }, [checkBtn, stamp, big]),
      msgs,
      statusBox,
    ]),
    h('section', { class: 'mr-card', 'aria-labelledby': 'mr-src-used' }, [
      h('h2', { class: 'mr-card__title', id: 'mr-src-used' }, [icon('satellite'), 'Źródła użyte w analizie']),
      prov.length ? h('p', { class: 'mr-hint', text: `Na podstawie ${what}: źródło, rodzaj danych i data danych.` }) : null,
      prov.length ? h('ul', { class: 'mr-srclist' }, prov.map((s) => h('li', { class: 'mr-src' }, [
        h('div', { class: 'mr-src__head' }, [link(s.url, s.name || s.url), s.kind === 'demo' ? demoBadge() : h('span', { class: 'mr-muted', text: KIND[s.kind] || s.kind || '' })]),
        h('p', { class: 'mr-muted', text: dataDate(s) }),
      ]))) : h('p', { class: 'mr-muted', text: 'Źródła pojawią się po analizie działek.' }),
      used.length ? h('details', { class: 'mr-src__lic' }, [
        h('summary', { text: 'Licencje i atrybucja źródeł' }),
        h('ul', { class: 'mr-plainlist' }, used.map((s) => h('li', null, [link(s.url, s.name || s.url), s.licence ? h('span', { class: 'mr-muted', text: ` – ${s.licence}` }) : null]))),
      ]) : null,
    ]),
    h('section', { class: 'mr-card', 'aria-labelledby': 'mr-src-map' }, [
      h('h2', { class: 'mr-card__title', id: 'mr-src-map' }, [icon('layers'), 'Podkłady mapy']),
      h('ul', { class: 'mr-plainlist' }, [
        h('li', null, [link('https://www.geoportal.gov.pl/', 'Ortofotomapa – GUGiK'), ' (dane otwarte, usługa WMS)']),
        h('li', null, [link('https://www.openstreetmap.org/copyright', 'OpenStreetMap'), ' (licencja ODbL)']),
        h('li', null, [link('https://www.geoportal.gov.pl/', 'Cieki i rowy – BDOT10k, GUGiK'), ' (dane otwarte, usługa WMS)']),
      ]),
    ]),
    h('section', { class: 'mr-card', 'aria-labelledby': 'mr-src-priv' }, [
      h('h2', { class: 'mr-card__title', id: 'mr-src-priv' }, [icon('info'), 'Prywatność']),
      h('p', { text: 'Współrzędne działek są wysyłane do usług danych (Copernicus, IMGW-PIB, GUGiK, Open-Meteo) wyłącznie w celu analizy. Dodane działki są przechowywane tylko w tej karcie przeglądarki – zapisz je do pliku CSV, jeśli chcesz wrócić do nich później.' }),
    ]),
    h('section', { class: 'mr-card', 'aria-labelledby': 'mr-src-a11y' }, [
      h('h2', { class: 'mr-card__title', id: 'mr-src-a11y' }, [icon('info'), 'O aplikacji i dostępność']),
      h('p', null, [h('strong', { text: 'Prototyp HackNation – dane informacyjne. ' }), 'Nie jest to oficjalna usługa KOWR ani ARiMR; wyniki nie zastępują doradztwa ani analizy gleby.']),
      h('p', null, [h('a', { href: '/static/deklaracja-dostepnosci.html' }, 'Deklaracja dostępności')]),
    ]),
  ]);
}

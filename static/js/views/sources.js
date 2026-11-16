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

export function renderSources(panel) {
  const statusBox = h('div', { class: 'mr-card__body' }, loadingView('Sprawdzanie stanu źródeł…'));
  const checkBtn = h('button', { type: 'button', class: 'mr-btn mr-btn--secondary mr-btn--small', onclick: () => load(true) }, [icon('refresh'), 'Sprawdź teraz']);
  const stamp = h('p', { class: 'mr-hint' });
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
      checkBtn.disabled = false;
      if (check) stamp.textContent = `Sprawdzono: ${new Intl.DateTimeFormat('pl-PL', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date())}.`;
      if (auto) { autoChecked = true; load(true); }
    }).catch((err) => { checkBtn.disabled = false; stamp.textContent = ''; replace(statusBox, errorView(err, () => load(true), 'Nie udało się sprawdzić źródeł')); });
  };
  load();

  const used = state.analysis && Array.isArray(state.analysis.sources) ? state.analysis.sources : [];
  replace(panel, [
    h('section', { class: 'mr-card', 'aria-labelledby': 'mr-src-st' }, [
      h('h2', { class: 'mr-card__title', id: 'mr-src-st' }, [icon('refresh'), 'Stan usług danych']),
      h('div', { class: 'mr-src__bar' }, [checkBtn, stamp]),
      statusBox,
    ]),
    h('section', { class: 'mr-card', 'aria-labelledby': 'mr-src-used' }, [
      h('h2', { class: 'mr-card__title', id: 'mr-src-used' }, [icon('satellite'), 'Źródła użyte w analizie']),
      used.length ? h('ul', { class: 'mr-srclist' }, used.map((s) => h('li', { class: 'mr-src' }, [
        h('div', { class: 'mr-src__head' }, [link(s.url, s.name || s.url), s.kind === 'demo' ? demoBadge() : h('span', { class: 'mr-muted', text: KIND[s.kind] || s.kind || '' })]),
        s.licence ? h('p', { class: 'mr-muted', text: `Licencja: ${s.licence}` }) : null,
      ]))) : h('p', { class: 'mr-muted', text: 'Źródła pojawią się po analizie działek.' }),
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
  ]);
}

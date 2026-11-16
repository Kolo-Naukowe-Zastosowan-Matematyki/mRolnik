// Sekcje zakładki: mały pasek pod zakładkami z nazwami sekcji + karty sekcji jako rozwijane nagłówki.
// Karta zostaje w DOM (widoki dalej dopisują do niej treść); zwinięta karta ukrywa wszystko poza nagłówkiem (CSS).
// Domyślnie sekcje są zwinięte; stan rozwinięcia pamiętamy do przeładowania strony.
import { h, icon } from './dom.js';
import { redrawCharts } from './charts.js';

const openState = new Map();   // id nagłówka → rozwinięta?

function setOpen(card, btn, navBtn, open) {
  card.classList.toggle('mr-sec--closed', !open);
  btn.setAttribute('aria-expanded', String(open));
  if (navBtn) navBtn.setAttribute('aria-expanded', String(open));
  openState.set(btn.dataset.sec, open);
  if (open) requestAnimationFrame(() => { redrawCharts(); window.dispatchEvent(new Event('resize')); });
}

/**
 * Zamienia karty o podanych nagłówkach w rozwijane sekcje i wstawia nad nimi pasek nawigacji.
 * sections: [{ id: 'id nagłówka h2', label: 'krótka nazwa w pasku' }]
 */
export function sectionize(panel, sections, ariaLabel) {
  const items = [];
  sections.forEach(({ id, label }) => {
    const head = panel.querySelector(`#${CSS.escape(id)}`);
    const card = head && head.closest('.mr-card');
    if (!card || head.dataset.sec) return;
    if (!card.id) card.id = `${id}-sec`;
    const btn = h('button', { type: 'button', class: 'mr-sec__toggle', 'aria-expanded': 'false', 'aria-controls': card.id, dataset: { sec: id } },
      [...Array.from(head.childNodes), icon('chevronDown', 'mr-sec__chev')]);
    head.dataset.sec = id;
    head.replaceChildren(btn);
    card.classList.add('mr-sec');
    items.push({ id, label, card, btn });
  });
  if (!items.length) return null;
  const nav = h('nav', { class: 'mr-subnav', 'aria-label': ariaLabel || 'Sekcje zakładki' },
    h('ul', null, items.map((it) => {
      it.navBtn = h('button', { type: 'button', class: 'mr-subnav__btn', 'aria-controls': it.card.id, 'aria-expanded': 'false',
        onclick: () => {
          setOpen(it.card, it.btn, it.navBtn, true);
          it.card.scrollIntoView({ behavior: 'smooth', block: 'start' });
          it.btn.focus({ preventScroll: true });
        } }, it.label);
      return h('li', null, it.navBtn);
    })));
  items.forEach((it) => {
    it.btn.addEventListener('click', () => setOpen(it.card, it.btn, it.navBtn, it.btn.getAttribute('aria-expanded') !== 'true'));
    setOpen(it.card, it.btn, it.navBtn, openState.get(it.id) === true);
  });
  panel.prepend(nav);
  return nav;
}

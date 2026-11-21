// Pomocnicze funkcje DOM. Zasada: dane wyłącznie przez textContent / atrybuty – nigdy innerHTML.

const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * Tworzy element: h('p', {class: 'x', onclick: fn}, ['tekst', dziecko]).
 * Atrybuty on* są podpinane przez addEventListener (CSP bez 'unsafe-inline').
 */
export function h(tag, attrs, children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith('on') && typeof v === 'function') {
        el.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (k === 'class') {
        el.className = v;
      } else if (k === 'dataset') {
        Object.assign(el.dataset, v);
      } else if (k === 'text') {
        el.textContent = String(v);
      } else if (k === 'style') {
        throw new Error('Użyj klas CSS zamiast atrybutu style');
      } else if (v === true) {
        el.setAttribute(k, '');
      } else {
        el.setAttribute(k, String(v));
      }
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  if (children === null || children === undefined || children === false) return el;
  if (!Array.isArray(children)) children = [children];
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function replace(el, children) {
  clear(el);
  return append(el, children);
}

// Ikony: proste ścieżki SVG 24×24 (własne, bez emoji). Zawsze aria-hidden + tekst obok.
const ICONS = {
  info: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2zm0-8h-2V7h2z',
  check: 'M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z',
  checkCircle: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-2 15-5-5 1.4-1.4 3.6 3.6 7.6-7.6L19 8z',
  warn: 'M1 21h22L12 2zm12-3h-2v-2h2zm0-4h-2v-4h2z',
  alert: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-2h2zm0-4h-2V7h2z',
  minus: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm5 11H7v-2h10z',
  cross: 'M19 6.4 17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z',
  sun: 'M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM11 1h2v3h-2zm0 19h2v3h-2zM1 11h3v2H1zm19 0h3v2h-3zM4.2 5.6l1.4-1.4 2.1 2.1-1.4 1.4zm12.1 12.1 1.4-1.4 2.1 2.1-1.4 1.4zM4.2 18.4l2.1-2.1 1.4 1.4-2.1 2.1zM16.3 6.3l2.1-2.1 1.4 1.4-2.1 2.1z',
  moon: 'M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z',
  drop: 'M12 2s-7 8-7 13a7 7 0 0 0 14 0c0-5-7-13-7-13z',
  leaf: 'M17 8C8 10 5.9 16.2 3.8 21.3l1.9.7 1-2.3c.5.2 1 .3 1.3.3C19 20 22 3 22 3c-1 2-8 2.3-13 3.3S2 11.5 2 13.5s1.8 3.8 1.8 3.8C7 8 17 8 17 8z',
  cloud: 'M19.4 10A7.5 7.5 0 0 0 5.3 8 6 6 0 0 0 6 20h13a5 5 0 0 0 .4-10z',
  wind: 'M4 10h11a3 3 0 1 0-3-3h2a1 1 0 1 1 1 1H4zm0 4h15a3 3 0 1 1-3 3h2a1 1 0 1 0 1-1H4z',
  thermo: 'M15 13V5a3 3 0 0 0-6 0v8a5 5 0 1 0 6 0zm-3-9a1 1 0 0 1 1 1v3h-2V5a1 1 0 0 1 1-1z',
  search: 'M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.18-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z',
  waves: 'M2 5c2 0 3-2 5-2s3 2 5 2 3-2 5-2 3 2 5 2v2c-2 0-3-2-5-2s-3 2-5 2-3-2-5-2-3 2-5 2zM2 11c2 0 3-2 5-2s3 2 5 2 3-2 5-2 3 2 5 2v2c-2 0-3-2-5-2s-3 2-5 2-3-2-5-2-3 2-5 2zM2 17c2 0 3-2 5-2s3 2 5 2 3-2 5-2 3 2 5 2v2c-2 0-3-2-5-2s-3 2-5 2-3-2-5-2-3 2-5 2z',
  fitAll: 'M5 15H3v4a2 2 0 0 0 2 2h4v-2H5zM5 5h4V3H5a2 2 0 0 0-2 2v4h2zm14-2h-4v2h4v4h2V5a2 2 0 0 0-2-2zm0 16h-4v2h4a2 2 0 0 0 2-2v-4h-2zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  edit: 'M3 17.25V21h3.75L17.81 9.94l-3.75-3.75zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75z',
  pin: 'M12 2a7 7 0 0 0-7 7c0 5 7 13 7 13s7-8 7-13a7 7 0 0 0-7-7zm0 9.5a2.5 2.5 0 1 1 0-5 2.5 2.5 0 0 1 0 5z',
  plus: 'M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z',
  chevronDown: 'M7.4 8.6 12 13.2l4.6-4.6L18 10l-6 6-6-6z',
  upload: 'M5 20h14v-2H5zm7-18-5.5 5.5 1.4 1.4L11 5.8V16h2V5.8l3.1 3.1 1.4-1.4z',
  download: 'M5 20h14v-2H5zM19 9h-4V3H9v6H5l7 7z',
  refresh: 'M17.6 6.4A8 8 0 1 0 19.7 14h-2.1a6 6 0 1 1-1.4-6.2L13 11h7V4z',
  calc: 'M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm0 2v4h10V4zm0 6v2h2v-2zm4 0v2h2v-2zm4 0v6h2v-6zm-8 4v2h2v-2zm4 0v2h2v-2zm-4 4v2h2v-2zm4 0v2h2v-2z',
  layers: 'm12 16 7.4-5.7L21 9l-9-7-9 7 1.6 1.3zm0 2.5-7.4-5.7L3 14l9 7 9-7-1.6-1.2z',
  external: 'M14 3v2h3.6l-9.8 9.8 1.4 1.4L19 6.4V10h2V3zm5 16H5V5h7V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7h-2z',
  trash: 'M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6zM19 4h-3.5l-1-1h-5l-1 1H5v2h14z',
  photo: 'M19 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm0 16H5V5h14zm-5.04-6.71-2.75 3.54-1.96-2.36L6.5 17h11z',
  satellite: 'm4 7 3-3 4 4-3 3zm9 9 3-3 4 4-3 3zM9.5 9.5l5 5-2 2-5-5zM15 3a6 6 0 0 1 6 6h-2a4 4 0 0 0-4-4zm0 3a3 3 0 0 1 3 3h-2a1 1 0 0 0-1-1z',
  filter: 'M10 18h4v-2h-4zM3 6v2h18V6zm3 7h12v-2H6z',
  minimize: 'M5 17h14v2H5z',
  price: 'M21.4 11.6 12.4 2.6A2 2 0 0 0 11 2H4a2 2 0 0 0-2 2v7c0 .55.22 1.05.59 1.42l9 9a2 2 0 0 0 2.82 0l7-7a2 2 0 0 0 0-2.82zM6.5 8a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z',
};

export function icon(name, cls) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('class', 'mr-ico' + (cls ? ' ' + cls : ''));
  const p = document.createElementNS(SVG_NS, 'path');
  p.setAttribute('d', ICONS[name] || ICONS.info);
  svg.appendChild(p);
  return svg;
}

let uid = 0;
export function nextId(prefix) {
  uid += 1;
  return `${prefix || 'mr'}-${uid}`;
}

/** Przycisk rozwijający (wzorzec disclosure) – dostępny z klawiatury, działa na dotyk. */
export function disclosure(buttonContent, bodyContent, opts = {}) {
  const id = nextId('mr-disc');
  const body = h('div', { class: 'mr-pop' + (opts.bodyClass ? ' ' + opts.bodyClass : ''), id, hidden: true }, bodyContent);
  const btn = h('button', {
    type: 'button',
    class: opts.buttonClass || 'mr-infobtn',
    'aria-expanded': 'false',
    'aria-controls': id,
    'aria-label': opts.label || null,
    onclick: () => {
      const open = btn.getAttribute('aria-expanded') === 'true';
      btn.setAttribute('aria-expanded', String(!open));
      body.hidden = open;
    },
  }, buttonContent);
  body.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { btn.click(); btn.focus(); }
  });
  return h('span', { class: 'mr-disc' + (opts.wrapClass ? ' ' + opts.wrapClass : '') }, [btn, body]);
}

/** Ikona „i” z wyjaśnieniem pojęcia. */
export function infoTip(label, text) {
  return disclosure(icon('info'), h('span', { text }), { label: `Wyjaśnienie: ${label}`, buttonClass: 'mr-infobtn' });
}

export function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// Bezpieczny import/eksport CSV działek.
// Import: obsługa cudzysłowów, separatora ; lub , (Excel PL), przecinka dziesiętnego,
// walidacja współrzędnych w granicach Polski; wiersze bez współrzędnych są ODRZUCANE (bez losowania).
// Eksport: neutralizacja wstrzykiwania formuł (=, +, -, @, tab, CR → poprzedzone apostrofem).

export const PL_BBOX = { latMin: 49.0, latMax: 55.0, lonMin: 14.0, lonMax: 24.2 };
export const MAX_PARCELS = 50;
const MAX_STR = 120;
const MAX_BYTES = 256 * 1024;

/** Parser CSV (RFC 4180 + typowe odstępstwa). Zwraca tablicę wierszy (tablic pól). */
export function parseCsv(text, delimiter) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  const d = delimiter || detectDelimiter(text);
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 1; } else { inQuotes = false; }
      } else {
        field += c;
      }
    } else if (c === '"' && field === '') {
      inQuotes = true;
    } else if (c === d) {
      row.push(field); field = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i += 1;
      row.push(field); field = '';
      rows.push(row); row = [];
    } else {
      field += c;
    }
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((f) => f.trim() !== ''));
}

export function detectDelimiter(text) {
  const first = text.split(/\r?\n/, 1)[0] || '';
  const semi = (first.match(/;/g) || []).length;
  const comma = (first.match(/,/g) || []).length;
  const tab = (first.match(/\t/g) || []).length;
  if (tab > semi && tab > comma) return '\t';
  return semi >= comma && semi > 0 ? ';' : ',';
}

const HEADER_ALIASES = {
  lat: ['lat', 'szerokosc', 'szerokość', 'latitude', 'y'],
  lon: ['lon', 'lng', 'dlugosc', 'długość', 'longitude', 'x'],
  id_tag: ['id_tag', 'nazwa', 'name', 'opis', 'dzialka', 'działka'],
  crop: ['crop', 'uprawa'],
  soil: ['soil', 'gleba'],
  parcel_id: ['parcel_id', 'teryt', 'id_dzialki', 'identyfikator'],
};

function parseNum(s) {
  if (s === undefined || s === null) return NaN;
  const t = String(s).trim().replace(/\s/g, '').replace(',', '.');
  if (!/^-?\d+(\.\d+)?$/.test(t)) return NaN;
  return Number(t);
}

function cleanStr(s) {
  // usuwamy znaki sterujące; limit długości wg kontraktu API
  return String(s || '').replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, MAX_STR);
}

export function inPoland(lat, lon) {
  return lat >= PL_BBOX.latMin && lat <= PL_BBOX.latMax && lon >= PL_BBOX.lonMin && lon <= PL_BBOX.lonMax;
}

/**
 * Importuje działki z tekstu CSV. Zwraca {parcels, errors}.
 * Wymagane kolumny: lat, lon (lub polskie odpowiedniki). Bez nagłówka nie zgadujemy kolumn.
 */
export function importParcels(text) {
  const errors = [];
  if (text.length > MAX_BYTES) return { parcels: [], errors: ['Plik jest za duży (maks. 256 KB).'] };
  const clean = text.replace(/^﻿/, '');
  const rows = parseCsv(clean);
  if (rows.length < 2) return { parcels: [], errors: ['Plik nie zawiera nagłówka i co najmniej jednego wiersza danych.'] };
  const header = rows[0].map((h) => h.trim().toLowerCase());
  const col = {};
  for (const [key, aliases] of Object.entries(HEADER_ALIASES)) {
    const idx = header.findIndex((h) => aliases.includes(h));
    if (idx >= 0) col[key] = idx;
  }
  if (col.lat === undefined || col.lon === undefined) {
    return { parcels: [], errors: ['Brak kolumn ze współrzędnymi (lat i lon). Działek bez współrzędnych nie dodajemy.'] };
  }
  const parcels = [];
  rows.slice(1).forEach((r, i) => {
    const line = i + 2;
    const lat = parseNum(r[col.lat]);
    const lon = parseNum(r[col.lon]);
    if (Number.isNaN(lat) || Number.isNaN(lon)) {
      errors.push(`Wiersz ${line}: brak lub niepoprawne współrzędne – pominięto.`);
      return;
    }
    if (!inPoland(lat, lon)) {
      errors.push(`Wiersz ${line}: współrzędne poza granicami Polski – pominięto.`);
      return;
    }
    if (parcels.length >= MAX_PARCELS) {
      errors.push(`Wiersz ${line}: przekroczono limit ${MAX_PARCELS} działek – pominięto.`);
      return;
    }
    const p = { lat: Math.round(lat * 1e6) / 1e6, lon: Math.round(lon * 1e6) / 1e6 };
    for (const k of ['id_tag', 'crop', 'soil', 'parcel_id']) {
      if (col[k] !== undefined && r[col[k]] !== undefined && cleanStr(r[col[k]])) p[k] = cleanStr(r[col[k]]);
    }
    if (!p.id_tag) p.id_tag = `Działka ${parcels.length + 1}`;
    parcels.push(p);
  });
  return { parcels, errors };
}

/** Neutralizacja formuł arkusza (CSV injection). */
export function safeCell(v) {
  if (v === null || v === undefined) return '';
  let s = String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  if (/[";\n\r,]/.test(s)) s = `"${s.replace(/"/g, '""')}"`;
  return s;
}

/** Eksport do CSV (separator ; i BOM – poprawnie otwiera się w Excelu PL). */
export function exportCsv(columns, rows) {
  const head = columns.map((c) => safeCell(c.label)).join(';');
  const body = rows.map((r) => columns.map((c) => {
    const v = typeof c.get === 'function' ? c.get(r) : r[c.key];
    return typeof v === 'number' ? String(v).replace('.', ',') : safeCell(v);
  }).join(';'));
  return '﻿' + [head, ...body].join('\r\n') + '\r\n';
}

export function downloadText(filename, text, mime = 'text/csv;charset=utf-8') {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.hidden = true;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
}

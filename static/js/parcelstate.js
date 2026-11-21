// Stan działki w dwóch prostych ikonach (zamiast ogólnego „ryzyka”):
//  – roślinka: kondycja roślin z NDVI (Sentinel-2), oceniana tylko wtedy, gdy uprawa o tej porze roku powinna być zielona,
//  – kropelka: wilgotność gleby z modelu (między punktem więdnięcia a pojemnością polową = w normie).
// Te same reguły liczą komunikaty w zakładce „Plony”.
import { h, icon } from './dom.js';
import { num } from './format.js';

const isNum = (v) => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
const lc = (s) => String(s || '').toLowerCase();
const AUTUMN = ['ozim', 'rzepak'];
const PERENNIAL = ['tuz', 'trwały użytek', 'łąk', 'pastwisk', 'sad', 'wieloletn'];
export const WEAK_NDVI = 0.4;

/** Czy uprawa w tym miesiącu powinna mieć zielony łan (wtedy niskie NDVI to sygnał problemu, a nie np. ściernisko). */
export function inGreenSeason(crop, month = new Date().getMonth() + 1) {
  const c = lc(crop);
  if (!c) return false;
  if (AUTUMN.some((k) => c.includes(k))) return month >= 4 && month <= 6;                    // oziminy: wiosną po ruszeniu wegetacji
  if (PERENNIAL.some((k) => c.includes(k))) return month >= 5 && month <= 9;                 // łąki i pastwiska
  if (c.includes('kukurydz') || c.includes('burak') || c.includes('ziemniak') || c.includes('soj')) return month >= 7 && month <= 8;
  return month === 6;                                                                         // zboża jare: czerwiec (przed dojrzewaniem)
}

/** Kondycja roślin: {st: 'ok' | 'bad' | 'none', text}. */
export function plantState(p, month = new Date().getMonth() + 1) {
  const v = p && p.ndvi && isNum(p.ndvi.mean) ? Number(p.ndvi.mean) : null;
  if (v === null) return { st: 'none', text: 'Kondycja roślin: brak bezchmurnego zdjęcia satelitarnego' };
  if (v >= WEAK_NDVI) return { st: 'ok', text: `Kondycja roślin dobra (NDVI ${num(v, 2)})`, v };
  if (inGreenSeason(p.crop, month)) {
    return { st: 'bad', text: `Słabe rośliny (NDVI ${num(v, 2)}) – o tej porze roku ta uprawa powinna być wyraźnie zielona`, v };
  }
  return { st: 'none', text: `Mało zieleni (NDVI ${num(v, 2)}) – o tej porze roku to normalne, nie oceniamy kondycji`, v };
}

/** Wilgotność gleby: {st: 'ok' | 'bad' | 'none', key: 'wet' | 'dry' | undefined, text}. */
export function waterState(p, month = new Date().getMonth() + 1) {
  const sm = p && p.soil_moisture;
  if (!sm || !isNum(sm.surface_pct)) return { st: 'none', text: 'Wilgotność gleby: brak danych' };
  const v = Number(sm.surface_pct);
  if (isNum(sm.field_capacity_pct) && v >= Number(sm.field_capacity_pct)) {
    return { st: 'bad', key: 'wet', text: `Gleba nasycona wodą (${num(v, 0)}% przy pojemności polowej ${num(sm.field_capacity_pct, 0)}%)` };
  }
  if (isNum(sm.wilting_point_pct) && v <= Number(sm.wilting_point_pct)) {
    return { st: 'bad', key: 'dry', text: `Gleba przesuszona (${num(v, 0)}% przy punkcie więdnięcia ${num(sm.wilting_point_pct, 0)}%)` };
  }
  if (isNum(p.water_reserve_days) && Number(p.water_reserve_days) <= 3 && month >= 4 && month <= 9) {
    return { st: 'bad', key: 'dry', text: `Mało wody dla roślin (zapas ok. ${p.water_reserve_days} dni)` };
  }
  return { st: 'ok', text: `Wilgotność gleby w normie (${num(v, 0)}%)` };
}

/** Dwie ikonki stanu działki (roślinka i kropelka) z opisem dla czytników ekranu i w dymku. */
export function stateIcons(p) {
  const pl = plantState(p);
  const wa = waterState(p);
  const one = (kind, ico, s) => h('span', { class: `mr-pstate__i mr-pstate__i--${kind} mr-pstate__i--${s.st}`, title: s.text, role: 'img', 'aria-label': s.text }, icon(ico));
  return h('span', { class: 'mr-pstate' }, [one('plant', 'leaf', pl), one('water', 'drop', wa)]);
}

/** Te same dwa stany słownie (w rozwiniętej karcie działki). */
export function stateLines(p) {
  const pl = plantState(p);
  const wa = waterState(p);
  return h('ul', { class: 'mr-pstate-lines' }, [
    h('li', { class: `mr-pstate-lines__i mr-pstate-lines__i--${pl.st}` }, [icon('leaf'), pl.text]),
    h('li', { class: `mr-pstate-lines__i mr-pstate-lines__i--${wa.st} mr-pstate-lines__i--water` }, [icon('drop'), wa.text]),
  ]);
}

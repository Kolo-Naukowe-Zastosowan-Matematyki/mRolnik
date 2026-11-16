// Preferencje interfejsu w localStorage (tylko UI: motyw, ostatnie gospodarstwo, zakładka).
// Nigdy nie zapisujemy tu współrzędnych działek ani danych osobowych.
const KEY = 'mrolnik.prefs.v1';

function readAll() {
  try {
    const raw = window.localStorage.getItem(KEY);
    const obj = raw ? JSON.parse(raw) : {};
    return obj && typeof obj === 'object' ? obj : {};
  } catch {
    return {};
  }
}

export function getPref(name, fallback = null) {
  const v = readAll()[name];
  return v === undefined ? fallback : v;
}

export function setPref(name, value) {
  try {
    const all = readAll();
    all[name] = value;
    window.localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    /* tryb prywatny / zablokowany storage – preferencje tylko na czas sesji */
  }
}

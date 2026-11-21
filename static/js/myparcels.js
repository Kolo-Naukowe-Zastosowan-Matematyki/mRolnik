// Źródło listy „Moje działki” w menu obok „Wczytaj CSV”.
//
// PLACEHOLDER: na razie zwraca gospodarstwa przykładowe (DEMO) z /api/presets, wczytane w main.js do state.presets.
// Docelowo działki rolnika z rejestru (np. KOWR / ARiMR po zalogowaniu przez login.gov.pl): wystarczy podmienić
// myParcelGroups() tak, by zwracała ten sam kształt danych, a SOURCE.demo ustawić na false.
import { state } from './state.js';

export const SOURCE = {
  demo: true,
  hint: 'Na razie przykładowe gospodarstwa do pokazu. Docelowo tu pojawią się Twoje działki z rejestru (np. KOWR).',
};

/** Grupy działek do wyboru: [{ name, count }]. `name` to klucz przekazywany w zdarzeniu „farm:change”. */
export function myParcelGroups() {
  return state.presets.map((f) => ({ name: f.name, count: (f.parcels || []).length }));
}

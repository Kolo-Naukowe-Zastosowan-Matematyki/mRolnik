// Jeden helper do wszystkich wywołań API (Kontrakt 1).

let apiBase = '';
export function setApiBase(base) {
  apiBase = (base || '').replace(/\/$/, '');
}

export class ApiError extends Error {
  constructor(message, { status = 0, detail = null, retryAfter = null, kind = 'error' } = {}) {
    super(message);
    this.status = status;
    this.detail = detail;
    this.retryAfter = retryAfter;
    this.kind = kind; // 'network' | 'server' | 'rate' | 'validation' | 'auth' | 'abort' | 'error'
  }
}

function readCookie(name) {
  const parts = (document.cookie || '').split(';');
  for (const p of parts) {
    const [k, ...rest] = p.trim().split('=');
    if (k === name) {
      try { return decodeURIComponent(rest.join('=')); } catch { return rest.join('='); }
    }
  }
  return '';
}

function redirectToLogin(loginUrl) {
  const url = loginUrl && loginUrl.startsWith('/') && !loginUrl.startsWith('//') ? loginUrl : '/auth/login';
  const next = window.location.pathname + window.location.search;
  window.location.assign(`${url}${url.includes('?') ? '&' : '?'}next=${encodeURIComponent(next)}`);
}

/**
 * api('/api/analyze', {method: 'POST', body: {...}, signal, timeout})
 * – same-origin cookies, X-CSRF-Token dla metod zmieniających stan,
 * – 401 → przekierowanie na stronę logowania, 429 → przyjazny komunikat,
 * – błędy sieci i 5xx → ApiError z kind, który UI pokazuje z przyciskiem „Spróbuj ponownie”.
 */
export async function api(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  const headers = { Accept: 'application/json' };
  let body;
  if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const token = readCookie('mrolnik_csrf');
    if (token) headers['X-CSRF-Token'] = token;
  }

  const ctrl = new AbortController();
  const timeoutMs = opts.timeout ?? 60000;
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, timeoutMs);
  const onAbort = () => ctrl.abort();
  if (opts.signal) {
    if (opts.signal.aborted) ctrl.abort();
    else opts.signal.addEventListener('abort', onAbort, { once: true });
  }

  let res;
  try {
    res = await fetch(apiBase + path, { method, headers, body, credentials: 'same-origin', signal: ctrl.signal });
  } catch (err) {
    if (timedOut) throw new ApiError('Serwer nie odpowiedział w wyznaczonym czasie.', { kind: 'network' });
    if (err && err.name === 'AbortError') throw new ApiError('Przerwano', { kind: 'abort' });
    throw new ApiError('Brak połączenia z serwerem. Sprawdź internet.', { kind: 'network' });
  } finally {
    clearTimeout(timer);
    if (opts.signal) opts.signal.removeEventListener('abort', onAbort);
  }

  let data = null;
  const ctype = res.headers.get('content-type') || '';
  if (ctype.includes('application/json')) {
    try { data = await res.json(); } catch { data = null; }
  } else if (opts.text) {
    data = await res.text();
  }

  if (res.ok) return data;

  const detail = data && data.detail !== undefined ? data.detail : null;
  if (res.status === 401) {
    if (!opts.noRedirect) redirectToLogin(data && data.login_url);
    throw new ApiError('Sesja wygasła – zaloguj się ponownie.', { status: 401, kind: 'auth' });
  }
  if (res.status === 429) {
    const ra = res.headers.get('Retry-After');
    const secs = ra && /^\d+$/.test(ra) ? Number(ra) : null;
    throw new ApiError(
      secs ? `Zbyt wiele zapytań. Spróbuj ponownie za ${secs} s.` : 'Zbyt wiele zapytań. Odczekaj chwilę i spróbuj ponownie.',
      { status: 429, retryAfter: secs, kind: 'rate' },
    );
  }
  if (res.status === 422 || res.status === 400) {
    throw new ApiError('Sprawdź wprowadzone dane.', { status: res.status, detail, kind: 'validation' });
  }
  if (res.status === 403) {
    throw new ApiError(typeof detail === 'string' ? detail : 'Brak uprawnień do tej operacji.', { status: 403, kind: 'error' });
  }
  if (res.status >= 500) {
    throw new ApiError('Serwis chwilowo nie działa. Spróbuj ponownie za chwilę.', { status: res.status, kind: 'server' });
  }
  if (res.status === 404 && (detail === null || /^(not found|nie znaleziono)\.?$/i.test(String(detail)))) {
    // Ogólne 404 z adresu /api/… = serwer nie zna tego adresu: najczęściej działa starsza wersja serwera,
    // uruchomiona przed aktualizacją plików. Konkretne 404 (np. „Zastawka nie istnieje”) idą niżej z własnym komunikatem.
    throw new ApiError('Serwer nie zna tej funkcji – prawdopodobnie działa starsza wersja. Zamknij okno serwera i uruchom ponownie mrolnik.bat (macOS/Linux: mrolnik.sh), potem odśwież stronę.',
      { status: 404, detail, kind: 'server' });
  }
  throw new ApiError(typeof detail === 'string' ? detail : `Błąd zapytania (${res.status}).`, { status: res.status, detail, kind: 'error' });
}

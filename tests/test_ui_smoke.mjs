// Test dymny UI (Playwright + Chromium, Node ≥ 18). NIE jest częścią pytest – uruchom ręcznie:
//   node tests/test_ui_smoke.mjs                 (zrzuty do ./screens lub SCREENS_DIR)
// Wymaga: pakietu „playwright” dostępnego dla node oraz (opcjonalnie) axe-core:
//   AXE_PATH=/ścieżka/do/axe.min.js  albo  npm i -D axe-core (require.resolve('axe-core')).
// Serwer makietowy tests/ui_mock_server.py jest uruchamiany automatycznie (python w PATH lub PYTHON).
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.PORT || 8765);
const BASE = `http://127.0.0.1:${PORT}`;
const SCREENS = process.env.SCREENS_DIR || path.join(ROOT, 'screens');
fs.mkdirSync(SCREENS, { recursive: true });

let axeSource = null;
try {
  const p = process.env.AXE_PATH || require.resolve('axe-core/axe.min.js');
  axeSource = fs.readFileSync(p, 'utf8');
} catch { console.warn('[smoke] axe-core niedostępny – pomijam audyt WCAG (ustaw AXE_PATH).'); }

// --- kafelek PNG do zastąpienia podkładów (sandbox bez sieci) ---
function crc32(buf) {
  let c; const table = [];
  for (let n = 0; n < 256; n++) { c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; table[n] = c >>> 0; }
  let crc = 0xffffffff;
  for (const b of buf) crc = table[(crc ^ b) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}
function png(w, hgt, px) {
  const raw = Buffer.alloc((w * 3 + 1) * hgt);
  for (let y = 0; y < hgt; y++) { raw[y * (w * 3 + 1)] = 0; for (let x = 0; x < w; x++) { const [r, g, b] = px(x, y); const o = y * (w * 3 + 1) + 1 + x * 3; raw[o] = r; raw[o + 1] = g; raw[o + 2] = b; } }
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td)); return Buffer.concat([len, td, crc]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(hgt, 4); ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
const TILE = png(256, 256, (x, y) => {
  const band = Math.floor((x + y * 0.4) / 48) % 3;
  const base = [[156, 172, 108], [134, 158, 92], [176, 168, 120]][band];
  const n = ((x * 7 + y * 13) % 9) - 4;
  return base.map((v) => v + n);
});

const failures = [];
const fail = (m) => { failures.push(m); console.error('  ✗', m); };
const ok = (m) => console.log('  ✓', m);

async function waitForServer() {
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(`${BASE}/api/presets`); if (r.ok) return; } catch { /* czekamy */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error('Serwer makietowy nie wystartował');
}

async function axeCheck(page, label) {
  if (!axeSource) return;
  await page.evaluate(axeSource);
  const res = await page.evaluate(async () => {
    const r = await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] }, resultTypes: ['violations'] });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, help: v.help, target: v.nodes.slice(0, 3).map((x) => x.target.join(' ')) }));
  });
  // Elementy Leaflet-a nad podkładem (atrybucje) – raportujemy, ale nie ignorujemy.
  if (res.length) res.forEach((v) => fail(`[axe ${label}] ${v.id} (${v.impact}, ${v.n}×): ${v.help} → ${v.target.join(' | ')}`));
  else ok(`axe ${label}: 0 naruszeń WCAG 2.1 A/AA`);
}

async function newPage(browser, viewport, opts = {}) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1, colorScheme: opts.dark ? 'dark' : 'light', locale: 'pl-PL', timezoneId: 'Europe/Warsaw' });
  await ctx.route(/https:\/\/(mapy\.geoportal\.gov\.pl|tile\.openstreetmap\.org|[a-c]\.tile\.openstreetmap\.org)\//, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: TILE }));
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  await page.addInitScript(() => {
    window.__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(`${e.violatedDirective} ${e.blockedURI}`));
    window.alert = () => { window.__xss = true; };
  });
  return { ctx, page, errors };
}

async function tab(page, name) {
  await page.click(`#mr-tab-${name}`);
  await page.waitForTimeout(150);
}

async function runViewport(browser, name, viewport) {
  console.log(`\n== ${name} ${viewport.width}×${viewport.height} ==`);
  const { ctx, page, errors } = await newPage(browser, viewport);
  await page.goto(`${BASE}/web`);
  await page.waitForSelector('.mr-pitem', { timeout: 15000 });
  await page.waitForTimeout(500);

  // (5) aktywna zakładka podświetlona od startu
  const sel = await page.getAttribute('#mr-tab-pole', 'aria-selected');
  sel === 'true' ? ok('zakładka „Pole” aktywna na starcie') : fail('zakładka „Pole” nie jest aktywna na starcie');
  const tabBorder = await page.$eval('#mr-tab-pole', (el) => getComputedStyle(el).borderBottomColor);
  tabBorder !== 'rgba(0, 0, 0, 0)' ? ok('aktywna zakładka ma wyraźne podkreślenie') : fail('brak stylu aktywnej zakładki');
  // (6) brak zakładki Web
  (await page.$$eval('[role="tab"]', (t) => t.map((x) => x.textContent.trim()))).some((t) => /web/i.test(t)) ? fail('jest zakładka Web') : ok('brak zakładki Web');
  // (1) proporcje paska narzędzi
  const widths = await page.$eval('.mr-toolrow', (row) => { const r = row.getBoundingClientRect().width; return Array.from(row.children).map((c) => c.getBoundingClientRect().width / r); });
  (Math.abs(widths[0] - 0.5) < 0.04 && Math.abs(widths[1] - 0.25) < 0.04 && Math.abs(widths[2] - 0.25) < 0.04)
    ? ok(`pasek: ${widths.map((w) => Math.round(w * 100)).join('/')}%`) : fail(`proporcje paska ${widths.map((w) => w.toFixed(2)).join('/')}`);
  // (2)(3) brak selecta gospodarstw, sekcje zwinięte
  (await page.$('.mr-app select')) ? fail('widoczny select nad mapą/na starcie') : ok('brak listy wyboru gospodarstwa na starcie');
  (await page.$eval('.mr-examples', (d) => d.open)) ? fail('„Przykłady (demo)” rozwinięte') : ok('„Przykłady (demo)” zwinięte');
  // brak poziomego przewijania
  const hs = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  hs <= 0 ? ok('brak poziomego przewijania') : fail(`poziome przewijanie: ${hs}px`);
  // mapa nad zgięciem
  const mapTop = await page.$eval('.mr-map', (m) => m.getBoundingClientRect().top);
  mapTop < viewport.height * 0.5 ? ok(`mapa zaczyna się na ${Math.round(mapTop)} px`) : fail(`mapa za nisko (${mapTop}px)`);

  await page.screenshot({ path: path.join(SCREENS, `${name}-1-pole.png`), fullPage: name === 'mobile' });
  await axeCheck(page, `${name}/pole`);

  // rozwinięcie działki
  await page.click('.mr-pitem__head >> nth=0');
  await page.waitForTimeout(200);

  await tab(page, 'plony');
  await page.waitForSelector('#mr-tp-plony canvas', { timeout: 10000 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(SCREENS, `${name}-2-plony.png`), fullPage: name === 'mobile' });
  await axeCheck(page, `${name}/plony`);

  await tab(page, 'woda');
  await page.waitForSelector('#mr-tp-woda .mr-gates', { timeout: 10000 });
  await page.waitForSelector('#mr-tp-woda .mr-river', { timeout: 10000 });
  await page.waitForTimeout(800);
  (await page.$('.mr-gate--on')) ? ok('zastawki: zalecenie wyróżnione') : fail('brak wyróżnionego zalecenia zastawek');
  await page.screenshot({ path: path.join(SCREENS, `${name}-3-woda.png`), fullPage: name === 'mobile' });
  await axeCheck(page, `${name}/woda`);

  await tab(page, 'kalk');
  await page.waitForSelector('#mr-tp-kalk details', { timeout: 10000 });
  (await page.$$eval('#mr-tp-kalk details.mr-details--card', (d) => d.some((x) => x.open))) ? fail('kalkulatory rozwinięte na starcie') : ok('kalkulatory zwinięte na starcie');
  await page.click('#mr-tp-kalk details.mr-details--card >> nth=0 >> summary');
  await page.check('#mr-tp-kalk input[value="precyzyjny"]');
  await page.click('#mr-tp-kalk button:has-text("Oblicz normę")');
  await page.waitForSelector('#mr-tp-kalk .mr-result', { timeout: 10000 });
  const big = await page.textContent('#mr-tp-kalk .mr-result__big strong');
  /2[6-9][,.]\d kg\/ha/.test(big) ? ok(`siew punktowy kukurydzy: ${big}`) : fail(`zła norma kukurydzy: ${big}`);
  await page.click('#mr-tp-kalk details.mr-details--card >> nth=1 >> summary');
  await page.click('#mr-tp-kalk button:has-text("Oblicz dawki")');
  await page.waitForSelector('#mr-tp-kalk .mr-npk', { timeout: 10000 });
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(SCREENS, `${name}-4-kalkulatory.png`), fullPage: true });
  await axeCheck(page, `${name}/kalkulatory`);
  // walidacja: ujemna powierzchnia → komunikat przy polu
  const area = page.locator('#mr-tp-kalk details.mr-details--card >> nth=1 >> input[name="area_ha"]');
  await area.fill('-3');
  await page.click('#mr-tp-kalk button:has-text("Oblicz dawki")');
  await page.waitForSelector('#mr-tp-kalk .mr-error:not([hidden])', { timeout: 5000 });
  ok('błąd walidacji (422) pokazany przy polu');

  await tab(page, 'zrodla');
  await page.waitForSelector('#mr-tp-zrodla .mr-srclist', { timeout: 10000 });
  await page.screenshot({ path: path.join(SCREENS, `${name}-5-zrodla.png`), fullPage: name === 'mobile' });
  await axeCheck(page, `${name}/zrodla`);

  // klawiatura: strzałki w zakładkach
  await page.focus('#mr-tab-zrodla');
  await page.keyboard.press('Home');
  (await page.getAttribute('#mr-tab-pole', 'aria-selected')) === 'true' ? ok('Home w zakładkach → Pole') : fail('klawiatura w zakładkach');

  // XSS z wyników wyszukiwarki (dane OSM)
  await page.fill('.mr-input--search', 'Śnieciska');
  await page.waitForSelector('.mr-results__item', { timeout: 5000 });
  const injected = await page.$$eval('.mr-results img', (x) => x.length);
  (injected === 0 && !(await page.evaluate(() => window.__xss))) ? ok('wyniki wyszukiwania bez wstrzykniętego HTML') : fail('XSS w wynikach wyszukiwania');
  await page.screenshot({ path: path.join(SCREENS, `${name}-6-szukaj.png`) });

  // CSV: wiersz bez współrzędnych odrzucony
  await page.click('.mr-toolrow button:has-text("CSV")');
  await page.setInputFiles('.mr-file', { name: 'dzialki.csv', mimeType: 'text/csv', buffer: Buffer.from('id_tag;lat;lon;crop\n"=HYPERLINK(""x"")";52,2020;17,2012;Pszenica\nBez współrzędnych;;;Rzepak\nZa granicą;48,1;10,2;Owies\n') });
  await page.waitForSelector('.mr-csvmsg__errors', { timeout: 5000 });
  const csvMsg = await page.textContent('.mr-csvmsg');
  /Wczytano 1/.test(csvMsg) && /pominięto/.test(csvMsg) ? ok('CSV: 1 wiersz przyjęty, 2 odrzucone') : fail(`CSV: ${csvMsg}`);
  await page.waitForSelector('.mr-pitem', { timeout: 10000 });
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(SCREENS, `${name}-7-moje-pola.png`) });

  const csp = await page.evaluate(() => window.__csp);
  csp.length ? fail(`naruszenia CSP: ${csp.join('; ')}`) : ok('brak naruszeń CSP');
  const errs = errors.filter((e) => !/favicon/.test(e) && !/status of 422/.test(e));
  errs.length ? fail(`błędy konsoli: ${errs.join(' | ')}`) : ok('brak błędów w konsoli');
  await ctx.close();
}

async function runExtra(browser) {
  console.log('\n== ciemny motyw + stan błędu ==');
  const { ctx, page } = await newPage(browser, { width: 1440, height: 900 }, { dark: true });
  await page.goto(`${BASE}/web`);
  await page.waitForSelector('.mr-pitem', { timeout: 15000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(SCREENS, 'desktop-8-ciemny.png') });
  await axeCheck(page, 'dark/pole');
  await tab(page, 'woda');
  await page.waitForSelector('#mr-tp-woda .mr-gates', { timeout: 10000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(SCREENS, 'desktop-9-ciemny-woda.png') });
  await ctx.close();

  const e = await newPage(browser, { width: 390, height: 844 });
  await e.page.goto(`${BASE}/web?mock_fail=analyze`);
  await e.page.waitForSelector('.mr-state--error button', { timeout: 15000 });
  ok('awaria API → komunikat i „Spróbuj ponownie”');
  await e.page.screenshot({ path: path.join(SCREENS, 'mobile-9-blad.png') });
  await e.ctx.close();
}

const server = spawn(process.env.PYTHON || 'python', [path.join(ROOT, 'tests', 'ui_mock_server.py'), String(PORT)], { cwd: ROOT, stdio: ['ignore', 'inherit', 'inherit'] });
let browser;
try {
  await waitForServer();
  browser = await chromium.launch();
  await runViewport(browser, 'desktop', { width: 1440, height: 900 });
  await runViewport(browser, 'mobile', { width: 390, height: 844 });
  await runViewport(browser, 'mobile320', { width: 320, height: 640 });
  await runExtra(browser);
} catch (err) {
  fail(`wyjątek: ${err.stack || err}`);
} finally {
  if (browser) await browser.close();
  server.kill();
}
console.log(`\n${failures.length ? `NIEPOWODZENIA: ${failures.length}` : 'OK – wszystkie sprawdzenia przeszły'}. Zrzuty: ${SCREENS}`);
process.exit(failures.length ? 1 : 0);

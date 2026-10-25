/* Offline browser-script checks; run with node --test tests/test_pages.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const root = path.join(__dirname, '..');
const messages = JSON.parse(fs.readFileSync(path.join(root, 'locales/en.json'), 'utf8'));

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.style = { setProperty() {} }; this.classList = { add() {}, remove() {}, contains() { return false; } };
  }
  set textContent(value) { this.children = [String(value)]; }
  get textContent() { return this.children.map(x => typeof x === 'string' ? x : x.textContent).join(''); }
  append(...values) { this.children.push(...values); }
  replaceChildren(...values) { this.children = values; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener() {}
  insertAdjacentHTML() {} // production uses this only for its constant OS SVG icons
}

function page(filename) {
  const html = fs.readFileSync(path.join(root, filename), 'utf8');
  const nodes = new Map(), headings = [];
  for (const match of html.matchAll(/<[^>]*data-i18n="([^"]+)"[^>]*>/g)) {
    const node = new Element(); node.dataset.i18n = match[1]; node.textContent = match[1]; headings.push(node);
    const id = match[0].match(/\bid="([^"]+)"/); if (id) nodes.set(id[1], node);
  }
  const document = {
    documentElement: new Element('html'),
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    querySelectorAll() { return headings; },
    createElement(tag) { return new Element(tag); },
    createElementNS(ns, tag) { return new Element(tag); },
    createTextNode(text) { return String(text); },
  };
  const context = vm.createContext({ document, console, localStorage: { getItem() { return null; }, setItem() {} },
    setTimeout() {}, clearTimeout() {}, Date, URL, Object, Map, Set });
  context.window = context;
  context.el = (tag, cls, text) => { const node = new Element(tag); node.className = cls; if (text != null) node.textContent = text; return node; };
  context.level = () => 'ok'; context.pct = value => value == null ? '-' : `${Math.round(value)}%`;
  // Execute the locale and render scripts; the final script is the live bridge.
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  for (const script of scripts.slice(0, -1)) vm.runInContext(script, context, { filename });
  const setLocale = language => context.setLocale([{ plugin_id: 'dev.hyunwook.home-monitor', slot: 'page', id: 'dashboard', payload: { language, messages: language === 'en' ? messages : {} } }]);
  const text = () => [...headings, ...nodes.values()].map(n => n.textContent).join(' ');
  return { context, setLocale, text, nodes, scripts };
}

for (const filename of ['page.html', 'tools.html', 'subscriptions.html', 'programs.html', 'history.html', 'dashboard.html']) {
  test(`${filename}: English, Korean, then English without reloading`, () => {
    const p = page(filename);
    for (const language of ['en', 'ko', 'en']) {
      p.setLocale(language);
      p.context.render(filename === 'dashboard.html' ? [] : null);
      assert.equal(p.context.document.documentElement.lang, language);
      if (language === 'en') assert.doesNotMatch(p.text(), /[가-힣]/);
      else assert.match(p.text(), /[가-힣]/);
    }
    // Also syntax-check the live MessageChannel bridge script.
    new vm.Script(p.scripts.at(-1), { filename });
  });
}

test('late-bound labels and weekdays follow live language changes', () => {
  for (const [file, expression, english, korean] of [
    ['history.html', 'RANGES()[0][1]', '24 hours', '24시간'],
    ['history.html', 'DOW()[0]', 'Sun', '일'],
    ['programs.html', 'HEALTH().failed[0]', 'Failed', '실패'],
    ['subscriptions.html', 'STATUS().expired', 'Token expired', '토큰 만료'],
    ['dashboard.html', 'PANELS()[0][1]', 'Sessions', '세션'],
  ]) {
    const p = page(file);
    p.setLocale('en'); assert.equal(vm.runInContext(expression, p.context), english);
    p.setLocale('ko'); assert.equal(vm.runInContext(expression, p.context), korean);
  }
});

test('translation interpolation preserves external values as literal text', () => {
  const p = page('page.html'); p.setLocale('en');
  const value = '<img src=x onerror=alert(1)>{p1}';
  assert.equal(p.context.translate('수집기 오류: {p0}', { p0: value }), `Collector error: ${value}`);
  p.context.render({ updated: '12:00', hosts: [{ name: value, up: false, error: 'offline' }] });
  assert.ok(p.text().includes(value));
  assert.doesNotMatch(p.text(), /[가-힣]/);
});


test('populated pages render translated labels without shadowing table-row variables', () => {
  const host = { id: 'local', name: 'test-host', up: true, os: 'Linux', cores: 6, cpu: 35, load: 2,
    mem: 40, memUsed: 4e9, memTotal: 10e9, rx: 100, tx: 200,
    cpuSeries: [10, 35], memSeries: [35, 40], disks: [{ mount: '/', pct: 20, used: 2e9, total: 10e9 }] };
  const programs = { observed: '2026-10-25T12:00:00Z', summary: { failed: 1 }, hosts: [{ host: 'test-host', os: 'Linux',
    programs: [{ id: 'test', name: 'test', health: 'failed', kind: 'service', lastExit: 1 }] }] };
  const subscriptions = { updated: '12:00', usage: [{ host: 'test-host', providers: [] }], subscriptions: [] };
  const history = { host: 'local', hosts: [{ id: 'local', name: 'test-host' }], range: '24h', updated: '12:00',
    points: [{ at: 1000, cpuMax: 35, cpuAvg: 30 }], cells: [{ dow: 1, hour: 12, cpuAvg: 30, samples: 2 }],
    rows: [{ at: 1000 }], peaks: [{ at: 1000 }], busiestNet: [{ at: 1000 }] };
  const tools = { hosts: [{ host: 'test-host', clis: { codex: { mcp: [{ name: 'test', status: 'ok' }], skills: ['test'] } } }] };
  const payloads = { 'page.html': { hosts: [host], updated: '12:00' }, 'tools.html': tools,
    'subscriptions.html': subscriptions, 'programs.html': programs, 'history.html': history };
  const entries = Object.entries({ monitor: payloads['page.html'], tools, programs, subscriptions }).map(([id, payload]) =>
    ({ plugin_id: 'dev.hyunwook.home-monitor', slot: 'page', id, payload }));
  payloads['dashboard.html'] = entries;
  for (const [filename, payload] of Object.entries(payloads)) {
    const p = page(filename); p.setLocale('en'); p.context.render(payload);
    assert.doesNotMatch(p.text(), /[가-힣]/, filename);
    p.setLocale('ko'); p.context.render(payload); assert.match(p.text(), /[가-힣]/, filename);
  }
});


test('every page message has an English translation', () => {
  for (const file of fs.readdirSync(root).filter(name => name.endsWith('.html'))) {
    const html = fs.readFileSync(path.join(root, file), 'utf8');
    for (const match of html.matchAll(/translate\(("(?:[^"\\]|\\.)*")/g)) {
      const source = JSON.parse(match[1]);
      assert.ok(Object.hasOwn(messages, source), `${file}: missing ${source}`);
    }
    for (const match of html.matchAll(/data-i18n="([^"]+)"/g)) {
      assert.ok(Object.hasOwn(messages, match[1]), `${file}: missing heading ${match[1]}`);
    }
  }
});

const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../assets/activity.js'), 'utf8');
const now = Date.parse('2026-10-20T10:30:00Z');
const fixture = () => ({
  schemaVersion: 1, status: 'ready', provider: 'Cloudflare Web Analytics',
  host: 'lam810.github.io', timezone: 'UTC', includesToday: true,
  updatedAt: '2026-10-20T10:25:00Z', pageviews: 2, visits: 2,
  daily: Array.from({ length: 7 }, (_, i) => ({ date: `2026-10-${14+i}`,
    pageviews: i === 6 ? 2 : 0, visits: i === 6 ? 2 : 0 })),
  countries: [{ country: 'HK', pageviews: 2 }]
});
function node() {
  return { textContent: '', children: [], style: { setProperty() {} },
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; }, setAttribute() {} };
}
function harness(fetcher) {
  const nodes = { '#activity-status': node(), '#activity-data': node(), '.activity-period': node() };
  const listeners = {};
  const document = { visibilityState: 'visible', querySelector: key => nodes[key],
    createElement: node, addEventListener: (event, callback) => { listeners[event] = callback; } };
  const intervals = [];
  class Clock extends Date { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } }
  vm.runInNewContext(source, { document, fetch: fetcher, Date: Clock, Intl, AbortSignal,
    setInterval: (callback, delay) => intervals.push({ callback, delay }) });
  return { nodes, document, listeners, intervals };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('refreshes periodically and on tab return, without using cached JSON', async () => {
  const requests = [];
  const h = harness(async (url, options) => { requests.push({ url, options }); return { ok: true, json: async () => fixture() }; });
  await settle();
  assert.equal(requests.length, 1);
  assert.match(requests[0].url, /activity\.json\?v=\d+$/);
  assert.equal(requests[0].options.cache, 'no-store');
  assert.match(h.nodes['#activity-data'].children[0].textContent, /Snapshot as of.*Not live/);
  assert.equal(h.intervals[0].delay, 300000);
  h.intervals[0].callback(); await settle();
  h.listeners.visibilitychange(); await settle();
  assert.equal(requests.length, 3);
  h.document.visibilityState = 'hidden';
  h.intervals[0].callback(); h.listeners.visibilitychange(); await settle();
  assert.equal(requests.length, 3);
});

test('keeps the last valid chart on temporary network failure and labels it delayed', async () => {
  let calls = 0;
  const h = harness(async () => { if (calls++) throw Error('offline'); return { ok: true, json: async () => fixture() }; });
  await settle();
  h.intervals[0].callback(); await settle();
  assert.equal(h.nodes['#activity-data'].children.length, 4);
  assert.match(h.nodes['#activity-data'].children[0].textContent, /Update delayed/);
  assert.match(h.nodes['.activity-period'].textContent, /Last available/);
});

test('marks a three-hour-old snapshot delayed rather than current', async () => {
  const data = fixture(); data.updatedAt = '2026-10-20T07:30:00Z';
  const h = harness(async () => ({ ok: true, json: async () => data }));
  await settle();
  assert.match(h.nodes['#activity-data'].children[0].textContent, /07:30.*Update delayed/);
  assert.match(h.nodes['.activity-period'].textContent, /Last available/);
});

test('does not overlap requests when timer and tab return fire together', async () => {
  let resolve, calls = 0;
  const h = harness(() => { calls++; return new Promise(done => { resolve = done; }); });
  h.listeners.visibilitychange(); h.intervals[0].callback();
  assert.equal(calls, 1);
  resolve({ ok: true, json: async () => fixture() }); await settle();
  h.listeners.visibilitychange();
  assert.equal(calls, 2);
  resolve({ ok: true, json: async () => fixture() }); await settle();
});

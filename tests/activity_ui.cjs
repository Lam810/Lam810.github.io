const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../assets/activity.js'), 'utf8');
const now = Date.parse('2026-10-20T10:30:00Z');
const fixture = (days = 30) => ({
  schemaVersion: 1, status: 'ready', provider: 'Cloudflare Web Analytics',
  host: 'lam810.github.io', timezone: 'UTC', includesToday: true,
  updatedAt: '2026-10-20T10:25:00Z', pageviews: 2, visits: 2,
  daily: Array.from({ length: days }, (_, i) => ({
    date: new Date(Date.parse('2026-10-20T00:00:00Z') - (days - 1 - i) * 86400000).toISOString().slice(0, 10),
    pageviews: i === days - 1 ? 2 : 0, visits: i === days - 1 ? 2 : 0 })),
  countries: [{ country: 'HK', pageviews: 2 }]
});
function node() {
  return { textContent: '', children: [], attributes: {}, style: { setProperty() {} },
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    setAttribute(key, value) { this.attributes[key] = value; } };
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

test('renders thirty days including today and matches the country period', async () => {
  const h = harness(async () => ({ ok: true, json: async () => fixture() }));
  await settle();
  assert.equal(h.nodes['.activity-period'].textContent, 'Last 30 days · Including today · UTC');
  assert.match(h.nodes['#activity-status'].textContent, /Sep 21.*Oct 20/);
  const chartScroll = h.nodes['#activity-data'].children[2];
  assert.equal(chartScroll.attributes.tabindex, '0');
  const days = chartScroll.children[0].children;
  assert.equal(days.length, 30);
  assert.match(days[29].title, /2026-10-20.*partial day/);
  assert.doesNotMatch(days[6].title, /partial day/);
  const countries = h.nodes['#activity-data'].children[3];
  assert.match(countries.children[1].children[0].textContent, /Same 30-day period/);
});

test('retains a legacy snapshot with an honest seven-day label', async () => {
  const data = { ...fixture(7), refreshDelayed: true };
  const h = harness(async () => ({ ok: true, json: async () => data }));
  await settle();
  assert.equal(h.nodes['.activity-period'].textContent, 'Last available 7-day snapshot · UTC');
  assert.equal(h.nodes['#activity-data'].children[2].children[0].children.length, 7);
});

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

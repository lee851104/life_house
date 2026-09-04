const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/serving/static/comparison.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

function result(n = 10) {
  return {score: {value: 70, confidence: 'low', total_accidents: n},
    stats: {accidents: n}, methodology: {city_band: 2, city_bands: 8, city_candidates: 50},
    factors: [{key: 'night', count: 2, share: 20}, {key: 'large_vehicle', count: 1}],
    data_meta: {range: {from: '2021-07-01', to: '2026-06-30'}}};
}

function setup() {
  const nodes = {}, pending = [];
  const el = id => nodes[id] ||= {value: '', listeners: {},
    addEventListener(event, fn) { this.listeners[event] = fn; },
    setAttribute() {}, focus() {}, scrollIntoView() {}};
  const context = {document: {getElementById: el}, API_BASE: '', AbortController,
    setTimeout: () => 1, clearTimeout() {}, LH: {setOrigin() {}},
    escapeHtml: s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
    fetch: (url, options) => new Promise((resolve, reject) => pending.push({
      payload: JSON.parse(options.body), signal: options.signal, reject,
      done(data = result()) { resolve({ok: true, json: async () => data}); }
    }))};
  vm.createContext(context); vm.runInContext(source, context);
  context.Comparison.init();
  return {c: context.Comparison, nodes, pending,
    add(i, point, name, data = result()) {
      context.Comparison.setCurrent(point, name, data);
      nodes['compareAdd' + i].listeners.click();
    },
    click(action, i) {
      nodes.compareTable.listeners.click({target: {closest: () => ({dataset: {[action]: String(i)}})}});
    }};
}

test('two named places, duplicate rejection, replacement, removal, escaped names', () => {
  const h = setup();
  assert.equal(h.nodes.compareAdd0.disabled, true);
  h.add(0, [24.9, 121.2], '<img src=x>');
  h.add(1, [24.9, 121.2], 'same');
  assert.match(h.nodes.compareStatus.textContent, /兩個地點相同/);
  h.add(1, [24.99, 121.3], 'B');
  assert.match(h.nodes.compareTable.innerHTML, /&lt;img/);
  assert.match(h.nodes.compareTable.innerHTML, /10 件/);
  h.add(0, [24.8, 121.1], 'Replacement');
  assert.doesNotMatch(h.nodes.compareTable.innerHTML, /&lt;img/);
  h.click('remove', 1);
  assert.match(h.nodes.compareTable.innerHTML, /尚未選擇/);
  h.c.setCurrent(null, '', null);
  assert.equal(h.nodes.compareAdd0.disabled, true);
});

test('both requests share time; stale responses cannot replace new results', async () => {
  const h = setup();
  h.add(0, [24.9, 121.2], 'A'); h.add(1, [24.99, 121.3], 'B');
  h.c.setTime(22, 8);
  assert.doesNotMatch(h.nodes.compareTable.innerHTML, /10 件/);
  assert.equal(h.pending.length, 2);
  assert.ok(h.pending.every(p => p.payload.time_start === 22 && p.payload.time_hours === 8));
  h.c.setTime(7, 3);
  assert.ok(h.pending[0].signal.aborted);
  h.pending[2].done(result(25)); h.pending[3].done(result(26)); await settle();
  h.pending[0].done(result(99)); h.pending[1].done(result(98)); await settle();
  assert.match(h.nodes.compareTable.innerHTML, /25 件/);
  assert.match(h.nodes.compareTable.innerHTML, /26 件/);
  assert.doesNotMatch(h.nodes.compareTable.innerHTML, /99 件|98 件/);
});

test('failed request can retry; removing an in-flight place prevents its reappearance', async () => {
  const h = setup();
  h.add(0, [24.9, 121.2], 'A'); h.add(1, [24.99, 121.3], 'B');
  h.c.setTime(22, 8);
  h.pending[0].reject(new Error('offline')); h.pending[1].done(); await settle();
  assert.match(h.nodes.compareTable.innerHTML, /重試/);
  h.click('retry', 0);
  h.pending[2].done(result(55)); await settle();
  assert.match(h.nodes.compareTable.innerHTML, /55 件/);
  h.c.setTime(0, 24); h.click('remove', 0);
  h.pending[3].done(result(999)); h.pending[4].done(); await settle();
  assert.doesNotMatch(h.nodes.compareTable.innerHTML, /999 件/);
  assert.match(h.nodes.compareTable.innerHTML, /尚未選擇/);
});

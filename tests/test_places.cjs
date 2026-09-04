const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/serving/static/places.js'), 'utf8');
const KEY = 'lifehouse.favorites.v1';
function setup(storage = {}, hash = '') {
  const nodes = {}, opened = [];
  const el = id => nodes[id] ||= {value: '', listeners: {}, hidden: true,
    addEventListener(event, fn) { this.listeners[event] = fn; }, focus() {}, select() {}};
  const context = {URLSearchParams, document: {getElementById: el}, window: {addEventListener() {}},
    location: {origin: 'https://example.test', hostname: 'example.test', pathname: '/', hash},
    history: {replaceState(_, title, url) { context.location.hash = url.includes('#') ? url.slice(url.indexOf('#')) : ''; }},
    navigator: {}, LH: {restorePlace: value => opened.push(value)},
    localStorage: {getItem: k => storage[k] || null, setItem: (k, value) => { storage[k] = value; }},
    escapeHtml: s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;')};
  vm.createContext(context); vm.runInContext(source, context); context.Places.init();
  return {c: context.Places, context, nodes, opened, storage,
    ready(point = [24.9, 121.2], name = '地址 A', start = 22, hours = 8) {
      context.Places.ready(point, name, start, hours); el('compareName').value = name;
    }, click(id) { return el(id).listeners.click(); }};
}

test('share roundtrip preserves coordinates, Unicode, and midnight-spanning time', () => {
  const h = setup();
  const value = {lat: 24.958145, lon: 121.243715, name: '中原大學 & A', start: 22, hours: 8};
  assert.deepEqual(JSON.parse(JSON.stringify(h.c.decode(h.c.encode(value)))), value);
  const restored = setup({}, h.c.encode(value));
  assert.equal(restored.opened[0].name, value.name);
  assert.equal(restored.opened[0].hours, 8);
  for (const hash of ['#v=1&lat=&lon=121&start=0&hours=24', '#v=1&lat=NaN&lon=121&start=0&hours=24',
    '#v=1&lat=91&lon=121&start=0&hours=24', '#v=1&lat=24&lon=121&start=24&hours=24',
    '#v=1&lat=24&lon=121&start=0&hours=0', '#v=1&lat=24&lat=25&lon=121&start=0&hours=24']) {
    assert.throws(() => h.c.decode(hash));
  }
  assert.equal(h.c.decode('#method'), null);
});

test('favorites persist, update by address, restore, remove and escape names', () => {
  const h = setup(); h.ready(); h.click('savePlace');
  h.ready([24.9, 121.2], '<img src=x>', 7, 3); h.click('savePlace');
  assert.equal(h.nodes.favoriteCount.textContent, '1／20');
  assert.match(h.nodes.favoriteList.innerHTML, /&lt;img/);
  const fresh = setup(h.storage);
  fresh.nodes.favoriteList.listeners.click({target: {closest: () => ({dataset: {open: '0'}})}});
  assert.equal(fresh.opened[0].start, 7);
  assert.equal(fresh.opened[0].hours, 3);
  fresh.nodes.favoriteList.listeners.click({target: {closest: () => ({dataset: {delete: '0'}})}});
  assert.equal(setup(h.storage).nodes.favoriteCount.textContent, '0／20');
});

test('storage errors and the collection limit are handled without false success', () => {
  const broken = setup({[KEY]: '{bad json'});
  assert.match(broken.nodes.placeStatus.textContent, /無法讀取/);
  const h = setup(); h.ready();
  h.context.localStorage.setItem = () => { throw new Error('quota'); };
  h.click('savePlace');
  assert.match(h.nodes.placeStatus.textContent, /無法儲存/);
  assert.equal(h.nodes.favoriteCount.textContent, '0／20');
  const full = setup();
  for (let i = 0; i < 21; i++) { full.ready([24 + i / 100, 121.2]); full.click('savePlace'); }
  assert.equal(full.nodes.favoriteCount.textContent, '20／20');
  assert.match(full.nodes.placeStatus.textContent, /最多收藏/);
});

test('clipboard failure offers a manual copy link; pending analysis disables actions', async () => {
  const h = setup(); h.ready(); await h.click('sharePlace');
  assert.equal(h.nodes.shareFallback.hidden, false);
  assert.match(h.nodes.shareUrl.value, /^https:\/\/example.test\/#v=1/);
  assert.match(h.nodes.placeStatus.textContent, /無法自動複製/);
  h.c.begin([24.99, 121.3], 'B', 0, 24);
  assert.equal(h.nodes.sharePlace.disabled, true);
  assert.equal(h.nodes.shareFallback.hidden, true);
  assert.equal(h.c.decode(h.context.location.hash).hours, 24);
  h.c.begin(null, '', 0, 24);
  assert.equal(h.context.location.hash, '');
});

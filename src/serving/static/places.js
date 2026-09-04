/* Share a query, not a frozen score. Favorites stay in this browser. */
var Places = (function () {
  'use strict';
  var KEY = 'lifehouse.favorites.v1', LIMIT = 20;
  var current = null, favorites = [];
  function el(id) { return document.getElementById(id); }
  function message(text) { el('placeStatus').textContent = text; }
  function valid(value) {
    return value && Number.isFinite(value.lat) && value.lat >= -90 && value.lat <= 90 &&
      Number.isFinite(value.lon) && value.lon >= -180 && value.lon <= 180 &&
      Number.isInteger(value.start) && value.start >= 0 && value.start <= 23 &&
      Number.isInteger(value.hours) && value.hours >= 1 && value.hours <= 24 &&
      typeof value.name === 'string' && value.name.length <= 80;
  }
  function identity(value) { return value.lat.toFixed(6) + ',' + value.lon.toFixed(6); }
  function encode(value) {
    return '#' + new URLSearchParams({v: '1', lat: String(value.lat), lon: String(value.lon),
      start: String(value.start), hours: String(value.hours), name: value.name}).toString();
  }
  function decode(hash) {
    if (!hash || hash === '#') return null;
    var params = new URLSearchParams(hash.slice(1));
    if (!params.has('lat') && !params.has('lon') && !params.has('v')) return null;
    var fields = ['lat', 'lon', 'start', 'hours'];
    if (params.get('v') !== '1' || fields.some(function (f) {
      return params.getAll(f).length !== 1 || !params.get(f).trim();
    })) throw new Error('invalid query');
    var value = {lat: Number(params.get('lat')), lon: Number(params.get('lon')),
      start: Number(params.get('start')), hours: Number(params.get('hours')),
      name: params.get('name') || ''};
    if (!valid(value)) throw new Error('invalid query');
    return value;
  }
  function url(value) { return location.origin + location.pathname + encode(value); }
  function syncUrl(value) {
    history.replaceState(null, '', location.pathname + (value ? encode(value) : ''));
  }
  function begin(point, name, start, hours) {
    current = null;
    el('sharePlace').disabled = el('savePlace').disabled = true;
    el('shareFallback').hidden = true;
    message('');
    syncUrl(point ? {lat: point[0], lon: point[1], name: name.slice(0, 80), start: start, hours: hours} : null);
  }
  function ready(point, name, start, hours) {
    current = {lat: point[0], lon: point[1], name: name.slice(0, 80), start: start, hours: hours};
    el('sharePlace').disabled = el('savePlace').disabled = false;
  }
  function namedCurrent() {
    return Object.assign({}, current, {name: el('compareName').value.trim().slice(0, 80) || current.name});
  }
  function save() {
    if (!current) return;
    var value = namedCurrent();
    var index = favorites.findIndex(function (item) { return identity(item) === identity(value); });
    if (index < 0 && favorites.length >= LIMIT) { message('最多收藏 20 個地址，請先移除不需要的收藏。'); return; }
    var updated = favorites.slice();
    if (index >= 0) updated[index] = value; else updated.push(value);
    if (persist(updated)) message(index >= 0 ? '已更新此地址的名稱與時段。' : '已收藏，僅保存在這個瀏覽器。');
  }
  function persist(updated) {
    try { localStorage.setItem(KEY, JSON.stringify(updated)); }
    catch (_) { message('瀏覽器無法儲存收藏；請檢查儲存空間或隱私設定。'); return false; }
    favorites = updated; render(); return true;
  }
  async function share() {
    if (!current) return;
    var value = namedCurrent(), link = url(value);
    syncUrl(value);
    try {
      await navigator.clipboard.writeText(link);
      message('已複製地點與時段連結。');
    } catch (_) {
      el('shareFallback').hidden = false;
      el('shareUrl').value = link;
      el('shareUrl').focus(); el('shareUrl').select();
      message('無法自動複製，請複製下方連結。');
    }
  }
  function open(value) {
    if (valid(value)) LH.restorePlace(value);
  }
  function restore() {
    try { var value = decode(location.hash); if (value) open(value); }
    catch (_) { message('分享連結的座標或時段不正確，請重新選擇地點。'); }
  }
  function render() {
    el('favoriteList').innerHTML = favorites.length ? favorites.map(function (value, i) {
      var time = value.hours === 24 ? '全天' : String(value.start).padStart(2, '0') + ':00 起 ' + value.hours + ' 小時';
      return '<li><button type="button" data-open="' + i + '"><b>' + escapeHtml(value.name || identity(value)) +
        '</b><small>' + escapeHtml(identity(value)) + '・' + time + '</small></button>' +
        '<button type="button" data-delete="' + i + '" aria-label="移除收藏 ' +
        escapeHtml(value.name || identity(value)) + '">移除</button></li>';
    }).join('') : '<li class="favorite-empty">尚無收藏。選好位置後，按「收藏地址」即可加入。</li>';
    el('favoriteCount').textContent = favorites.length + '／' + LIMIT;
  }
  function init() {
    el('sharePlace').addEventListener('click', share);
    el('savePlace').addEventListener('click', save);
    el('favoriteList').addEventListener('click', function (event) {
      var button = event.target.closest('button');
      if (!button) return;
      if (button.dataset.open !== undefined) open(favorites[Number(button.dataset.open)]);
      if (button.dataset.delete !== undefined) {
        var index = Number(button.dataset.delete);
        if (!favorites[index]) return;
        if (persist(favorites.filter(function (_, i) { return i !== index; }))) message('已移除收藏。');
      }
    });
    try {
      var raw = JSON.parse(localStorage.getItem(KEY) || '[]');
      if (!Array.isArray(raw) || raw.length > LIMIT || !raw.every(valid)) throw new Error('invalid favorites');
      favorites = raw;
    } catch (_) { message('無法讀取收藏資料；你仍可搜尋地點或使用分享連結。'); }
    render();
    el('localShareNote').hidden = !['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
    window.addEventListener('hashchange', restore);
    restore();
  }
  return {init: init, begin: begin, ready: ready, decode: decode, encode: encode};
})();

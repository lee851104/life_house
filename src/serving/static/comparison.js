/* Two places always share the active time filter. Results from old requests are discarded. */
var Comparison = (function () {
  'use strict';
  var slots = [null, null], current = null, start = 0, hours = 24;
  var serial = 0;
  function el(id) { return document.getElementById(id); }
  function esc(value) { return escapeHtml(value); }
  function key(point) { return point.map(function (n) { return Number(n).toFixed(6); }).join(','); }
  function fact(d, name) { return d.factors.find(function (f) { return f.key === name; }); }
  function confidenceText(d) {
    return {high: '資料充足', mid: '資料尚可', low: '資料偏少，僅供參考'}[d.score.confidence];
  }
  function setCurrent(point, name, data) {
    current = data ? {point: point.slice(), name: name, data: data} : null;
    el('compareName').value = current ? name : '';
    el('compareName').disabled = !current;
    updateButtons();
  }
  function updateButtons() {
    [0, 1].forEach(function (i) {
      var b = el('compareAdd' + i);
      b.disabled = !current;
      b.textContent = (slots[i] ? '更新' : '設為') + '地點 ' + (i ? 'B' : 'A');
    });
  }
  function add(i) {
    if (!current) return;
    if (slots[1 - i] && key(slots[1 - i].point) === key(current.point)) {
      el('compareStatus').textContent = '兩個地點相同，請搜尋或點選另一個位置。';
      return;
    }
    if (slots[i] && slots[i].controller) slots[i].controller.abort();
    slots[i] = {point: current.point.slice(), name: el('compareName').value.trim() || current.name,
                data: current.data, id: ++serial};
    el('compareStatus').textContent = '已設定地點 ' + (i ? 'B' : 'A') + '。';
    render();
    el('comparison').scrollIntoView({behavior: 'smooth', block: 'nearest'});
  }
  function remove(i) {
    if (slots[i] && slots[i].controller) slots[i].controller.abort();
    slots[i] = null;
    render();
    el('compareStatus').textContent = '已移除地點 ' + (i ? 'B' : 'A') + '。';
    el('compareAdd' + i).focus();
  }
  async function load(i) {
    var item = slots[i];
    if (!item) return;
    if (item.controller) item.controller.abort();
    var ticket = ++serial;
    item.id = ticket; item.data = null; item.error = null;
    var controller = new AbortController();
    item.controller = controller;
    render();
    var timeout = setTimeout(function () { controller.abort(); }, 30000);
    try {
      var response = await fetch(API_BASE + '/api/v3/analyze', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, signal: controller.signal,
        body: JSON.stringify({mode: 'walk', lat: item.point[0], lon: item.point[1],
                              time_start: start, time_hours: hours})
      });
      var data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error ? data.error.message : '分析服務暫時無法使用');
      if (slots[i] !== item || item.id !== ticket) return;
      item.data = data;
    } catch (error) {
      if (slots[i] !== item || item.id !== ticket) return;
      item.error = error.name === 'AbortError' ? '查詢逾時，請重試。' : '查詢失敗，請重試。';
    } finally {
      clearTimeout(timeout);
      if (slots[i] === item && item.id === ticket) { item.controller = null; render(); }
    }
  }
  function setTime(s, h) {
    start = s; hours = h;
    // Clear both results before rendering either request so no old/new values mix.
    slots.forEach(function (item) { if (item) item.data = null; });
    slots.forEach(function (item, i) { if (item) load(i); });
    render();
  }
  function render() {
    updateButtons();
    var end = (start + hours) % 24;
    var label = hours === 24 ? '全天' : String(start).padStart(2, '0') + ':00–' +
      (start + hours >= 24 ? '隔日 ' : '') + String(end).padStart(2, '0') + ':00';
    el('compareTime').textContent = '共同時段：' + label + '・每處半徑 500 公尺';
    function value(item, fn) {
      if (!item) return '尚未選擇';
      if (item.error) return '—';
      if (!item.data) return '更新中…';
      return fn(item.data);
    }
    var rows = [
      ['同類地區安全百分位', function (d) { return '<strong>' + d.score.value +
        '%</strong><small>桃園第 ' + d.methodology.city_band + '／' + d.methodology.city_bands +
        ' 路網分層・' + d.methodology.city_candidates + ' 個比較格</small>'; }],
      ['行人事故', function (d) { return d.stats.accidents + ' 件'; }],
      ['夜間行人事故（18–06）', function (d) { var f = fact(d, 'night');
        return f.count + ' 件<small>' + (f.share === null ? '無事故，比例不適用' :
          '占所選時段行人事故 ' + f.share + '%') + '</small>'; }],
      ['資料充足度', function (d) { return confidenceText(d) + '<small>依 ' + d.score.total_accidents +
        ' 件事故分級；不是統計信賴區間</small>'; }],
      ['大型車涉入', function (d) { var f = fact(d, 'large_vehicle'); return f.count + ' 件'; }],
      ['資料期間', function (d) { return esc(d.data_meta.range.from) + '<br>至 ' + esc(d.data_meta.range.to); }]
    ];
    var head = slots.map(function (item, i) {
      var label = i ? 'B' : 'A';
      return '<th scope="col">地點 ' + label + '<span class="compare-place">' +
        esc(item ? item.name : '搜尋或點選後加入') + '</span>' + (item ?
          '<small>' + esc(key(item.point)) + '</small><div class="compare-actions">' +
          '<button type="button" data-view="' + i + '">查看地圖</button>' +
          '<button type="button" data-remove="' + i + '" aria-label="移除地點 ' + label + '">移除</button></div>' +
          (item.error ? '<p role="alert">' + esc(item.error) + '</p><button type="button" data-retry="' + i + '">重試</button>' : '') : '') + '</th>';
    }).join('');
    el('compareTable').innerHTML = '<table><caption class="sr-only">兩個地點的歷史行人事故比較</caption>' +
      '<thead><tr><th scope="col">比較項目</th>' + head + '</tr></thead><tbody>' +
      rows.map(function (row) { return '<tr><th scope="row">' + row[0] + '</th>' +
        slots.map(function (item) { return '<td>' + value(item, row[1]) + '</td>'; }).join('') + '</tr>'; }).join('') +
      '</tbody></table>';
    el('comparison').setAttribute('aria-busy', String(slots.some(function (s) { return s && !s.data && !s.error; })));
  }
  function explain(d) {
    var m = d.methodology;
    el('scoreBasis').innerHTML =
      '<p><b>附近百分位：</b>與中心附近每 ' + m.local_grid_m + ' 公尺取樣的 ' + m.local_candidates +
      ' 個比較格相比，每格也評估半徑 ' + m.radius_m + ' 公尺。不同地址的附近比較格不同，不能直接當成兩地排名。</p>' +
      '<p><b>桃園同類地區百分位：</b>本次為 <b>' + d.score.value + '%</b>，與有效步行路網長度相近的第 ' +
      m.city_band + '／' + m.city_bands + ' 層、' + m.city_candidates + ' 個基準格比較。有效路網長度為 ' +
      m.effective_network_km + ' 公里；這是距離加權值，不是實際可走距離或人流量。</p>' +
      '<p>例如 70% 表示加權風險低於比較母體中約 70% 的位置，不代表有 70% 機率安全。同分取名次中點，結果限制為 1–99%。</p>' +
      '<ol><li>事故嚴重度＝1＋' + m.fatality_weight + '×死亡人數；距離權重＝1−(距離／' + m.radius_m +
      ')²。從資料截止日往前各年的時間權重為 ' + m.recency_weights.join('、') + '。</li>' +
      '<li>事故加權總和除以距離加權路網長度；以 ' + m.shrinkage_km +
      ' 公里先驗量向同層中位風險收縮，減少小樣本波動。篩選時段時，全市百分位另以全市同時段事故加權占比校正；附近百分位使用同時段的共同先驗。</li>' +
      '<li>資料充足：至少 ' + m.confidence_thresholds.high + ' 件；尚可：' + m.confidence_thresholds.mid +
      '–' + (m.confidence_thresholds.high - 1) + ' 件；其餘資料偏少。這是件數提示，不是統計信賴區間。有效路網少於 ' +
      m.minimum_network_km + ' 公里時不提供附近百分位。</li></ol>' +
      '<p>計算使用歷史行人事故；夜間與大型車指標直接呈現件數、占比，沒有另外換算成安全分數。零事故不等於零風險，亦未納入實際行人流量、人行道品質等因素。範圍為直線半徑，並非沿道路可達範圍。</p>';
    var night = fact(d, 'night'), large = fact(d, 'large_vehicle');
    el('observedFacts').textContent = '所選時段內：夜間（18–06）行人事故 ' + night.count + ' 件' +
      (night.share === null ? '（無事故，比例不適用）' : '（占 ' + night.share + '%）') +
      '；大型車涉入 ' + large.count + ' 件。以上是事故組成，不代表個人發生機率。';
  }
  function init() {
    [0, 1].forEach(function (i) { el('compareAdd' + i).addEventListener('click', function () { add(i); }); });
    el('compareTable').addEventListener('click', function (event) {
      var b = event.target.closest('button');
      if (!b) return;
      if (b.dataset.remove !== undefined) remove(Number(b.dataset.remove));
      if (b.dataset.retry !== undefined) load(Number(b.dataset.retry));
      if (b.dataset.view !== undefined) {
        var item = slots[Number(b.dataset.view)];
        if (item) { LH.setOrigin(item.point[0], item.point[1], item.name); el('map').scrollIntoView({block: 'center'}); }
      }
    });
    setCurrent(null, '', null); render();
  }
  return {init: init, setCurrent: setCurrent, setTime: setTime, explain: explain};
})();

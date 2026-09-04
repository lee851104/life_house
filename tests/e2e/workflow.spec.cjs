const {test, expect} = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const leafletDir = path.dirname(require.resolve('leaflet'));

test.beforeEach(async ({context}) => {
  // Only external map resources are isolated. All /api requests use the real backend/data.
  await context.route('**/*', async route => {
    const u = new URL(route.request().url());
    if (u.hostname === '127.0.0.1') return route.continue();
    if (u.pathname.endsWith('/leaflet.js') || u.pathname.endsWith('/leaflet.css')) {
      const css = u.pathname.endsWith('.css');
      return route.fulfill({body: fs.readFileSync(path.join(leafletDir, css ? 'leaflet.css' : 'leaflet.js')),
        contentType: css ? 'text/css' : 'text/javascript'});
    }
    return route.abort();
  });
});

async function pick(page) {
  await page.getByRole('combobox', {name: '搜尋地址、地標或路口'}).fill('中原大學');
  const analysis = page.waitForResponse(r => r.url().endsWith('/api/v3/analyze') && r.request().method() === 'POST');
  await page.locator('#gmenu').getByRole('option', {name: '中原大學 中壢區 university', exact: true}).click();
  const response = await analysis;
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test('search → choose place → change time → inspect intersection', async ({page}) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  const day = await pick(page);
  expect(day.stats.accidents).toBeGreaterThan(0);
  await expect(page.locator('#nv1')).toHaveText(day.stats.accidents + '件');
  const analysis = page.waitForResponse(r => r.url().endsWith('/api/v3/analyze'));
  await page.getByRole('button', {name: '深夜 22–06', exact: true}).click();
  const response = await analysis;
  const night = await response.json();
  expect(response.request().postDataJSON()).toMatchObject({time_start: 22, time_hours: 8});
  expect(night.stats.accidents).toBeLessThan(day.stats.accidents);
  await expect(page.locator('#nv1')).toHaveText(night.stats.accidents + '件');
  await expect(page.locator('#numScope')).toContainText('22:00–隔日 06:00');
  const intersection = night.intersections[0];
  await page.locator('.leaflet-marker-icon.xn').first().click();
  await expect(page.locator('#cardIntersection')).toBeVisible();
  await expect(page.locator('#xDetailTitle')).toHaveText(intersection.name);
  await expect(page.locator('#xPedSummary')).toHaveText(
    intersection.summary.total + ' 件中有 ' + intersection.summary.pedestrian + ' 件與行人有關');
  expect(intersection.points.length).toBe(intersection.summary.pedestrian);
  await page.getByRole('button', {name: '← 返回 500 公尺生活圈'}).click();
  await expect(page.locator('#cardScore')).toBeVisible();
  expect(errors).toEqual([]);
});

test('share restores query; favorite persists, restores time and can be removed', async ({page, context}) => {
  await page.goto('/');
  const original = await pick(page);
  const analysis = page.waitForResponse(r => r.url().endsWith('/api/v3/analyze'));
  await page.getByRole('button', {name: '深夜 22–06', exact: true}).click();
  const night = await (await analysis).json();
  await expect(page.getByRole('button', {name: '收藏地址', exact: true})).toBeEnabled();
  await page.locator('#compareName').fill('我的看屋地址');
  await page.getByRole('button', {name: '收藏地址', exact: true}).click();
  await expect(page.locator('#favoriteCount')).toHaveText('1／20');
  await page.getByRole('button', {name: '複製分享連結', exact: true}).click();
  const link = page.url();
  expect(new URLSearchParams(new URL(link).hash.slice(1)).get('hours')).toBe('8');
  const shared = await context.newPage();
  await shared.goto(link);
  await expect(shared.locator('#nv1')).toHaveText(night.stats.accidents + '件');
  await expect(shared.locator('#compareName')).toHaveValue('我的看屋地址');
  await expect(shared.locator('#timeStart')).toHaveValue('22');
  await shared.getByRole('button', {name: '重設', exact: true}).click();
  await shared.reload();
  await shared.locator('.favorites summary').click();
  await expect(shared.locator('#favoriteCount')).toHaveText('1／20');
  await shared.locator('#favoriteList button[data-open]').click();
  await expect(shared.locator('#nv1')).toHaveText(night.stats.accidents + '件');
  await expect(shared.locator('#timeHours')).toHaveValue('8');
  expect(night.stats.accidents).toBeLessThan(original.stats.accidents);
  await shared.getByRole('button', {name: '移除收藏 我的看屋地址', exact: true}).click();
  await expect(shared.locator('#favoriteCount')).toHaveText('0／20');
  await shared.reload();
  await expect(shared.locator('#favoriteCount')).toHaveText('0／20');
});

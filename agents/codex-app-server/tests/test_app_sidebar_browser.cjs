'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1582, height: 1022 } });
    const errors = []; const calls = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (r.url().endsWith('/api/native/call')) calls.push(r.postDataJSON()); });
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.getByRole('button', { name: '打开 Apps', exact: true }).click({ timeout: 5000 });
    const panel = page.getByRole('tabpanel', { name: 'Apps面板', exact: true });
    const card = panel.locator('[data-mcp-app="fixture-app:lookup:ui://fixture/app"]');
    await card.waitFor();
    assert.equal(await panel.getByRole('checkbox').count(), 0);
    const refresh = panel.getByRole('button', { name: '刷新应用', exact: true });
    await refresh.click();
    await panel.getByRole('status').getByText('Apps 已刷新', { exact: true }).waitFor();
    const search = panel.getByRole('searchbox', { name: '搜索 Apps', exact: true });
    await search.fill('no-matching-app');
    await panel.getByText('没有匹配的应用', { exact: true }).waitFor();
    await search.clear();
    const buttons = [refresh, card.getByRole('button', { name: '打开应用', exact: true })];
    for (const theme of ['light', 'dark']) {
      if (theme === 'dark') await page.getByRole('button', { name: '切换深色主题', exact: true }).click();
      await page.waitForTimeout(250); // Let the theme transition settle before inspecting colors.
      const colors = await Promise.all(buttons.map(button => button.evaluate(el => getComputedStyle(el).backgroundColor)));
      assert.equal(colors[0], colors[1], 'catalog actions use the same button surface');
      assert.notEqual(colors[0], 'rgba(0, 0, 0, 0)');
      const searchBox = await search.boundingBox();
      assert.ok(searchBox.width > 160, 'search is visible instead of an unstyled blank field');
      if (process.env.CONSOLE_SCREENSHOTS) {
        fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/apps-catalog-${theme}.png` });
      }
    }
    await page.getByRole('button', { name: '切换浅色主题', exact: true }).click();
    await card.getByRole('button', { name: '打开应用', exact: true }).click();
    const app = panel.frameLocator('iframe');
    await app.locator('#status').getByText('native app tool result', { exact: true }).waitFor();
    const session = new URL(page.url()).searchParams.get('session');
    assert.ok(session, 'a sidebar launch selects its backing chat so activation polling can follow it');
    const first = calls.find(c => c.method === 'mcpServer/tool/call');
    assert.ok(first.params.threadId);
    assert.equal(await page.locator('.aui-thread-root iframe').count(), 0, 'App lives in the side tab, outside the transcript');
    const original = await panel.locator('iframe').elementHandle();
    await app.locator('#draft').fill('sidebar draft stays');
    await page.getByRole('tab', { name: '摘要', exact: true }).click();
    await page.getByRole('tab', { name: 'Apps', exact: true }).click();
    assert.equal(await original.evaluate(el => el.isConnected), true, 'tab switching preserves the same iframe');
    assert.equal(await app.locator('#draft').inputValue(), 'sidebar draft stays');
    await page.getByRole('button', { name: '关闭任务面板', exact: true }).click();
    await page.getByRole('button', { name: '打开 Apps', exact: true }).click();
    assert.equal(await original.evaluate(el => el.isConnected), true, 'collapsing the panel preserves the same iframe');
    assert.equal(await app.locator('#draft').inputValue(), 'sidebar draft stays');
    assert.equal(calls.filter(c => c.method === 'mcpServer/tool/call').length, 1, 'reopening the panel does not invoke the launch tool twice');

    // An activation appears after launch, then survives reload in its own chat.
    const other = await page.evaluate(async () => {
      const response = await fetch('/api/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': 'sidebar-other-' + Date.now() }, body: '{}' });
      return response.json();
    });
    await page.route('**/api/mcp-app/activations?*', route => {
      const sid = new URL(route.request().url()).searchParams.get('session_id');
      return route.fulfill({ json: { items: sid === session || sid === other.id ? [{
        activation_id: 'sidebar-activation', state: 'ready', source_session_id: session,
        session_id: other.id, thread_id: other.thread_id, app: { server: 'fixture-app', uri: 'ui://fixture/app' },
      }] : [] } });
    });
    await page.waitForURL(url => url.searchParams.get('session') === other.id);
    await page.locator('.workbench-panel').waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: '打开 Apps', exact: true }).click();
    await panel.frameLocator('iframe').locator('#draft').waitFor();
    assert.equal(await original.evaluate(el => el.isConnected), false, 'switching chat tears down the old App scope');
    assert.equal(await panel.locator('iframe').count(), 1);
    assert.equal(new URL(await panel.locator('iframe').getAttribute('src'), page.url()).searchParams.get('threadId'), other.thread_id);
    await page.reload();
    await page.locator('#text').waitFor();
    assert.equal(await page.getByRole('tab', { name: 'Apps', exact: true, selected: true }).count(), 0, 'legacy activation does not reopen the panel on reload');
    await page.getByRole('button', { name: '打开 Apps', exact: true }).click();
    await page.getByRole('tab', { name: 'Apps', exact: true, selected: true }).waitFor();
    await panel.frameLocator('iframe').locator('#draft').waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('tabpanel', { name: 'Apps面板', exact: true }).frameLocator('iframe').locator('#draft').waitFor();
    const box = await page.getByRole('dialog', { name: 'Apps', exact: true }).boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 391);
    assert.deepEqual(errors, []);
    console.log('Sidebar Apps: direct launch, selected session, iframe retention, activation routing, reload and mobile passed.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });

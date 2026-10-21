'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'],
    ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
    const base = process.env.CONSOLE_TEST_URL;
    const id = 'recovery-ui-' + Date.now();
    const post = async (url, data) => {
      const response = await context.request.post(base + url, { data, headers: { 'idempotency-key': randomUUID() } });
      assert.equal(response.ok(), true, await response.text());
    };
    await post('/api/sessions', { session_id: id });
    await post(`/api/sessions/${id}/interrupt`, {});
    await context.addInitScript(id => localStorage.setItem('codex-agent.active', id), id);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let release;
    const delayed = new Promise(resolve => { release = resolve; });
    const keys = [];
    await page.route('**/api/sessions/*/resume', async route => {
      keys.push(route.request().headers()['idempotency-key']);
      if (keys.length === 1) {
        await delayed;
        await route.fulfill({ status: 409, contentType: 'application/json',
          body: JSON.stringify({ error: 'session-extensions-changed', retry_safe: true }) });
      } else await route.continue();
    });
    await page.goto(base);
    const resume = page.locator('#resume');
    await resume.click();
    assert.equal(await resume.isDisabled(), true);
    assert.equal(await resume.textContent(), '正在核对…');
    release();
    await page.locator('#error').getByText(/技能或扩展已更新/).waitFor();
    await page.waitForFunction(() => !document.querySelector('#resume')?.disabled);
    assert.equal(keys.length, 1);
    const shot = async name => {
      if (!process.env.CONSOLE_SCREENSHOTS) return;
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: path.join(process.env.CONSOLE_SCREENSHOTS, name + '.png') });
    };
    await shot('recovery-error');
    await resume.click();
    await resume.waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#error').count(), 0);
    assert.equal(keys.length, 2);
    assert.notEqual(keys[0], keys[1]);
    await page.getByRole('button', { name: '会话操作', exact: true }).click();
    await page.getByRole('menuitem', { name: '原生事件', exact: true }).click();
    const history = page.getByRole('button', { name: '查询历史', exact: true });
    const live = page.getByRole('button', { name: '查看实时', exact: true });
    await history.waitFor();
    const style = element => {
      const value = getComputedStyle(element);
      return { background: value.backgroundColor, color: value.color, radius: value.borderRadius, height: value.height };
    };
    for (const dark of [false, true]) {
      await page.evaluate(dark => document.documentElement.classList.toggle('dark', dark), dark);
      await page.waitForTimeout(250);
      assert.deepEqual(await history.evaluate(style), await live.evaluate(style));
      await shot(dark ? 'peer-buttons-dark' : 'peer-buttons-light');
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await history.isVisible(), true);
    assert.equal(await live.isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    console.log('PASS recovery feedback, pending state, safe retry, peer button parity in both themes, and mobile layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

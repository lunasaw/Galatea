'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');
const gate = () => {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
};

(async () => {
  const base = process.env.CONSOLE_TEST_URL || 'http://127.0.0.1:18889';
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const create = async () => {
      const response = await context.request.post(base + '/api/sessions', {
        headers: { Origin: base, 'Idempotency-Key': crypto.randomUUID() }, data: { options: { cwd: '/fixture' } },
      });
      assert(response.ok(), await response.text());
      return response.json();
    };
    const session = await create(), other = await create();
    const start = await context.request.post(`${base}/api/sessions/${session.id}/messages`, {
      headers: { Origin: base, 'Idempotency-Key': crypto.randomUUID() }, data: { text: 'steer-running-fixture' },
    });
    assert(start.ok(), await start.text());
    const page = await context.newPage();
    const errors = [], calls = [];
    page.on('pageerror', error => errors.push(error.message));
    let sending = gate(), reject = false;
    await page.route('**/api/native/call', async route => {
      const body = route.request().postDataJSON();
      if (body.method !== 'turn/steer') return route.continue();
      calls.push({ body, key: route.request().headers()['idempotency-key'] });
      await sending.promise;
      if (reject) { reject = false; return route.abort('failed'); }
      return route.continue();
    });
    await page.goto(`${base}/?session=${session.id}`);
    const input = page.locator('#text');
    const message = text => page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: text });
    const row = id => page.locator(`[data-slot="aui_thread-list-item-trigger"][title="${id}"]`);
    await page.locator('#interrupt').waitFor();

    // Enter renders before the request reaches the server, while replying.
    await input.fill('运行中立即补充');
    await input.press('Enter');
    await message('运行中立即补充').waitFor({ timeout: 1500 });
    assert.equal(await input.inputValue(), '');
    await message('运行中立即补充').getByText('正在发送…', { exact: true }).waitFor();
    await input.fill('下一条草稿');
    await input.press('Enter');
    assert.equal(await input.inputValue(), '下一条草稿');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].body.params.clientUserMessageId, calls[0].key);
    if (process.env.CONSOLE_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/immediate-steer-before-response.png` });
    }
    sending.release();
    await message('运行中立即补充').getByRole('status').waitFor({ state: 'detached' });
    assert.equal(await message('运行中立即补充').count(), 1);
    assert.equal(await message('steer-running-fixture').count(), 1);
    assert.equal(await input.inputValue(), '下一条草稿');

    // The send button follows the same path. Retry retains native identity.
    sending = gate(); reject = true;
    await input.fill('补充失败重试');
    await page.getByRole('button', { name: '发送补充', exact: true }).click();
    await message('补充失败重试').waitFor({ timeout: 1500 });
    assert.equal(await input.inputValue(), '');
    await input.fill('保留新的草稿');
    sending.release();
    await message('补充失败重试').getByRole('button', { name: '重试发送' }).click();
    await message('补充失败重试').getByRole('status').waitFor({ state: 'detached' });
    assert.deepEqual(calls[1], calls[2]);
    assert.equal(await message('补充失败重试').count(), 1);
    assert.equal(await input.inputValue(), '保留新的草稿');

    // Switching while a steering response is pending must not clear a new draft.
    sending = gate();
    await input.fill('切换中的补充');
    await input.press('Enter');
    await message('切换中的补充').waitFor({ timeout: 1500 });
    await row(other.id).click();
    await input.fill('其他聊天草稿');
    assert.equal(await message('切换中的补充').count(), 0);
    const completed = page.waitForResponse(response => response.url().endsWith('/api/native/call') &&
      response.request().postDataJSON()?.method === 'turn/steer');
    sending.release();
    await completed;
    assert.equal(await input.inputValue(), '其他聊天草稿');
    assert.equal(await page.locator('#session-title').getAttribute('title'), other.id);
    await row(session.id).click();
    await message('切换中的补充').waitFor();
    await message('切换中的补充').getByRole('status').waitFor({ state: 'detached' });
    assert.equal(await message('切换中的补充').count(), 1);
    assert.equal(await message('steer-running-fixture').count(), 1);
    assert.deepEqual(errors, []);
    console.log('Immediate steering passed: Enter/button, pending response, deduplication, retry identity, drafts, and switching.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

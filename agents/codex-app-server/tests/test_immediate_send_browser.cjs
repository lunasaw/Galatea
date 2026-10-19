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
    const response = await context.request.post(base + '/api/sessions', {
      headers: { Origin: base, 'Idempotency-Key': crypto.randomUUID() }, data: { options: { cwd: '/fixture' } },
    });
    assert(response.ok(), await response.text());
    const other = await response.json();
    const page = await context.newPage();
    const errors = [], calls = [];
    page.on('pageerror', error => errors.push(error.message));
    const creation = gate();
    let sending = gate(), reject = false;
    await page.route('**/api/sessions', async route => {
      if (route.request().method() === 'POST') await creation.promise;
      await route.continue();
    });
    await page.route('**/api/sessions/*/messages', async route => {
      calls.push({ body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] });
      await sending.promise;
      if (reject) {
        reject = false;
        return route.abort('failed');
      }
      await route.continue();
    });
    await page.goto(base);
    await page.getByRole('heading', { name: '今天想构建什么？' }).waitFor();
    await page.locator('.composer-model-trigger').getByText('Fixture model', { exact: true }).waitFor();
    const input = page.locator('#text');
    const message = text => page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: text });
    const row = id => page.locator(`[data-slot="aui_thread-list-item-trigger"][title="${id}"]`);

    // The server has not created the chat, yet the first message is visible.
    await input.fill('新聊天立即显示');
    await page.locator('#send').click();
    await message('新聊天立即显示').waitFor({ timeout: 1500 });
    assert.equal(await input.inputValue(), '');
    assert.equal(calls.length, 0);
    await message('新聊天立即显示').getByText('正在发送…', { exact: true }).waitFor();
    if (process.env.CONSOLE_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/immediate-send-before-response.png` });
    }
    creation.release();
    await page.waitForRequest(request => request.url().endsWith('/messages'));
    const firstId = await page.locator('#session-title').getAttribute('title');
    assert.equal(await message('新聊天立即显示').count(), 1);
    sending.release();
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    assert.equal(await message('新聊天立即显示').count(), 1);
    assert.equal(await message('新聊天立即显示').getByRole('status').count(), 0);

    // Existing chats immediately show a submission, and a failure retains it.
    sending = gate(); reject = true;
    await input.fill('失败后保留并重试');
    await input.press('Enter');
    await message('失败后保留并重试').waitFor({ timeout: 1500 });
    assert.equal(await input.inputValue(), '');
    await input.fill('下一条草稿');
    await input.press('Enter');
    assert.equal((await input.inputValue()).trim(), '下一条草稿');
    await input.fill('下一条草稿');
    assert.equal(calls.length, 2);
    sending.release();
    await message('失败后保留并重试').getByRole('button', { name: '重试发送' }).waitFor();
    assert.equal(await input.inputValue(), '下一条草稿');
    const retry = page.waitForResponse(response => response.url().endsWith('/messages'));
    await message('失败后保留并重试').getByRole('button', { name: '重试发送' }).click();
    await retry;
    await page.waitForFunction(() => document.querySelectorAll('[data-slot="aui_user-message-root"] [role="status"]').length === 0);
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    assert.deepEqual(calls[1], calls[2]);
    assert.equal(await message('失败后保留并重试').count(), 1);
    assert.equal(await input.inputValue(), '下一条草稿');

    // A slow send in one chat must not affect the next chat's draft or history.
    sending = gate();
    await input.fill('切换时仍在发送');
    await input.press('Enter');
    await message('切换时仍在发送').waitFor({ timeout: 1500 });
    await row(other.id).click();
    await input.fill('其他聊天草稿');
    assert.equal(await message('切换时仍在发送').count(), 0);
    sending.release();
    await page.waitForResponse(response => response.url().endsWith(`/sessions/${firstId}/messages`));
    assert.equal(await input.inputValue(), '其他聊天草稿');
    assert.equal(await page.locator('#session-title').getAttribute('title'), other.id);
    await row(firstId).click();
    await message('切换时仍在发送').waitFor();
    assert.equal(await message('切换时仍在发送').count(), 1);
    assert.deepEqual(errors, []);
    console.log('Immediate send passed: new/existing chats, pending responses, deduplication, failure retry, draft retention, and switching.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

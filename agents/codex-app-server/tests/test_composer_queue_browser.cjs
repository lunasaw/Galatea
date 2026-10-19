'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const base = process.env.CONSOLE_TEST_URL || 'http://127.0.0.1:18889';
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const post = async (path, data) => {
      const response = await context.request.post(base + path, { headers: { Origin: base, 'Idempotency-Key': crypto.randomUUID() }, data });
      assert(response.ok(), await response.text());
      return response.json();
    };
    const first = await post('/api/sessions', { options: { cwd: '/fixture' } });
    const second = await post('/api/sessions', { options: { cwd: '/fixture' } });
    await post(`/api/sessions/${first.id}/messages`, { text: 'queue-ui-fixture' });
    const page = await context.newPage();
    const errors = [], calls = [];
    let running = true, loseReply = false;
    let releaseAdd;
    let pendingAdd = null;
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(id => {
      if (!localStorage.getItem('queue-seeded')) {
        localStorage.setItem('queue-seeded', '1');
        localStorage.setItem('codex-agent.active', id);
      }
    }, first.id);
    // Hold a synthetic active turn so queue/steer keyboard tests are deterministic.
    await page.route('**/api/sessions', async route => {
      const response = await route.fetch();
      const sessions = await response.json();
      await route.fulfill({ response, json: sessions.map(session => session.id === first.id && running
        ? { ...session, status: 'running', active_turn_id: 'queue-active-turn' } : session) });
    });
    await page.route('**/api/native/call', async route => {
      const body = route.request().postDataJSON();
      calls.push({ ...body, key: route.request().headers()['idempotency-key'] });
      if (body.method === 'turn/steer') return route.fulfill({ json: {} });
      if (body.method === 'thread/queue/add' && pendingAdd) await pendingAdd;
      if (body.method === 'thread/queue/add' && loseReply) {
        loseReply = false;
        await route.fetch(); // Native accepted it, but the response was lost.
        return route.abort('failed');
      }
      return route.continue();
    });
    await page.goto(base);
    const input = page.locator('#text');
    const queue = page.getByRole('region', { name: '待发送消息', exact: true });
    const row = id => page.locator(`[data-slot="aui_thread-list-item-trigger"][title="${id}"]`);
    await page.locator('#interrupt').waitFor();
    await page.waitForFunction(() => Object.keys(localStorage).some(key => key.startsWith('codex-agent.projects-native:')));
    assert.equal(await queue.count(), 0);
    await input.fill('你好');
    await page.getByRole('button', { name: '加入待发送队列', exact: true }).waitFor();
    await input.press('Tab');
    await queue.getByText('你好', { exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector('#text').value === '');
    assert.equal(calls.filter(call => call.method === 'thread/queue/add').length, 1);
    assert.equal(calls.filter(call => call.method === 'turn/steer').length, 0);
    assert.equal(await page.locator('.settings-page').count(), 0);
    assert.equal(await queue.locator('textarea').count(), 0);
    await input.fill('你好');
    const queueBox = await queue.boundingBox();
    const shellBox = await page.locator('[data-slot="aui_composer-shell"]').boundingBox();
    assert(queueBox.x > shellBox.x && queueBox.width < shellBox.width);
    assert(queueBox.y < shellBox.y && Math.abs(queueBox.y + queueBox.height - shellBox.y - 20) < 2);
    const shot = async name => {
      if (!process.env.CONSOLE_SCREENSHOTS) return;
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.mouse.move(0, 0);
      await page.locator('.composer-stack').screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/${name}.png` });
    };
    await shot('composer-queue');
    await page.getByRole('button', { name: '会话操作', exact: true }).click();
    assert.equal(await page.getByRole('menuitem', { name: '输入队列', exact: true }).count(), 0);
    await page.keyboard.press('Escape');
    await input.press('Enter');
    await page.waitForFunction(() => document.querySelector('#text').value === '');
    assert.equal(calls.filter(call => call.method === 'turn/steer').length, 1);
    assert.equal(calls.filter(call => call.method === 'thread/queue/add').length, 1);

    await input.fill('输入法草稿');
    await input.dispatchEvent('keydown', { key: 'Tab', code: 'Tab', isComposing: true });
    assert.equal(await input.inputValue(), '输入法草稿');
    assert.equal(calls.filter(call => call.method === 'thread/queue/add').length, 1);
    await input.fill('丢失回执后重试');
    loseReply = true;
    await page.getByRole('button', { name: '加入待发送队列', exact: true }).click();
    await page.locator('#error').waitFor();
    assert.equal(await input.inputValue(), '丢失回执后重试');
    await page.getByRole('button', { name: '加入待发送队列', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#text').value === '');
    const retryCalls = calls.filter(call => call.method === 'thread/queue/add').slice(-2);
    assert.deepEqual(retryCalls[0], retryCalls[1]);
    await page.waitForFunction(() => document.querySelectorAll('.composer-queue-item').length === 2);
    await queue.getByRole('button', { name: '待发送消息更多操作' }).last().click();
    assert.equal(await page.getByRole('menuitem', { name: '立即发送', exact: true }).getAttribute('aria-disabled'), 'true');
    await page.getByRole('menuitem', { name: '上移', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.composer-queue-preview span').textContent === '丢失回执后重试');

    const attachment = { type: 'skill', name: 'fixture-skill', path: '/fixture/SKILL.md' };
    await post('/api/native/call', { method: 'thread/queue/add', params: {
      threadId: first.id, clientUserMessageId: crypto.randomUUID(), input: [{ type: 'text', text: '带引用的排队消息', text_elements: [] }, attachment],
    } });
    await queue.getByText('带引用的排队消息', { exact: true }).waitFor();
    await queue.getByRole('button', { name: '编辑待发送消息：带引用的排队消息' }).click();
    await queue.getByLabel('编辑待发送消息', { exact: true }).fill('修改后仍保留引用');
    await queue.getByRole('button', { name: '保存', exact: true }).click();
    await queue.getByText('修改后仍保留引用', { exact: true }).waitFor();
    assert.deepEqual(calls.filter(call => call.method === 'thread/queue/update').at(-1).params.input[1], attachment);
    await input.fill('不应被删除操作清空的草稿');
    await queue.getByRole('button', { name: '删除待发送消息' }).last().click();
    await page.waitForFunction(() => document.querySelectorAll('.composer-queue-item').length === 2);
    assert.equal(await input.inputValue(), '不应被删除操作清空的草稿');

    pendingAdd = new Promise(resolve => { releaseAdd = resolve; });
    await input.fill('切换前的排队消息');
    await page.getByRole('button', { name: '加入待发送队列' }).click();
    await row(second.id).click();
    await input.fill('另一个会话的草稿');
    releaseAdd(); pendingAdd = null;
    await page.waitForFunction(() => !document.querySelector('.composer-queue-submit')?.disabled);
    assert.equal(await input.inputValue(), '另一个会话的草稿');
    assert.equal(await queue.count(), 0);
    await row(first.id).click();
    await queue.getByText('切换前的排队消息', { exact: true }).waitFor();
    await page.reload();
    await queue.getByText('切换前的排队消息', { exact: true }).waitFor();
    await input.fill('你好');
    await page.getByRole('button', { name: '切换深色主题', exact: true }).click();
    await shot('composer-queue-dark');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await shot('composer-queue-mobile');
    assert.deepEqual(errors, []);
    console.log('Composer queue passed: inline layout, native Tab/Enter routing, retries, editing with attachments, ordering, deletion, reload, switching, dark/mobile layout.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

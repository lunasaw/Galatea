'use strict';
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.getByRole('button', {name: '新建会话', exact: true}).click();
    await page.locator('#text').fill('浏览器测试 <img src=x onerror="window.injected=1">');
    await page.getByRole('button', {name: '发送', exact: true}).click();
    await page.locator('#messages').getByText('你好，完整回复。', {exact: true}).waitFor();
    assert.equal(await page.locator('#messages .assistant').count(), 1);
    assert.equal(await page.locator('#messages .user').count(), 1);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    const sessionId = await page.locator('#session-title').textContent();
    await page.reload();
    await page.locator('#sessions button').filter({hasText: sessionId.slice(0,22)}).click();
    await page.locator('#messages').getByText('你好，完整回复。', {exact: true}).waitFor();
    if (process.env.CONSOLE_SCREENSHOT) await page.screenshot({path: process.env.CONSOLE_SCREENSHOT, fullPage: true});
    for (const [button, state] of [['中断', 'interrupted'], ['核对并恢复', 'ready'], ['关闭会话', 'closed'], ['核对并恢复', 'ready']]) {
      await page.getByRole('button', {name: button, exact: true}).click();
      await page.locator('#sessions button.selected').filter({hasText: state}).waitFor();
    }
    await page.getByRole('button', {name: '执行流程', exact: true}).click();
    await page.locator('#steps').getByText('galatea_observe_job', {exact: true}).waitFor();
    await page.getByRole('button', {name: '原生事件', exact: true}).click();
    await page.locator('#native-events details').first().locator('summary').click();
    await page.locator('#native-events pre').first().waitFor();
    await page.locator('#method').fill('item/completed');
    await page.getByRole('button', {name: '查询历史', exact: true}).click();
    await page.waitForFunction(() => {
      const rows = [...document.querySelectorAll('#native-events summary')];
      return rows.length === 2 && rows.every(row => row.textContent.includes('item/completed'));
    });
    await page.locator('#method').fill('');
    await page.route('**/api/sessions/*/events?after=*', async route => {
      await route.fulfill({status: 409, contentType: 'application/json', body: '{"error":"EPOCH_MISMATCH"}'});
    }, {times: 1});
    await page.locator('#sessions button.selected').click();
    await page.locator('#reset').waitFor({state: 'visible'});
    await page.locator('#messages').getByText('你好，完整回复。', {exact: true}).waitFor();
    assert.equal(await page.locator('input[type=password]').count(), 0);
    await page.locator('#sessions button').filter({hasText: 'history'}).click();
    await page.locator('#messages .body').getByText('history message 250', {exact: true}).waitFor();
    await page.locator('#older-messages').click();
    await page.locator('#messages .body').getByText('history message 50', {exact: true}).waitFor({timeout: 3000});
    assert.equal(await page.locator('#messages .body').getByText('history message 250', {exact: true}).count(), 0);
    await page.locator('#older-messages').click();
    await page.locator('#messages .body').getByText('history message 0', {exact: true}).waitFor();
    assert.equal(await page.locator('#older-messages').isDisabled(), true);
    await page.locator('#latest-messages').click();
    await page.locator('#messages .body').getByText('history message 449', {exact: true}).waitFor();
    assert.deepEqual(errors, []);
    process.stdout.write('Browser chat, controls, reload/reset, trace, filters, pagination, XSS and anonymous access passed.\n');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });

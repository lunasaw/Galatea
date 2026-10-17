'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1582, height: 1022 } }); const calls = []; const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (r.url().endsWith('/api/native/call')) calls.push(r.postDataJSON()); });
    await page.route('**/api/sessions/*/history?*', route => {
      const url = new URL(route.request().url());
      if (url.searchParams.get('method') !== 'item/commandExecution/requestApproval') return route.continue();
      const base = { runtime_instance_id: 'review-fixture', rpc_id: 'approval-one', method: 'item/commandExecution/requestApproval', observed_at: '2026-10-16T10:00:00Z' };
      return route.fulfill({ json: { events: [
        { ...base, event_id: 'a:1', kind: 'request', direction: 'runtime_to_host', payload: { params: { command: 'touch allowed-file', cwd: '/fixture', reason: 'Requested change' } } },
        { ...base, event_id: 'a:2', kind: 'response', direction: 'host_to_runtime', payload: { result: { decision: 'accept' } } },
      ], coverage: { status: 'complete' }, has_more: false, next_cursor: 'a:2', watermark: 'a:2' } });
    });
    await page.goto(process.env.CONSOLE_TEST_URL);
    for (const name of ['Fixture App', 'fixture-plugin', 'fixture-skill']) {
      await page.getByRole('button', { name: '添加到聊天', exact: true }).click();
      await page.getByRole('menuitem', { name: new RegExp(name) }).click();
      await page.getByRole('menu', { name: '添加到聊天', exact: true }).waitFor({ state: 'hidden' });
    }
    assert.equal(await page.locator('.aui-composer-attachments').getByText('Fixture App', { exact: true }).count() > 0, true);
    await page.locator('#text').fill('引用能力测试');
    const sent = page.waitForRequest(r => /\/api\/sessions\/[^/]+\/messages$/.test(r.url()));
    await page.locator('#send').click(); const inputs = (await sent).postDataJSON().input_items;
    assert.ok(inputs.some(p => p.type === 'mention' && p.path === 'app://fixture-app'));
    assert.ok(inputs.some(p => p.type === 'mention' && p.path === 'plugin://fixture-plugin@fixture'));
    assert.ok(inputs.some(p => p.type === 'skill' && p.path === '/fixture/SKILL.md'));
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '会话操作', exact: true }).click();
    await page.getByRole('menuitem', { name: '代码审查与审批记录', exact: true }).click();
    const review = page.getByRole('tabpanel', { name: '代码审查与审批记录面板', exact: true });
    await review.getByText('命令执行 · 已允许', { exact: true }).waitFor();
    await review.locator('pre').getByText('touch allowed-file', { exact: true }).waitFor();
    await review.getByLabel('审查目标', { exact: true }).selectOption('baseBranch');
    await review.getByLabel('基准分支', { exact: true }).fill('main');
    const reviewed = page.waitForResponse(r => r.url().endsWith('/api/native/call') && r.request().postDataJSON().method === 'review/start');
    await review.getByRole('button', { name: '开始审查', exact: true }).click();
    await reviewed;
    assert.ok(calls.some(c => c.method === 'review/start' && c.params.target.branch === 'main'));
    await page.getByRole('button', { name: '会话操作', exact: true }).click();
    await page.getByRole('menuitem', { name: '子 Agent', exact: true }).click();
    await page.getByRole('tabpanel', { name: '子 Agent面板', exact: true }).getByRole('heading', { name: '当前聊天的子 Agent', exact: true }).waitFor();
    assert.ok(calls.some(c => c.method === 'thread/list' && c.params.ancestorThreadId && c.params.sourceKinds.includes('subAgent')));
    await page.getByRole('button', { name: '会话历史', exact: true }).click();
    await page.getByRole('region', { name: '会话历史', exact: true }).getByLabel('已归档', { exact: true }).check();
    assert.equal(await page.locator('.settings-page').count(), 0);
    if (process.env.CONSOLE_SCREENSHOTS) await page.screenshot({ path: process.env.CONSOLE_SCREENSHOTS + '/history.png' });
    await page.getByRole('button', { name: '返回聊天', exact: true }).click();
    await page.getByRole('button', { name: '设置', exact: true }).click();
    const navigation = page.getByRole('navigation', { name: '设置分类', exact: true });
    assert.equal(await navigation.getByRole('button', { name: /会话与 Agent|代码审查|长期目标|输入队列|后台终端|^文件$/ }).count(), 0);
    assert.deepEqual(errors, []);
    console.log('Conversation panels: native references, review targets, Agent scope and independent history passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

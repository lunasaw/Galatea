'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(12000);
    const calls = [], errors = [];
    let used = 810, capacity = 1000;
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (r.url().endsWith('/api/native/call')) calls.push(r.postDataJSON()); });
    await page.route('**/api/native/call', async route => {
      const { method } = route.request().postDataJSON();
      if (method === 'account/read') return route.fulfill({ json: { account: { type: 'apiKey' }, requiresOpenaiAuth: true } });
      if (method === 'account/rateLimits/read') return route.fulfill({ status: 422, json: { error: 'chatgpt authentication required to read rate limits' } });
      return route.continue();
    });
    await page.route('**/snapshot*', async route => {
      const response = await route.fetch(), data = await response.json();
      if (data.turns?.length) data.turns.at(-1).usage = { last: { totalTokens: used }, total: { totalTokens: 123456 }, modelContextWindow: capacity };
      await route.fulfill({ response, json: data });
    });
    const button = name => page.getByRole('button', { name, exact: true });
    const input = page.locator('#text');
    const close = async () => { await button('返回聊天').click(); await page.locator('.settings-page').waitFor({ state: 'hidden' }); };
    const slash = async name => { await input.fill('/' + name); await input.press('Enter'); };
    const category = async name => page.getByRole('navigation', { name: '设置分类' }).getByRole('button', { name, exact: true }).click();
    const screenshot = async name => { if (process.env.CONSOLE_SCREENSHOTS) { fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true }); await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/${name}.png` }); } };
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.locator('.composer-context-trigger').waitFor();
    assert.match(await page.locator('.composer-context-trigger').innerText(), /—/);
    await input.fill('session-settings-fixture'); await page.locator('#send').click();
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    await page.getByRole('button', { name: /^上下文已使用 81%/ }).waitFor();
    const sessionId = await page.evaluate(() => localStorage.getItem('codex-agent.active'));
    const sessions = await (await page.request.get(process.env.CONSOLE_TEST_URL + '/api/sessions')).json();
    const threadId = (Array.isArray(sessions) ? sessions : sessions.sessions).find(s => s.id === sessionId).thread_id;

    // All categories are inspected for session actions; shared configuration remains.
    await button('设置').click();
    const nav = page.getByRole('navigation', { name: '设置分类' });
    assert.equal(await nav.getByRole('button', { name: /^(模型与权限|聊天资源|执行流程|原生事件|终端)$/ }).count(), 0);
    await category('使用情况和计费'); await page.getByText('API 密钥登录', { exact: true }).waitFor();
    assert.equal(await page.locator('.settings-body').getByText('当前聊天', { exact: true }).count(), 0);
    assert.equal(await page.locator('.settings-body').getByText('累计 Token', { exact: true }).count(), 0);
    assert.equal(calls.filter(c => c.method === 'account/rateLimits/read').length, 0);
    await category('模型与实验功能'); await page.getByText('实验功能', { exact: true }).waitFor();
    assert.equal(await button('应用到当前会话').count(), 0);
    await category('Memory'); assert.equal(await page.getByLabel('会话记忆模式').count(), 0);
    await category('Hooks'); assert.equal(await page.getByText('当前会话执行记录', { exact: true }).count(), 0);
    await category('语音'); assert.equal(await page.getByText('初始上下文、指令与 Handoff', { exact: true }).count(), 0);
    await category('高级原生能力'); await page.getByText('存储维护', { exact: true }).waitFor();
    assert.equal(await page.getByText('修改聊天运行设置', { exact: true }).count(), 0);
    await category('Skills'); await page.getByText('fixture-skill', { exact: true }).waitFor();
    assert.equal(await button('在对话中使用').count(), 0);
    await category('MCP'); await page.locator('[data-mcp]').first().waitFor();
    assert.equal(await page.getByText('工具与资源', { exact: true }).count(), 0);
    assert.equal(calls.filter(c => c.method === 'mcpServerStatus/list').at(-1).params.threadId, undefined);
    await category('Apps'); await page.getByRole('heading', { name: 'Codex CLI Apps', exact: true }).waitFor();
    assert.equal(await button('打开应用').count(), 0);
    assert.equal(calls.filter(c => c.method === 'app/list').at(-1).params.threadId, undefined);
    await screenshot('global-settings'); await close();

    // Slash entry and the ring open the same chat status; an existing draft survives.
    await input.fill('保留这段草稿'); await button('快捷命令').click();
    await page.getByRole('option', { name: /^状态/ }).click();
    const status = page.getByRole('dialog', { name: '会话状态', exact: true });
    await status.getByText('已使用 81% · 剩余 19%', { exact: true }).waitFor();
    assert.equal(await input.inputValue(), '保留这段草稿');
    await status.getByText('810 / 1,000 Token', { exact: true }).waitFor();
    await status.getByText(threadId, { exact: true }).waitFor();
    assert.equal(await status.getByRole('progressbar').getAttribute('aria-valuenow'), '81');
    assert.equal(await status.getByText(/自动压缩参考点|距参考点/).count(), 0);
    await status.getByText('上下文占用较高，后续对话可能需要压缩。', { exact: true }).waitFor();
    await screenshot('context-status-light'); await button('关闭会话状态').click();
    await slash('status'); await status.waitFor(); await button('关闭会话状态').click();
    assert.equal(await input.inputValue(), '');

    const tabs = [['settings', '模型与权限'], ['resources', '聊天资源'], ['memory', '会话记忆'], ['hooks', 'Hooks 执行记录'], ['advanced', '高级会话操作'], ['loop', '执行流程'], ['native', '原生事件'], ['voice', '会话语音上下文'], ['skills', 'Skills'], ['apps', 'Apps'], ['plugins', '会话插件'], ['feedback', '会话反馈']];
    for (const [command, title] of tabs) {
      await slash(command); const dialog = page.getByRole('dialog', { name: title, exact: true }); await dialog.waitFor();
      await dialog.locator('.capability-page, .native-form').first().waitFor();
      assert.equal(await nav.count(), 0, `${command} must not open global settings`);
      assert.match(await dialog.locator('.session-scope-label').innerText(), /当前聊天/);
      if (command === 'settings') { await button('应用到当前会话').waitFor(); assert.equal(await page.getByText('实验功能', { exact: true }).count(), 0); await screenshot('session-model-settings'); }
      if (command === 'memory') { const saved = page.waitForResponse(r => r.url().endsWith('/api/native/call') && r.request().postDataJSON().method === 'thread/memoryMode/set'); await page.getByLabel('会话记忆模式', { exact: true }).selectOption('disabled'); await saved; assert.equal(calls.filter(c => c.method === 'thread/memoryMode/set').at(-1).params.threadId, threadId); }
      if (command === 'advanced') { await page.getByText('修改聊天运行设置', { exact: true }).waitFor(); assert.equal(await page.getByText('存储维护', { exact: true }).count(), 0); await screenshot('session-advanced'); }
      if (command === 'voice') { await page.getByLabel('包含 realtimeStartInstructions', { exact: true }).check(); await page.getByLabel('realtimeStartInstructions', { exact: true }).fill('Only this chat'); await button('保存会话语音上下文').click(); assert.equal(await page.evaluate(id => JSON.parse(sessionStorage.getItem(`codex-agent.voice-context.${id}`)).realtimeStartInstructions, threadId), 'Only this chat'); }
      if (command === 'plugins') { await button('应用会话插件').click(); await page.getByText('请求已完成', { exact: true }).waitFor(); assert.deepEqual(calls.filter(c => c.method === 'thread/settings/update').at(-1).params, { threadId, disabledPluginIds: [] }); }
      await close();
    }
    await slash('mcp'); await page.getByRole('option', { name: /^会话工具与资源/ }).click();
    await page.getByRole('dialog', { name: 'MCP', exact: true }).waitFor();
    await page.getByText('工具与资源', { exact: true }).first().waitFor();
    assert.equal(calls.filter(c => c.method === 'mcpServerStatus/list').at(-1).params.threadId, threadId);
    assert.equal(await button('重新加载全部').count(), 0); await close();

    used = 960; await page.reload(); await page.getByRole('button', { name: /^上下文已使用 96%/ }).click();
    await status.getByText('上下文接近容量上限，可能需要压缩。', { exact: true }).waitFor();
    await button('关闭会话状态').click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: /^上下文已使用 96%/ }).click();
    await screenshot('context-status-mobile');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const bounds = await status.boundingBox(); assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
    await button('关闭会话状态').click(); await slash('settings'); await page.getByRole('dialog', { name: '模型与权限', exact: true }).getByLabel('模型', { exact: true }).waitFor(); await page.waitForTimeout(250); await screenshot('session-settings-mobile'); await close();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await button('切换深色主题').click(); await slash('status'); await screenshot('context-status-dark'); await button('关闭会话状态').click();
    used = 120; await page.reload(); await page.getByRole('button', { name: /^上下文已使用 12%/ }).click();
    await status.getByText('已使用 12% · 剩余 88%', { exact: true }).waitFor();
    assert.equal(await status.locator('.context-warning').count(), 0, 'usage and warning reset after native compaction updates');
    await button('关闭会话状态').click();
    used = 0; capacity = 0; await page.reload(); await page.getByRole('button', { name: /^查看会话状态/ }).click();
    assert.equal(await status.getByRole('progressbar').count(), 0, 'unknown capacity is not zero percent');
    await button('关闭会话状态').click();
    await input.press('Control+Shift+o'); await page.waitForFunction(() => !localStorage.getItem('codex-agent.active'));
    await page.locator('.composer-context-trigger').click(); await status.getByText('尚未开始聊天', { exact: true }).waitFor();
    assert.equal(await status.getByText('123,456', { exact: true }).count(), 0, 'new chat never borrows the previous chat usage');
    assert.equal(calls.filter(c => c.method === 'account/rateLimits/read').length, 0);
    assert.deepEqual(errors, []);
    console.log('Session settings: global/session audit, all slash panels, scoped writes, draft preservation, context progress and warnings, unknown data, chat switching, API-key limits, mobile and dark theme passed.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });

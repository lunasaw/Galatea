'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    page.setDefaultTimeout(10000);
    await page.addInitScript(() => localStorage.setItem('codex-agent.theme', 'dark'));
    const errors = [], calls = [], messages = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => {
      if (request.url().endsWith('/api/native/call')) calls.push(request.postDataJSON());
      if (/\/api\/sessions\/[^/]+\/messages$/.test(request.url())) messages.push(request.postDataJSON());
    });
    await page.goto(process.env.CONSOLE_TEST_URL);
    const input = page.locator('#text');
    const root = page.getByRole('listbox', { name: '快捷命令', exact: true });
    const options = (name) => page.getByRole('listbox', { name, exact: true });
    const screenshot = async (name) => {
      if (!process.env.CONSOLE_SCREENSHOTS) return;
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: process.env.CONSOLE_SCREENSHOTS + '/' + name + '.png' });
    };
    await input.fill('/');
    await root.waitFor();
    assert.deepEqual(await root.locator('.slash-command-label').allTextContents(), [
      'MCP', '使用情况和计费', '侧边', '分享聊天', '创建聊天分支', '初始化', '压缩', '反馈', '归档', '推理', '模型',
      '新聊天', '计划模式', '目标', '输入队列', '搜索', '权限', 'Skills', 'Apps', '插件', '恢复聊天', '审查', '回退', '状态', '帮助',
    ]);
    assert.match(await root.getByRole('option', { name: /^侧边/ }).getAttribute('title'), /尚未接入/);
    assert.equal(await root.getByRole('option', { name: /^分享聊天/ }).getAttribute('aria-disabled'), 'true');
    await screenshot('slash-commands-dark');
    await input.press('ArrowDown');
    await input.press('ArrowDown');
    assert.equal(await root.locator('[aria-selected=true] .slash-command-label').textContent(), '初始化', 'arrows skip unsupported and unavailable commands');
    await input.press('End');
    assert.equal(await root.locator('[aria-selected=true] .slash-command-label').textContent(), '帮助');
    assert.ok(await page.locator('.desktop-slash-menu').evaluate((element) => element.scrollTop > 0));
    await input.press('Home');
    await input.press('ArrowRight');
    const mcp = options('MCP选项');
    await mcp.getByRole('option', { name: /fixture-app/ }).waitFor();
    assert.match(await mcp.textContent(), /2 个工具/);
    await input.press('ArrowLeft');
    await root.waitFor();
    await input.press('Escape');
    await root.waitFor({ state: 'hidden' });
    assert.equal(await input.inputValue(), '/');
    await input.fill('/推理');
    assert.equal(await root.getByRole('option').count(), 1);
    await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true });
    await root.waitFor();
    await input.press('Enter');
    const reasoning = options('推理选项');
    await reasoning.getByRole('option', { name: '极高', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#text').value === '');
    assert.match(await page.locator('.composer-model-trigger').textContent(), /极高/);
    await input.fill('/MODEL');
    await input.press('Tab');
    await options('模型选项').getByRole('option', { name: /Alternate model/ }).click();
    await page.waitForFunction(() => document.querySelector('#text').value === '');
    assert.match(await page.locator('.composer-model-trigger').textContent(), /Alternate model高/);
    assert.equal(messages.length, 0);

    // Clicking Send blurs the textarea before submit; completion must still win.
    await input.fill('/');
    await page.locator('#send').click();
    await mcp.waitFor();
    assert.equal(messages.length, 0, 'the slash trigger never becomes a model message');
    await input.press('Escape');
    await input.fill('/usage');
    await page.locator('#send').click();
    const settings = page.getByRole('region', { name: '设置', exact: true });
    await settings.getByText('fixture@example.test · pro', { exact: true }).waitFor();
    await settings.getByText('已使用 24%', { exact: true }).waitFor();
    await settings.getByText('12.50', { exact: true }).waitFor();
    await page.getByRole('button', { name: '返回聊天', exact: true }).click();
    await input.fill('/feedback');
    await input.press('Enter');
    await settings.getByRole('heading', { name: '诊断与反馈', exact: true }).waitFor();
    assert.equal(calls.filter((call) => call.method === 'feedback/upload').length, 0, 'opening feedback never submits a report');
    await page.getByRole('button', { name: '返回聊天', exact: true }).click();

    // Init follows the native CLI's prompt flow, including safe handling of an existing guide.
    await input.fill('/init');
    await input.press('Enter');
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector('#text').value === '');
    assert.equal(messages.length, 1);
    assert.match(messages[0].text, /先检查.*AGENTS\.md.*若已存在，不覆盖、不修改/);
    assert.ok(!messages[0].text.startsWith('/'));
    await input.fill('/compact');
    await input.press('Enter');
    await page.waitForFunction(() => document.querySelector('#text').value === '');
    assert.ok(calls.some((call) => call.method === 'thread/compact/start' && call.params.threadId));

    // A running turn must not receive slash commands through the steer button.
    await input.fill('slash-running-fixture');
    await page.locator('#send').click();
    await input.fill('/compact');
    const compact = root.getByRole('option', { name: /^压缩/ });
    await compact.waitFor();
    assert.equal(await compact.getAttribute('aria-disabled'), 'true');
    await page.getByRole('button', { name: '发送补充', exact: true }).click();
    assert.equal(calls.filter((call) => call.method === 'turn/steer').length, 0);
    await page.getByText('你好，完整回复。', { exact: true }).nth(1).waitFor();
    assert.equal(await input.inputValue(), '/compact', 'a blocked command keeps its draft');
    await input.fill('/');
    await root.getByRole('option', { name: /压缩.*已使用 81%/ }).waitFor();
    await screenshot('slash-commands-active');
    await input.fill('/mcp');
    await input.press('Enter');
    await mcp.getByRole('option', { name: /fixture-app/ }).waitFor();
    assert.ok(calls.filter((call) => call.method === 'mcpServerStatus/list').at(-1).params.threadId, 'MCP status follows the current thread');
    await input.fill('/fork');
    await input.press('Enter');
    await options('聊天分支选项').getByRole('option', { name: /^从指定轮次/ }).click();
    const fork = page.getByRole('dialog', { name: '分叉会话', exact: true });
    const turns = fork.getByRole('combobox', { name: '分支轮次', exact: true });
    await turns.locator('option').nth(1).waitFor({ state: 'attached' });
    const firstTurn = await turns.locator('option').first().getAttribute('value');
    await turns.selectOption(firstTurn);
    await screenshot('slash-fork');
    await fork.getByRole('button', { name: '确认分叉会话', exact: true }).click();
    await fork.waitFor({ state: 'hidden' });
    assert.equal(calls.find((call) => call.method === 'thread/fork').params.lastTurnId, firstTurn);
    await input.fill('/archive');
    await input.press('Enter');
    const archive = page.getByRole('dialog', { name: '归档会话', exact: true });
    await archive.waitFor();
    assert.equal(calls.filter((call) => call.method === 'thread/archive').length, 0);
    await archive.getByRole('button', { name: '确认归档会话', exact: true }).click();
    await archive.waitFor({ state: 'hidden' });
    assert.ok(calls.some((call) => call.method === 'thread/archive'));
    assert.equal(messages.length, 2);

    // Themes and narrow viewports keep the popup aligned with the composer.
    await page.getByRole('button', { name: '切换浅色主题', exact: true }).click();
    await input.fill('/');
    await root.waitFor();
    await screenshot('slash-commands-light');
    await page.setViewportSize({ width: 390, height: 844 });
    await screenshot('slash-commands-mobile');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const bounds = await page.locator('.desktop-slash-menu').boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390 && bounds.y >= 0);
    assert.deepEqual(errors, []);
    console.log('Slash commands: reference order, Chinese search, keyboard/IME, submenus, settings, native init/compact/fork/archive, running guards, themes and mobile passed.');
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });

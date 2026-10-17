'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1582, height: 1022 } });
    const errors = []; page.on('pageerror', (error) => errors.push(error.message));
    const calls = [];
    page.on('request', (request) => { if (request.url().endsWith('/api/native/call')) calls.push(request.postDataJSON()); });
    await page.goto(process.env.CONSOLE_TEST_URL);
    const menu = async (name) => {
      await page.getByRole('button', { name: '添加到聊天', exact: true }).click();
      await page.getByRole('menuitem', { name }).click();
    };
    await menu(/计划模式/);
    await page.getByRole('button', { name: '关闭计划模式', exact: true }).waitFor();
    await menu(/^目标/);
    await page.getByRole('button', { name: '退出目标输入', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '关闭计划模式', exact: true }).count(), 0);
    await menu(/计划模式/);
    assert.equal(await page.getByRole('button', { name: '退出目标输入', exact: true }).count(), 0);
    await menu(/^目标/);
    await page.locator('#text').fill('验证原生目标，不作为普通消息发送');
    await page.getByRole('button', { name: '添加到聊天', exact: true }).click();
    assert.equal(await page.getByRole('menuitem', { name: /文件和文件夹/ }).isDisabled(), true);
    await page.keyboard.press('Escape');
    let reject = true;
    await page.route('**/api/native/call', (route) => {
      if (route.request().postDataJSON().method === 'thread/goal/set' && reject) {
        reject = false;
        return route.fulfill({ status: 400, contentType: 'application/json', body: '{"error":"goal-rejected"}' });
      }
      return route.continue();
    });
    await page.getByRole('button', { name: '发送目标', exact: true }).click();
    await page.getByText(/goal-rejected/).waitFor();
    assert.equal(await page.locator('#text').inputValue(), '验证原生目标，不作为普通消息发送');
    await page.getByRole('button', { name: '发送目标', exact: true }).click();
    const goal = page.getByRole('region', { name: '当前聊天的目标', exact: true });
    await goal.waitFor();
    await page.waitForFunction(() => document.querySelector('#text').value === '');
    await goal.getByRole('button', { name: /目标/ }).click();
    await goal.getByRole('button', { name: '暂停目标', exact: true }).click();
    await goal.getByRole('button', { name: '继续目标', exact: true }).waitFor();
    await goal.getByLabel('Token 预算（可选）', { exact: true }).fill('1200');
    await goal.getByRole('button', { name: '保存目标', exact: true }).click();
    await goal.getByText('12 / 1,200 Token', { exact: true }).waitFor();
    await goal.getByRole('button', { name: /目标 已暂停/ }).click();
    const openQueue = async () => {
      await page.getByRole('button', { name: '会话操作', exact: true }).click();
      await page.getByRole('menuitem', { name: '输入队列', exact: true }).click();
    };
    await openQueue();
    const queue = page.getByRole('region', { name: '当前聊天的输入队列', exact: true });
    for (const text of ['第一条排队输入', '第二条排队输入']) {
      await queue.getByLabel('排队输入', { exact: true }).fill(text);
      await queue.getByRole('button', { name: '加入队列', exact: true }).click();
      await queue.getByText(text, { exact: true }).waitFor();
    }
    await queue.getByRole('button', { name: '上移', exact: true }).nth(1).click();
    await page.waitForFunction(() => document.querySelector('[aria-label="当前聊天的输入队列"] li p').textContent === '第二条排队输入');
    await queue.getByRole('button', { name: '编辑', exact: true }).first().click();
    await queue.getByLabel('编辑排队消息', { exact: true }).fill('修改后的排队输入');
    await queue.getByRole('button', { name: '保存消息', exact: true }).click();
    await queue.getByText('修改后的排队输入', { exact: true }).waitFor();
    await queue.getByRole('button', { name: '删除', exact: true }).last().click();
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="当前聊天的输入队列"] li').length === 1);
    await queue.getByRole('button', { name: '启动', exact: true }).click();
    await page.locator('[data-slot=aui_message-group]').getByText('你好，完整回复。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '搜索聊天', exact: true }).click();
    const search = page.getByRole('dialog', { name: '搜索聊天', exact: true });
    await search.getByRole('searchbox', { name: '搜索聊天', exact: true }).fill('修改后的排队输入');
    await search.getByRole('button', { name: '查看命中位置', exact: true }).first().waitFor();
    assert.ok(calls.some((call) => call.method === 'thread/search'));
    await search.locator('[data-search-choice]').first().click();
    await page.waitForFunction(() => !document.querySelector('[role=dialog]'));
    await page.getByRole('button', { name: '搜索聊天', exact: true }).click();
    await search.getByRole('button', { name: '搜索文件', exact: true }).click();
    const files = page.getByRole('dialog', { name: '搜索文件', exact: true });
    await files.getByRole('searchbox', { name: '搜索文件', exact: true }).fill('README');
    await files.getByRole('button', { name: /README.md/ }).waitFor();
    await files.getByRole('button', { name: /README.md/ }).click();
    await page.getByRole('tab', { name: 'README.md', exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('[role=dialog]'));
    await page.getByRole('button', { name: '会话操作', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Agent 后台终端', exact: true }).click();
    await page.getByRole('tabpanel', { name: '后台终端面板', exact: true }).getByText('fixture watcher', { exact: true }).waitFor();
    if (process.env.CONSOLE_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.getByRole('button', { name: '搜索聊天', exact: true }).click();
      await search.getByRole('button', { name: '新聊天', exact: true }).waitFor();
      await page.waitForTimeout(200);
      await page.screenshot({ path: process.env.CONSOLE_SCREENSHOTS + '/chat-search.png' });
    }
    assert.deepEqual(errors, []);
    console.log('Composer capability browser acceptance passed.');
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });

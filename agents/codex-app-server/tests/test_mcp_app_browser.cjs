'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage(); const calls = []; const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => { if (request.url().endsWith('/api/native/call')) calls.push(request.postDataJSON()); });
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.locator('#text').fill('mcp-app-fixture'); await page.locator('#send').click();
    // Completion folds the activity. Open it after the reply settles, so lazy
    // App loading cannot race the automatic collapse while clicking a tool.
    await page.locator('[data-final="true"]').getByText('你好，完整回复。', { exact: true }).waitFor();
    const activity = page.locator('[data-activity]').filter({ has: page.locator('iframe[title="fixture-app 交互视图"]') });
    await activity.getByRole('button', { name: '处理过程', exact: true }).click();
    const app = page.frameLocator('iframe[title="fixture-app 交互视图"]');
    await app.locator('#status').getByText('MCP App ready', { exact: true }).waitFor();
    const iframe = page.locator('iframe[title="fixture-app 交互视图"]');
    assert.equal(await iframe.getAttribute('sandbox'), 'allow-scripts');
    const frame = await (await iframe.elementHandle()).contentFrame();
    assert.equal(await frame.evaluate(() => { try { return !!parent.document; } catch { return false; } }), false);
    const request = page.waitForRequest(r => r.url().endsWith('/api/native/call') && r.postDataJSON().method === 'mcpServer/tool/call');
    await app.getByRole('button', { name: 'Read tool', exact: true }).click();
    assert.equal((await request).postDataJSON().params.server, 'fixture-app');
    await app.getByText('native app tool result', { exact: true }).waitFor();
    await app.getByRole('button', { name: 'Change tool', exact: true }).click();
    await page.getByText('应用请求执行 change', { exact: true }).waitFor();
    assert.equal(calls.filter(c => c.method === 'mcpServer/tool/call' && c.params.tool === 'change').length, 0);
    await page.getByRole('button', { name: '拒绝', exact: true }).click();
    await app.getByText('用户未允许此操作', { exact: true }).waitFor();
    await app.getByRole('button', { name: 'Change tool', exact: true }).click();
    await page.getByRole('button', { name: '允许本次', exact: true }).click();
    await app.getByText('native app tool result', { exact: true }).waitFor();
    assert.equal(calls.filter(c => c.method === 'mcpServer/tool/call' && c.params.tool === 'change').length, 1);
    assert.deepEqual(errors, []);
    console.log('MCP App native resources, handshake, results, scoped tools and sandbox isolation passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

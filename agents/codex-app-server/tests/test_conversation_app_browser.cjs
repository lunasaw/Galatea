'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1582, height: 1022 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.locator('#text').fill('mcp-app-fixture');
    await page.locator('#send').click();
    const panel = page.getByRole('tabpanel', { name: 'Apps面板', exact: true });
    const app = panel.frameLocator('iframe');
    await app.locator('#status').getByText('MCP App ready', { exact: true }).waitFor();
    const session = new URL(page.url()).searchParams.get('session');
    assert.ok(session);
    assert.equal(await page.locator('.aui-thread-root iframe').count(), 0);
    const original = await panel.locator('iframe').elementHandle();
    await app.locator('#draft').fill('retained selection');
    await page.getByRole('tab', { name: '摘要', exact: true }).click();
    await page.getByRole('tab', { name: 'Apps', exact: true }).click();
    assert.equal(await original.evaluate(e => e.isConnected), true);
    await page.getByRole('button', { name: '关闭任务面板', exact: true }).click();
    await page.getByRole('button', { name: '打开 Apps', exact: true }).click();
    assert.equal(await app.locator('#draft').inputValue(), 'retained selection');
    assert.equal(new URL(page.url()).searchParams.get('session'), session);
    await page.reload();
    await panel.frameLocator('iframe').locator('#status').getByText('MCP App ready', { exact: true }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('session'), session);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('dialog', { name: 'Apps', exact: true }).frameLocator('iframe').locator('#draft').waitFor();
    assert.deepEqual(errors, []);
    console.log('Conversation App: automatic display from native tool, same chat, no duplicate frame, retention, reload and mobile passed.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });

'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    const calls = []; const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => {
      if (request.url().endsWith('/api/native/call')) calls.push({ ...request.postDataJSON(), key: request.headers()['idempotency-key'] });
    });
    await page.goto(process.env.CONSOLE_TEST_URL);
    const open = async () => page.getByRole('button', { name: '打开终端', exact: true }).click();
    await open();
    await page.getByRole('tab', { name: '主机进程', exact: true }).click();
    await page.getByRole('button', { name: '启动终端', exact: true }).click();
    const input = page.locator('.pty-pane:not([hidden]) .xterm-helper-textarea');
    await input.focus(); await page.keyboard.type('before-reload');
    await page.waitForFunction(() => document.querySelector('.pty-pane:not([hidden]) .xterm-rows')?.textContent.includes('before-reload'));
    await page.reload(); await open();
    await page.getByRole('tab', { name: '主机进程', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.pty-pane:not([hidden]) .xterm-rows')?.textContent.includes('before-reload'));
    const spawns = calls.filter((call) => call.method === 'process/spawn');
    assert.equal(spawns.length, 2); assert.deepEqual(spawns[0], spawns[1]);
    await input.focus(); await page.keyboard.type('after-reload');
    await page.waitForFunction(() => document.querySelector('.pty-pane:not([hidden]) .xterm-rows')?.textContent.includes('after-reload'));
    await page.getByRole('button', { name: '停止进程', exact: true }).click();
    await page.getByText('进程已退出 · 退出码 0', { exact: true }).waitFor();
    await page.getByRole('button', { name: '关闭已退出的终端', exact: true }).click();
    assert.deepEqual(errors, []);
    console.log('Terminal reconnect preserves the process identity, replays output, accepts stdin and exits.');
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });

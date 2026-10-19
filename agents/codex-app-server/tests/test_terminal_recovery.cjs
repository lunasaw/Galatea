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
    await page.getByRole('button', { name: '新建终端', exact: true }).click();
    await page.getByText('高级执行设置', { exact: true }).click();
    await page.getByLabel('包含 tty', { exact: true }).check();
    await page.getByLabel('tty', { exact: true }).uncheck();
    await page.getByLabel('包含 env', { exact: true }).check();
    await page.getByLabel('env新增字段名', { exact: true }).fill('NATIVE_SECRET');
    await page.getByRole('button', { name: '添加字段', exact: true }).click();
    await page.getByLabel('NATIVE_SECRET', { exact: true }).fill('browser-memory-only');
    await page.getByLabel('包含 outputBytesCap', { exact: true }).check();
    await page.getByLabel('outputBytesCap', { exact: true }).fill('4');
    await Promise.all([
      page.waitForRequest(r => r.url().endsWith('/api/native/call') && r.postDataJSON().method === 'process/spawn'),
      page.getByRole('button', { name: '启动终端', exact: true }).click(),
    ]);
    const advanced = calls.filter(c => c.method === 'process/spawn').at(-1);
    assert.equal(advanced.params.tty, false); assert.equal(advanced.params.outputBytesCap, 4);
    assert.deepEqual(advanced.params.env, { NATIVE_SECRET: 'browser-memory-only' });
    assert.equal(advanced.params.size, undefined);
    assert.ok(!await page.evaluate(() => sessionStorage.getItem('codex-agent.terminal-handles').includes('browser-memory-only')));
    let receiptReads = 0;
    page.on('request', r => { if (r.url().includes('/api/native/receipts/')) receiptReads++; });
    const beforeReload = calls.filter(c => c.method === 'process/spawn').length;
    await page.reload(); await open();
    await page.getByRole('tab', { name: '主机进程', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.pty-pane:not([hidden]) .xterm-helper-textarea'));
    await page.getByRole('button', { name: '停止进程', exact: true }).click();
    await page.getByText('进程已退出 · 退出码 0', { exact: true }).waitFor();
    assert.equal(calls.filter(c => c.method === 'process/spawn').length, beforeReload);
    assert.ok(receiptReads > 0);
    assert.ok(!calls.some(c => c.method === 'process/resizePty' && c.params.processHandle === advanced.params.processHandle));
    assert.deepEqual(errors, []);
    console.log('Terminal recovery: stable process identity, output/stdin/stop, advanced env/cap/TTY settings, secret-free storage and receipt-only reload passed.');
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });

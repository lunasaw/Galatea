'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox', '--host-resolver-rules=MAP codex-copy.test 127.0.0.1', '--no-proxy-server'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      const clipboard = navigator.clipboard;
      window.readClipboard = () => clipboard.readText();
      window.seedClipboard = value => clipboard.writeText(value);
      window.copyMode = 'modern';
      const execCommand = document.execCommand.bind(document);
      document.execCommand = (...args) => window.copyMode === 'failed' ? false : execCommand(...args);
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        get: () => window.copyMode === 'modern' ? clipboard
          : window.copyMode === 'denied' ? { writeText: () => Promise.reject(new Error('Permission denied')) }
          : undefined,
      });
    });
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.locator('#text').fill('第一条回答');
    await page.locator('#send').click();
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    await page.locator('#text').fill('copy-fixture');
    await page.locator('#send').click();
    await page.getByText('最后一行：回答已完整复制。', { exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    const expected = '## 完整复制验收\n\n' + Array.from({ length: 160 }, (_, index) =>
      `第 ${index} 段：这段内容必须完整保留。**中文和标点**、\`代码\`、链接 [文档](https://example.test/docs)。`
    ).join('\n\n') + '\n\n```python\nprint("完整代码")\n```\n\n| 字段 | 内容 |\n| --- | --- |\n| 结果 | 全部保留 |\n\n最后一行：回答已完整复制。';
    const reply = page.locator('[data-slot="aui_assistant-message-root"]').filter({ has: page.getByRole('heading', { name: '完整复制验收', exact: true }) });
    const button = reply.getByRole('button', { name: '复制回复', exact: true });
    for (const mode of ['modern', 'unavailable', 'denied']) {
      await page.evaluate(async mode => { window.copyMode = mode; await window.seedClipboard('之前复制的内容'); }, mode);
      await page.locator('#text').fill('复制时保留草稿');
      await button.click();
      await page.waitForFunction(() => document.querySelector('[aria-label="复制回复"][data-copied="true"]'));
      assert.equal(await page.evaluate(() => window.readClipboard()), expected, `${mode}: full Markdown source including its last line`);
      assert.equal(await page.locator('#text').inputValue(), '复制时保留草稿');
      assert.equal(await page.locator('.clipboard-copy-source').count(), 0);
    }

    await page.evaluate(async () => { window.copyMode = 'failed'; await window.seedClipboard('未修改'); });
    await button.click();
    await reply.getByRole('status').getByText('复制失败，请重试', { exact: true }).waitFor();
    assert.equal(await button.getAttribute('data-copied'), null);
    assert.equal(await page.evaluate(() => window.readClipboard()), '未修改');
    await page.evaluate(() => { window.copyMode = 'unavailable'; });
    await button.click();
    assert.equal(await page.evaluate(() => window.readClipboard()), expected);
    await page.reload();
    await button.waitFor();
    await page.evaluate(() => { window.copyMode = 'unavailable'; });
    await button.click();
    assert.equal(await page.evaluate(() => window.readClipboard()), expected, 'replayed final answer remains complete');

    const insecure = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const httpUrl = new URL(process.env.CONSOLE_TEST_URL);
    httpUrl.hostname = 'codex-copy.test';
    await insecure.goto(httpUrl.href);
    assert.equal(await insecure.evaluate(() => isSecureContext || !!navigator.clipboard), false);
    await insecure.locator('#text').fill('copy-fixture');
    await insecure.locator('#send').click();
    await insecure.getByText('最后一行：回答已完整复制。', { exact: true }).waitFor();
    await insecure.waitForFunction(() => !document.querySelector('#interrupt'));
    const httpButton = insecure.locator('[data-slot="aui_assistant-message-root"]')
      .filter({ has: insecure.getByRole('heading', { name: '完整复制验收', exact: true }) })
      .getByRole('button', { name: '复制回复', exact: true });
    await httpButton.click();
    assert.equal(await httpButton.getAttribute('data-copied'), 'true');
    await page.bringToFront();
    assert.equal(await page.evaluate(() => window.readClipboard()), expected, 'actual insecure HTTP page replaces the system clipboard');
    await insecure.close();
    assert.deepEqual(errors, []);
    console.log('Copy reply: exact long Markdown, final line, streamed completion, previous clipboard replacement, absent/rejected API fallback, failure/retry, draft preservation, reload and actual insecure HTTP passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

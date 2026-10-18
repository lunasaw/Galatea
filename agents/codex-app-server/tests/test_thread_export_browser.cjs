'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error' && /Content Security Policy|Refused to|React error/.test(m.text())) errors.push(m.text()); });
    await page.addInitScript(() => {
      const clipboard = navigator.clipboard;
      window.readClipboard = () => clipboard.readText();
      window.copyMode = 'modern';
      const exec = document.execCommand.bind(document);
      document.execCommand = (...args) => window.copyMode === 'failed' ? false : exec(...args);
      Object.defineProperty(navigator, 'clipboard', { configurable: true, get: () => window.copyMode === 'modern' ? clipboard
        : window.copyMode === 'denied' ? { writeText: () => Promise.reject(new Error('Permission denied')) } : undefined });
    });
    const menu = async () => page.getByRole('button', { name: '会话操作', exact: true }).click();
    const submenu = async () => {
      await menu();
      await page.getByRole('menuitem', { name: '复制', exact: true }).click();
      await page.getByRole('menuitem', { name: '复制为 Markdown', exact: true }).waitFor();
      await page.waitForTimeout(150);
    };
    const copied = () => page.locator('.thread-export-status').filter({ hasText: /已复制/ }).waitFor();
    const shot = async name => {
      if (!process.env.CONSOLE_SCREENSHOTS) return;
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/${name}.png` });
    };
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.locator('#text').fill('copy-fixture');
    await page.locator('#send').click();
    await page.getByText('最后一行：回答已完整复制。', { exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    const sessionId = await page.locator('#session-title').getAttribute('title');
    await page.locator('#text').fill('导出时保留草稿');
    let markdown;
    for (const mode of ['modern', 'unavailable', 'denied']) {
      await page.evaluate(mode => { window.copyMode = mode; }, mode);
      await submenu();
      if (mode === 'modern') await shot('thread-copy-desktop');
      await page.getByRole('menuitem', { name: '复制为 Markdown', exact: true }).click();
      await copied();
      const text = await page.evaluate(() => window.readClipboard());
      assert.match(text, /## 用户\n\ncopy-fixture/);
      assert.match(text, /```python\nprint\("完整代码"\)\n```/);
      assert.match(text, /第 0 段/);
      assert.match(text, /第 159 段/);
      assert.match(text, /最后一行：回答已完整复制。/);
      assert.ok(!text.includes('PRIVATE_REASONING_FIXTURE'));
      if (markdown) assert.equal(text, markdown);
      markdown = text;
      assert.equal(await page.locator('#text').inputValue(), '导出时保留草稿');
    }
    await menu();
    const download = page.waitForEvent('download');
    await page.getByRole('menuitem', { name: '导出 Markdown', exact: true }).click();
    const file = await download;
    assert.equal(file.suggestedFilename(), 'copy-fixture.md');
    assert.equal(fs.readFileSync(await file.path(), 'utf8'), markdown);

    await page.evaluate(() => { window.copyMode = 'failed'; });
    await submenu();
    await page.getByRole('menuitem', { name: '复制工作目录', exact: true }).click();
    await page.locator('.thread-export-status[role="alert"]').waitFor();
    assert.equal(await page.evaluate(() => window.readClipboard()), markdown);
    await page.evaluate(() => { window.copyMode = 'modern'; });
    await submenu();
    await page.getByRole('menuitem', { name: '复制工作目录', exact: true }).click();
    await copied();
    assert.equal(await page.evaluate(() => window.readClipboard()), '/fixture');

    await submenu();
    await page.getByRole('menuitem', { name: '复制深度链接', exact: true }).click();
    await copied();
    const link = await page.evaluate(() => window.readClipboard());
    assert.equal(new URL(link).searchParams.get('session'), sessionId);
    await page.evaluate(() => localStorage.setItem('codex-agent.active', 'history'));
    const linked = await context.newPage();
    await linked.goto(link);
    await linked.getByText('最后一行：回答已完整复制。', { exact: true }).waitFor();
    assert.equal(await linked.locator('#session-title').getAttribute('title'), sessionId);
    await linked.goto(new URL('?session=missing-export-session', link).href);
    await linked.getByRole('alert').getByText('链接中的会话不存在或无法访问', { exact: true }).waitFor();
    assert.equal(await linked.locator('[data-slot="aui_user-message-root"]').count(), 0);
    await linked.close();

    await page.locator('[data-slot="aui_thread-list-item-trigger"][title="history"]').click();
    await page.getByText('history message 449', { exact: true }).waitFor();
    await page.locator('#text').fill('历史导出草稿');
    const count = await page.locator('[data-slot="aui_user-message-root"]').count();
    await submenu();
    await page.getByRole('menuitem', { name: '复制为 Markdown', exact: true }).click();
    await copied();
    const history = await page.evaluate(() => window.readClipboard());
    assert.deepEqual([...history.matchAll(/^history message (\d+)$/gm)].map(m => Number(m[1])), Array.from({ length: 450 }, (_, i) => i));
    assert.equal(await page.locator('[data-slot="aui_user-message-root"]').count(), count);
    assert.equal(await page.locator('#text').inputValue(), '历史导出草稿');

    // A failed older page must not emit a truncated download. Retrying succeeds.
    let downloads = 0;
    page.on('download', () => downloads++);
    await page.route('**/snapshot?limit=200&view=all&page=*', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"EXPORT_FIXTURE_FAILURE"}' }), { times: 1 });
    await menu();
    await page.getByRole('menuitem', { name: '导出 Markdown', exact: true }).click();
    await page.locator('.thread-export-status[role="alert"]').filter({ hasText: 'EXPORT_FIXTURE_FAILURE' }).waitFor();
    assert.equal(downloads, 0);
    await menu();
    const retry = page.waitForEvent('download');
    await page.getByRole('menuitem', { name: '导出 Markdown', exact: true }).click();
    assert.equal(fs.readFileSync(await (await retry).path(), 'utf8'), history);

    let release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/snapshot?limit=200&view=all', async route => { await gate; await route.continue(); }, { times: 1 });
    await submenu();
    await page.getByRole('menuitem', { name: '复制为 Markdown', exact: true }).click();
    await page.locator('.thread-export-status').getByText('正在读取完整会话…', { exact: true }).waitFor();
    await page.locator(`[data-slot="aui_thread-list-item-trigger"][title="${sessionId}"]`).click();
    await page.getByText('最后一行：回答已完整复制。', { exact: true }).waitFor();
    release();
    await copied();
    assert.equal(await page.evaluate(() => window.readClipboard()), history, 'switching chats keeps the original export target');

    // Export is unavailable until the initial history has loaded.
    let releaseHistory;
    const initialHistory = new Promise(resolve => { releaseHistory = resolve; });
    await page.route('**/snapshot', async route => { await initialHistory; await route.continue(); }, { times: 1 });
    await page.reload();
    await page.locator('[data-slot="aui_thread-history-skeleton"]').waitFor();
    await menu();
    assert.equal(await page.getByRole('menuitem', { name: '导出 Markdown', exact: true }).getAttribute('aria-disabled'), 'true');
    await page.keyboard.press('Escape');
    releaseHistory();
    await page.getByText('最后一行：回答已完整复制。', { exact: true }).waitFor();

    for (const [theme, width, height] of [['dark', 1440, 900], ['light', 390, 844]]) {
      if (await page.getByRole('button', { name: '关闭导出提示' }).count()) await page.getByRole('button', { name: '关闭导出提示' }).click();
      await page.getByRole('button', { name: theme === 'dark' ? '切换深色主题' : '切换浅色主题', exact: true }).click();
      await page.setViewportSize({ width, height });
      await menu();
      await page.getByRole('menuitem', { name: '复制', exact: true }).focus();
      await page.keyboard.press('ArrowRight');
      const sub = page.locator('.thread-copy-menu');
      await sub.waitFor();
      await page.waitForTimeout(150);
      const bounds = await sub.boundingBox();
      await shot(`thread-copy-${theme === 'dark' ? 'dark' : 'mobile'}`);
      assert(bounds.x >= 0 && bounds.x + bounds.width <= width, `submenu stays within viewport: ${JSON.stringify(bounds)}`);
      assert(bounds.y >= 0 && bounds.y + bounds.height <= height);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.getByRole('menuitem', { name: '复制工作目录', exact: true }).click();
      await copied();
      assert.equal(await page.evaluate(() => window.readClipboard()), '/fixture');
    }
    assert.deepEqual(errors, []);
    console.log('Thread export passed: full Markdown, clipboard fallback/failure, downloads, deep links, 450-message pagination, failed-page retry, session switching/loading, draft retention, keyboard, dark/mobile menus.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

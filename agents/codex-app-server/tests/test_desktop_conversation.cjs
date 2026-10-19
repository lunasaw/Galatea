'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1560, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    const errors = [], paths = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (request.url().endsWith('/api/native/call') && request.postDataJSON()?.method === 'fs/readFile') paths.push(request.postDataJSON().params.path);
    });
    const shot = async name => {
      if (!process.env.CONSOLE_SCREENSHOTS) return;
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.mouse.move(0, 0);
      await page.waitForTimeout(250);
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/${name}.png` });
    };
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.locator('#text').fill('desktop-ui-fixture');
    await page.locator('#send').click();
    await page.getByText('会话交互已调整。点击文件后可在右侧阅读，对话与草稿会保留。', { exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    await page.locator('#text').fill('保留草稿\n继续检查文件交互');
    await page.locator('.activity-trigger').first().click();
    const read = page.locator('.native-activity').filter({ hasText: 'synthetic read output' });
    await read.getByRole('button', { name: 'README.md', exact: true }).click();
    assert.equal(await read.getAttribute('open'), null, 'file shortcut must not expand the tool record');
    assert.equal(await page.getByRole('dialog').count(), 0, 'desktop file reading uses the side panel');
    const viewer = page.getByRole('region', { name: '文件预览', exact: true });
    await viewer.getByRole('heading', { name: '会话界面', exact: true }).waitFor();
    assert.equal(await viewer.locator('table').count(), 1);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.equal(await viewer.locator('img').count(), 0);
    await viewer.getByRole('button', { name: '源码', exact: true }).click();
    await viewer.locator('.workspace-file-source').waitFor();
    await viewer.getByRole('button', { name: '预览', exact: true }).click();
    await viewer.getByRole('button', { name: '阅读源码', exact: true }).click();
    await viewer.getByText('第 84–86 行', { exact: true }).waitFor();
    assert.deepEqual(await viewer.locator('[data-selected=true]').evaluateAll(nodes => nodes.map(node => Number(node.dataset.line))), [84, 85, 86]);
    const scrolled = await viewer.locator('.workspace-file-scroll').evaluate(el => el.scrollTop);
    assert(scrolled > 700, 'line link scrolls the file into view');
    const row = await viewer.locator('[data-line="84"]').boundingBox();
    const viewport = await viewer.locator('.workspace-file-scroll').boundingBox();
    assert(row.y >= viewport.y && row.y < viewport.y + viewport.height);
    assert.match(await viewer.locator('[data-line="84"]').innerText(), /source line 84/);
    assert.equal(await page.locator('#text').inputValue(), '保留草稿\n继续检查文件交互');
    await page.getByRole('button', { name: '定位源码', exact: true }).click();
    assert.equal(await page.getByRole('tab', { name: 'workbench.tsx', exact: true }).count(), 1, 'opening the same path reuses its tab');
    await viewer.getByRole('button', { name: '复制文件内容', exact: true }).click();
    assert.match(await page.evaluate(() => navigator.clipboard.readText()), /source line 150/);
    await shot('desktop-file-lines');

    const separator = page.getByRole('separator', { name: '调整任务面板宽度', exact: true });
    const before = await page.getByLabel('任务面板', { exact: true }).boundingBox();
    await separator.focus();
    await page.keyboard.press('ArrowLeft');
    const after = await page.getByLabel('任务面板', { exact: true }).boundingBox();
    assert.equal(Math.round(after.width - before.width), 32);
    await page.getByRole('tab', { name: 'README.md', exact: true }).click();
    await viewer.getByRole('heading', { name: '会话界面', exact: true }).waitFor();
    await shot('desktop-markdown');
    const beforeSwitch = paths.filter(path => path === '/fixture/src/workbench.tsx').length;
    await page.getByRole('tab', { name: 'workbench.tsx', exact: true }).click();
    assert.equal(await viewer.locator('.workspace-file-scroll').evaluate(el => el.scrollTop), scrolled, 'switching tabs preserves the reading position');
    assert.equal(paths.filter(path => path === '/fixture/src/workbench.tsx').length, beforeSwitch, 'switching tabs uses the already loaded file');
    await page.getByRole('button', { name: '相对路径', exact: true }).click();
    await viewer.getByText('第 12 行', { exact: true }).waitFor();
    assert.equal(paths.at(-1), '/fixture/src/workbench.tsx');
    await page.getByRole('button', { name: '中文文件', exact: true }).click();
    await viewer.getByText('fixture-file', { exact: true }).waitFor();
    assert.equal(paths.at(-1), '/fixture/docs/界面 说明.md');
    const external = page.getByRole('link', { name: '外部文档', exact: true });
    assert.equal(await external.getAttribute('target'), '_blank');

    let attempts = 0;
    await page.route('**/api/native/call', async route => {
      const call = route.request().postDataJSON();
      if (call.method !== 'fs/readFile') return route.continue();
      if (call.params.path === '/fixture/missing.md') {
        attempts++;
        return route.fulfill({ status: attempts === 1 ? 503 : 200, contentType: 'application/json', body: JSON.stringify(attempts === 1 ? { error: 'synthetic read failure' } : { dataBase64: Buffer.from('# 重试成功').toString('base64') }) });
      }
      if (call.params.path === '/fixture/slow.txt') {
        await new Promise(resolve => setTimeout(resolve, 700));
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ dataBase64: Buffer.from('stale response must stay in its file').toString('base64') }) });
      }
      return route.continue();
    });
    await page.getByRole('button', { name: '重试文件', exact: true }).click();
    await viewer.getByText('无法打开此文件', { exact: true }).waitFor();
    await viewer.getByRole('button', { name: '重试', exact: true }).click();
    await viewer.getByRole('heading', { name: '重试成功', exact: true }).waitFor();
    assert.equal(attempts, 2);
    await page.getByRole('button', { name: '慢速文件', exact: true }).click();
    await viewer.getByRole('status').waitFor();
    await page.getByRole('button', { name: '定位源码', exact: true }).click();
    await viewer.getByText('第 84–86 行', { exact: true }).waitFor();
    await page.waitForTimeout(850);
    assert.equal(await viewer.getByText('stale response must stay in its file', { exact: true }).count(), 0);
    assert.match(await viewer.locator('[data-line="84"]').innerText(), /source line 84/);
    await page.getByRole('button', { name: '关闭 workbench.tsx', exact: true }).click();
    assert.equal(await page.getByRole('tab', { name: 'workbench.tsx', exact: true }).count(), 0);
    await page.getByRole('button', { name: '定位源码', exact: true }).click();
    await viewer.getByText('第 84–86 行', { exact: true }).waitFor();
    await page.getByRole('button', { name: '切换深色主题', exact: true }).click();
    await shot('dark-file-lines');
    for (const [name, width, height] of [['tablet', 1024, 900], ['mobile', 390, 844]]) {
      await page.setViewportSize({ width, height });
      await page.getByRole('dialog').getByRole('region', { name: '文件预览', exact: true }).waitFor();
      await page.waitForTimeout(250);
      const close = await page.getByRole('button', { name: '关闭任务面板', exact: true }).boundingBox();
      assert(close.x >= 0 && close.x + close.width <= width, 'close control remains inside the viewport');
      const selected = await page.getByRole('tab', { name: 'workbench.tsx', exact: true }).boundingBox();
      const strip = await page.getByRole('tablist', { name: '任务面板标签', exact: true }).boundingBox();
      assert(selected.x >= strip.x && selected.x + selected.width <= strip.x + strip.width + 1, 'selected file tab stays visible after viewport changes');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await shot(`${name}-file-lines`);
    }
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#text').inputValue(), '保留草稿\n继续检查文件交互');
    await page.setViewportSize({ width: 1560, height: 1000 });
    await page.getByRole('button', { name: '定位源码', exact: true }).click();
    await viewer.getByText('第 84–86 行', { exact: true }).waitFor();
    // Switching chats must never expose another chat's open file state.
    await page.locator('[data-slot=aui_thread-list-new]').click();
    await page.getByRole('heading', { name: '今天想构建什么？' }).waitFor();
    assert.equal(await page.getByRole('region', { name: '文件预览', exact: true }).count(), 0);
    assert.deepEqual(errors, []);
    console.log('Desktop conversation: structured read links, side panel, deduplicated tabs, source/range navigation, Markdown, clipboard, resize, relative/Unicode links, retry, stale reads, chat isolation, draft preservation, dark/tablet/mobile passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

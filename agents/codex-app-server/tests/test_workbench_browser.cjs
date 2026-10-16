'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const shot = async name => {
      if (!process.env.CONSOLE_SCREENSHOTS) return;
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.mouse.move(0, 0);
      await page.waitForTimeout(250);
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/${name}.png` });
    };
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.getByRole('heading', { name: '今天想构建什么？' }).waitFor();
    assert.equal(await page.locator('.chat-header').evaluate(el => el.getBoundingClientRect().height), 46);
    await shot('welcome');
    await page.locator('#text').fill('parity-fixture');
    await page.locator('#send').click();
    await page.getByText('验收完成。', { exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    await page.locator('#text').fill('保留这段草稿');
    await page.getByRole('button', { name: '查看更改', exact: true }).click();
    await page.locator('.review-diff').getByText('+new', { exact: true }).waitFor();
    assert.equal(await page.getByRole('dialog').count(), 0);
    assert.equal(await page.locator('.diff-count').innerText(), '+1\n−1');
    await page.getByRole('button', { name: '自动换行', exact: true }).click();
    assert.equal(await page.locator('.review-files').getAttribute('data-wrap'), 'false');
    await shot('desktop-review');
    await page.getByRole('button', { name: '查看摘要', exact: true }).click();
    await page.locator('.summary-plan').getByText('读取配置', { exact: true }).waitFor();
    await page.getByRole('button', { name: '关闭任务面板', exact: true }).click();
    await page.getByRole('button', { name: '切换任务面板', exact: true }).click();
    await page.locator('.workbench-launcher').getByRole('button', { name: /更改/ }).click();
    await page.locator('.review-diff').waitFor();
    assert.equal(await page.locator('#text').inputValue(), '保留这段草稿');

    // Two turns must remain independently selectable after a live update.
    await page.locator('#text').fill('parity-fixture');
    await page.locator('#send').click();
    await page.waitForFunction(() => document.querySelectorAll('#review-turn option').length === 2);
    const options = await page.locator('#review-turn option').evaluateAll(nodes => nodes.map(node => node.value));
    await page.locator('#review-turn').selectOption(options[0]);
    assert.equal(await page.locator('#review-turn').inputValue(), options[0]);
    assert.equal(await page.locator('.review-file').count(), 1);

    // Synthetic directory contents exercise the browser's native RPC boundary.
    await page.route('**/api/native/call', async route => {
      const body = route.request().postDataJSON();
      if (body.method !== 'fs/readDirectory') return route.continue();
      const entries = body.params.path === '/fixture/src'
        ? [{ fileName: 'index.ts', isDirectory: false, isFile: true }]
        : [{ fileName: 'README.md', isDirectory: false, isFile: true }, { fileName: 'src', isDirectory: true, isFile: false }];
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ entries }) });
    });
    await page.getByRole('button', { name: '打开文件', exact: true }).click();
    await page.locator('.file-browser-list').getByRole('button', { name: 'src', exact: true }).click();
    await page.locator('.file-browser-list').getByRole('button', { name: 'index.ts', exact: true }).click();
    await page.getByLabel('文件预览', {exact:true}).getByText('fixture-file', { exact: true }).waitFor();
    assert.equal(await page.getByRole('dialog').count(),0);
    await page.getByRole('tab',{name:'文件',exact:true}).click();
    await page.getByRole('button', { name: '上一级目录', exact: true }).click();
    await page.locator('.file-browser-list').getByText('README.md', { exact: true }).waitFor();

    await page.getByRole('button', { name: '打开终端', exact: true }).click();
    await page.getByLabel('底部终端').getByLabel('Shell 命令').fill('printf fixture-command-output');
    await page.getByLabel('底部终端').getByRole('button', { name: '执行命令', exact: true }).click();
    await page.getByLabel('底部终端').locator('.native-terminal').getByText('fixture-command-output', { exact: true }).waitFor();
    await shot('terminal');
    await page.getByRole('button', { name: '关闭终端', exact: true }).click();
    await page.getByRole('button', { name: '打开终端', exact: true }).click();
    assert.equal(await page.getByLabel('底部终端').getByLabel('Shell 命令').inputValue(), 'printf fixture-command-output');
    await page.getByRole('button', { name: '关闭终端', exact: true }).click();
    await page.getByRole('button', { name: '查看更改', exact: true }).click();
    await page.getByRole('button', { name: '切换深色主题', exact: true }).click();
    await shot('dark-review');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('dialog').locator('.review-diff').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await shot('mobile-review');
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    await shot('mobile-chat');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    console.log('Workbench: panel toggles, draft preservation, turn selection, diff, plan, directory navigation, file preview, terminal execution/preservation, dark theme and mobile passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

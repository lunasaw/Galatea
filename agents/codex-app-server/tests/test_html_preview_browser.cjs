'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1560, height: 1000 } });
    const errors = [], forbidden = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', request => {
      if (request.frame() !== page.mainFrame() && request.url().includes('/api/sessions')) forbidden.push(request.url());
    });
    await page.goto(process.env.CONSOLE_TEST_URL || 'http://127.0.0.1:18977');
    await page.locator('#text').fill('html-preview-fixture');
    await page.locator('#send').click();
    await page.getByRole('button', { name: '交互讲解图', exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    await page.locator('#text').fill('保留聊天草稿');
    await page.getByRole('button', { name: '交互讲解图', exact: true }).click();
    const viewer = page.getByRole('region', { name: '文件预览', exact: true });
    const frame = viewer.frameLocator('iframe');
    await frame.getByRole('heading', { name: '交互讲解页面' }).waitFor();
    await frame.getByRole('button', { name: '点击 0', exact: true }).click();
    await frame.getByRole('button', { name: '点击 1', exact: true }).waitFor();
    await frame.getByText('父页面已隔离', { exact: true }).waitFor();
    await frame.getByText('网络请求已隔离', { exact: true }).waitFor();
    assert.equal(await frame.locator('body').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(240, 245, 250)');
    assert.equal(await page.evaluate(() => document.body.dataset.previewEscaped), undefined);
    assert.deepEqual(forbidden, []);
    assert.equal(await page.locator('#text').inputValue(), '保留聊天草稿');
    await viewer.getByRole('button', { name: '源码', exact: true }).click();
    await viewer.locator('.workspace-file-source').waitFor();
    assert.match(await viewer.locator('.workspace-file-source').textContent(), /<script type="module">/);
    await viewer.getByRole('button', { name: '预览', exact: true }).click();
    await frame.getByRole('button', { name: '点击 0', exact: true }).waitFor();
    await frame.getByRole('button', { name: '点击 0', exact: true }).click();
    await viewer.getByRole('button', { name: '重新读取文件', exact: true }).click();
    await frame.getByRole('button', { name: '点击 0', exact: true }).waitFor();
    await page.getByRole('button', { name: 'HTM 页面', exact: true }).click();
    await frame.getByRole('heading', { name: '交互讲解页面' }).waitFor();
    await page.getByRole('button', { name: '查看第 3 行', exact: true }).click();
    await viewer.locator('[data-line="3"][data-selected="true"]').waitFor();
    await viewer.getByRole('button', { name: '预览', exact: true }).click();
    await frame.getByRole('heading', { name: '交互讲解页面' }).waitFor();
    if (process.env.CONSOLE_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/html-preview-desktop.png` });
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await viewer.getByRole('button', { name: '预览', exact: true }).click();
    await frame.getByText('网络请求已隔离', { exact: true }).waitFor();
    await frame.getByRole('button', { name: '点击 0', exact: true }).click();
    await frame.getByRole('button', { name: '点击 1', exact: true }).waitFor();
    assert.deepEqual(errors, []);
    console.log('HTML preview passed: inline styling/scripts, source/preview, refresh, HTM, line links, draft retention, mobile, and isolation.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

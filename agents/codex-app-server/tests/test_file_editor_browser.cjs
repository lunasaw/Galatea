'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage(); const errors = []; const calls = []; let disk = '\uFEFF原始文本\r\n';
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/api/native/call', route => {
      const { method, params } = route.request().postDataJSON(); calls.push({ method, params });
      if (method === 'fs/readFile' && params.path === '/fixture/file.txt') return route.fulfill({ json: { dataBase64: Buffer.from(disk).toString('base64') } });
      if (method === 'fs/writeFile' && params.path === '/fixture/file.txt') { disk = Buffer.from(params.dataBase64, 'base64').toString(); return route.fulfill({ json: {} }); }
      return route.continue();
    });
    await page.goto(process.env.CONSOLE_TEST_URL);
    const open = async () => {
      await page.getByRole('button', { name: '打开文件', exact: true }).click();
      await page.getByRole('button', { name: '管理当前目录', exact: true }).click();
      await page.getByLabel('文件或目录的绝对路径', { exact: true }).fill('/fixture/file.txt');
      await page.getByRole('button', { name: '打开路径', exact: true }).click();
    };
    await open();
    const editor = page.getByLabel('文件内容', { exact: true }); await editor.waitFor();
    assert.equal(await editor.inputValue(), '\uFEFF原始文本\n');
    await editor.fill('浏览器草稿'); disk = '外部编辑';
    await page.getByRole('button', { name: '保存文件', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: '文件已在外部修改' }).waitFor();
    assert.equal(calls.filter(c => c.method === 'fs/writeFile').length, 0);
    assert.equal(await editor.inputValue(), '浏览器草稿');
    await page.getByRole('button', { name: '关闭任务面板', exact: true }).click();
    await open(); assert.equal(await editor.inputValue(), '浏览器草稿');
    await page.getByRole('button', { name: '放弃编辑', exact: true }).click();
    await page.getByRole('button', { name: '打开路径', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('textarea.native-editor')?.value === '外部编辑');
    await editor.fill('保存后的文本');
    await page.getByRole('button', { name: '保存文件', exact: true }).click();
    await page.getByText('文件已保存', { exact: true }).waitFor(); assert.equal(disk, '保存后的文本');
    await page.getByLabel('复制到绝对路径', { exact: true }).fill('/fixture/copy.txt');
    await page.getByRole('button', { name: '复制文件', exact: true }).click();
    await page.getByText('复制完成', { exact: true }).waitFor();
    await page.getByLabel('监听文件变化', { exact: false }).check();
    await page.getByText('监听文件变化 · 已连接', { exact: true }).waitFor();
    await page.getByRole('button', { name: '删除此文件…', exact: true }).click();
    assert.equal(calls.filter(c => c.method === 'fs/remove').length, 0);
    await page.getByRole('button', { name: '确认删除', exact: true }).click(); await page.getByText('已删除', { exact: true }).waitFor();
    assert.ok(calls.some(c => c.method === 'fs/copy' && !c.params.recursive));
    assert.ok(calls.some(c => c.method === 'fs/unwatch'));
    assert.deepEqual(errors, []);
    console.log('Files: BOM/UTF-8, external edit conflict, retained draft, save, copy, watch cleanup and explicit delete passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

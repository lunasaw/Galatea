'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext(); const page = await context.newPage(); const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const call = async (method, params) => {
      const r = await context.request.post(process.env.CONSOLE_TEST_URL + '/api/native/call', {
        headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { method, params },
      }); assert.ok(r.ok(), await r.text()); return r.json();
    };
    const name = '多目录验收-' + Date.now();
    const { project } = await call('project/create', { idempotencyKey: crypto.randomUUID(), name, roots: [{ path: '/fixture/one' }], metadata: { foreign: 'retain-me' } });
    await page.goto(process.env.CONSOLE_TEST_URL);
    const edit = async () => {
      await page.getByRole('button', { name: name + ' 项目菜单', exact: true }).click();
      await page.getByRole('menuitem', { name: '编辑项目', exact: true }).click();
    };
    await edit(); const dialog = page.getByRole('dialog', { name: '编辑项目', exact: true });
    await dialog.getByRole('button', { name: '添加工作目录', exact: true }).click();
    await dialog.getByLabel('工作目录 2', { exact: true }).fill('/fixture/two');
    await dialog.getByRole('button', { name: '上移目录 2', exact: true }).click();
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    let saved = (await call('project/read', { projectId: project.id })).project;
    assert.deepEqual(saved.roots, [{ path: '/fixture/two' }, { path: '/fixture/one' }]);
    assert.equal(saved.metadata.foreign, 'retain-me');
    await page.reload(); await edit();
    assert.equal(await dialog.getByLabel('工作目录 1', { exact: true }).inputValue(), '/fixture/two');
    await dialog.getByRole('button', { name: '删除目录 2', exact: true }).click();
    await dialog.getByRole('button', { name: '保存', exact: true }).click(); await dialog.waitFor({ state: 'hidden' });
    saved = (await call('project/read', { projectId: project.id })).project;
    assert.deepEqual(saved.roots, [{ path: '/fixture/two' }]);
    await page.getByRole('button', { name: '在 ' + name + ' 中新建会话', exact: true }).click();
    await page.locator('#text').fill('验证第一个目录');
    const created = page.waitForRequest(r => r.url().endsWith('/api/sessions') && r.method() === 'POST');
    await page.locator('#send').click(); const body = (await created).postDataJSON();
    assert.equal(body.options.cwd, '/fixture/two'); assert.equal(body.options.projectId, project.id);
    assert.deepEqual(errors, []);
    console.log('Project roots: add, reorder, remove, reload, metadata preservation and default new-chat directory passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

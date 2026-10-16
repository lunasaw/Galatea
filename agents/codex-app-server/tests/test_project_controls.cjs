'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 980 } });
    const errors = [], calls = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.method() === 'POST') calls.push({ url: request.url(), body: request.postDataJSON() }); });
    const url = process.env.CONSOLE_TEST_URL || 'http://127.0.0.1:18883';
    await page.goto(url);
    await page.getByRole('button', { name: '选择项目', exact: true }).waitFor();
    await page.waitForFunction(() => document.body.textContent.includes('fixture-model'));
    await page.locator('#text').fill('保留我的草稿');
    const permission = page.getByRole('button', { name: '切换权限', exact: true });
    await permission.click();
    await page.getByRole('menuitem', { name: /帮我批准/ }).click();
    assert.match(await permission.innerText(), /帮我批准/);
    assert.equal(await page.getByRole('dialog').count(), 0);
    assert.equal(await page.locator('#text').inputValue(), '保留我的草稿');
    await permission.click();
    await page.getByRole('menuitem', { name: /完全访问权限/ }).click();
    assert.match(await permission.innerText(), /完全访问/);
    await page.reload();
    await page.waitForFunction(() => document.body.textContent.includes('fixture-model'));
    assert.match(await permission.innerText(), /完全访问/);
    // Add an existing server folder, then create a directory through the native fs API.
    await page.getByRole('button', { name: '添加项目到项目', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: '添加此项目', exact: true }).waitFor();
    await dialog.getByLabel('项目目录').fill('/fixture/alpha');
    await dialog.getByRole('button', { name: '添加此项目', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    const alpha = page.locator('[data-project-path="/fixture/alpha"]');
    await alpha.waitFor();
    await page.getByRole('button', { name: '添加项目到项目', exact: true }).click();
    await dialog.getByRole('button', { name: '新建文件夹', exact: true }).click();
    await dialog.getByLabel('项目目录').fill('/fixture');
    await dialog.getByLabel('文件夹名称').fill('beta');
    await dialog.getByRole('button', { name: '创建并添加', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    const beta = page.locator('[data-project-path="/fixture/beta"]');
    await beta.waitFor();
    assert(calls.some(c => c.body?.method === 'fs/createDirectory' && c.body.params.path === '/fixture/beta' && c.body.params.recursive === false));
    // Create a sidebar section and drag a project into it with real browser drag events.
    await page.getByRole('button', { name: '项目分组菜单', exact: true }).click();
    await page.getByRole('menuitem', { name: '创建分组', exact: true }).click();
    await dialog.getByLabel('名称', { exact: true }).fill('工作');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    const work = page.locator('[data-project-section]').filter({ has: page.getByRole('button', { name: '工作', exact: true }) });
    await beta.locator('.sidebar-project-row').dragTo(work.locator('.sidebar-section-heading'));
    assert.equal(await work.locator('[data-project-path="/fixture/beta"]').count(), 1);
    await alpha.locator('.sidebar-project-row').dragTo(work.locator('.sidebar-section-heading'));
    await alpha.locator('.sidebar-project-row').dragTo(beta.locator('.sidebar-project-row'));
    assert.deepEqual(await work.locator('[data-project-path]').evaluateAll(nodes => nodes.map(n => n.dataset.projectPath)), ['/fixture/alpha', '/fixture/beta']);
    await page.reload();
    await work.locator('[data-project-path="/fixture/alpha"]').waitFor();
    assert.deepEqual(await work.locator('[data-project-path]').evaluateAll(nodes => nodes.map(n => n.dataset.projectPath)), ['/fixture/alpha', '/fixture/beta']);
    // Select a project without opening settings; new thread must use it and the permission choice.
    await page.getByRole('button', { name: '选择项目', exact: true }).click();
    await page.getByRole('menuitem', { name: /alpha/ }).click();
    await page.locator('#text').fill('project-controls-fixture');
    await page.locator('#send').click();
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    const creation = calls.find(c => c.url.endsWith('/api/sessions') && c.body?.options?.cwd === '/fixture/alpha');
    assert(creation);
    assert.equal(creation.body.options.sandbox, 'danger-full-access');
    assert.equal(creation.body.options.approvalPolicy, 'never');
    await page.locator('#text').fill('existing chat draft');
    await permission.click();
    await page.getByRole('menuitem', { name: /^请求批准/ }).click();
    await page.waitForFunction(() => document.querySelector('[aria-label="切换权限"]').textContent.includes('请求批准'));
    assert(calls.some(c => c.body?.method === 'thread/settings/update' && c.body.params.sandboxPolicy?.type === 'workspaceWrite' && c.body.params.approvalsReviewer === 'user'));
    assert.equal(await page.locator('#text').inputValue(), 'existing chat draft');
    assert.equal(await page.getByRole('dialog').count(), 0);
    // A rejected permission update must retain the existing choice.
    await page.route('**/api/native/call', async route => {
      const body = route.request().postDataJSON();
      if (body.method === 'thread/settings/update' && body.params.sandboxPolicy?.type === 'dangerFullAccess') return route.fulfill({ status: 400, json: { error: 'policy-rejected-fixture' } });
      await route.continue();
    });
    await permission.click();
    await page.getByRole('menuitem', { name: /完全访问权限/ }).click();
    await page.getByText(/policy-rejected-fixture/).first().waitFor();
    assert.match(await permission.innerText(), /请求批准/);
    await page.unroute('**/api/native/call');
    // Move a thread to another project; this updates the real thread cwd.
    await alpha.locator('.project-thread-row').first().dragTo(beta.locator('.sidebar-project-row'));
    await beta.locator('.project-thread-row').first().waitFor();
    assert(calls.some(c => c.body?.method === 'thread/settings/update' && c.body.params.cwd === '/fixture/beta'));
    assert.match(await page.getByRole('button', { name: '选择项目', exact: true }).innerText(), /beta/);
    await page.locator('#text').fill('/permissions');
    await page.locator('#text').press('Enter');
    await page.getByRole('menuitem', { name: /^请求批准/ }).waitFor();
    assert.equal(await page.getByRole('dialog').count(), 0);
    if (process.env.CONSOLE_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/projects-permissions.png` });
    }
    await page.keyboard.press('Escape');
    // Attach text through the + control and verify it is part of the real request.
    await page.getByRole('button', { name: '新建会话', exact: true }).click();
    const chooserEvent = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: '添加图片或文本文件', exact: true }).click();
    const chooser = await chooserEvent;
    await chooser.setFiles({ name: 'notes.md', mimeType: 'text/markdown', buffer: Buffer.from('attachment-content-fixture') });
    await page.locator('#text').fill('请阅读附件');
    await page.locator('#send').click();
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    assert(calls.some(c => c.url.endsWith('/messages') && c.body.text.includes('attachment-content-fixture')));
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    await page.setViewportSize({ width: 390, height: 844 });
    await permission.click();
    await page.getByRole('menuitem', { name: /^请求批准/ }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const box = await page.getByRole('menu').boundingBox();
    assert(box.x >= 0 && box.x + box.width <= 390);
    assert.deepEqual(errors, []);
    console.log('Projects: existing/new folders, groups, drag/reorder, reload, native cwd, direct permissions, rejected updates, slash menu, attachments, mobile passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

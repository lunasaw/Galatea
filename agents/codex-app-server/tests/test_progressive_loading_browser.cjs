'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

const gate = () => {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
};

(async () => {
  const base = process.env.CONSOLE_TEST_URL || 'http://127.0.0.1:18889';
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const post = async (path, data) => {
      const response = await context.request.post(base + path, {
        headers: { 'Idempotency-Key': crypto.randomUUID(), Origin: base }, data,
      });
      assert.equal(response.ok(), true, await response.text());
      return response.json();
    };
    const first = await post('/api/sessions', { options: { cwd: '/fixture' } });
    const second = await post('/api/sessions', { options: { cwd: '/fixture' } });
    const { project } = await post('/api/native/call', { method: 'project/create', params: {
      idempotencyKey: crypto.randomUUID(), name: '渐进加载项目', roots: [{ path: '/fixture' }],
    } });
    await post('/api/native/call', { method: 'thread/metadata/update', params: { threadId: first.id, projectId: project.id } });
    const { section } = await post('/api/native/call', { method: 'threadSection/create', params: { name: '渐进加载分组' } });
    await post('/api/native/call', { method: 'thread/section/move', params: { threadId: second.id, sectionId: section.id } });
    await post(`/api/sessions/${first.id}/messages`, { text: 'progressive-first' });
    await post(`/api/sessions/${second.id}/messages`, { text: 'progressive-second' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(id => localStorage.setItem('codex-agent.active', id), first.id);
    const optional = gate(), history = gate(), projects = gate();
    const waiting = new Set();
    let slowMetadata = null, slowHistory = null;
    await page.route('**/api/**', async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (['/api/console/status', '/api/capabilities', '/api/native/protocol', '/api/native/requests'].includes(path)) {
        waiting.add(path); await optional.promise;
      }
      if (path === `/api/sessions/${first.id}/snapshot`) await history.promise;
      if (path === '/api/native/call' && request.postDataJSON().method === 'project/list') {
        waiting.add('project/list'); await projects.promise;
      }
      if (slowMetadata && (path === '/api/sessions' || path === '/api/console/status')) await slowMetadata.promise;
      if (slowHistory && path === `/api/sessions/${first.id}/snapshot`) await slowHistory.promise;
      await route.continue().catch(() => {}); // A switched-away request may be aborted.
    });
    await page.goto(base);
    const row = id => page.locator(`[data-slot="aui_thread-list-item-trigger"][title="${id}"]`);
    await row(first.id).waitFor();
    await row(second.id).waitFor();
    await page.waitForFunction(id => document.querySelector('#session-title')?.title === id, first.id);
    await page.locator('[data-slot="aui_thread-history-skeleton"]').waitFor();
    assert(waiting.has('/api/native/protocol'));
    assert(waiting.has('/api/console/status'));

    history.release();
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'progressive-first' }).waitFor();
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    assert.equal(await page.locator('[data-slot="aui_thread-history-skeleton"]').count(), 0);
    assert.equal(await page.locator('#health').textContent(), '连接中');
    if (process.env.CONSOLE_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/progressive-chat-before-metadata.png` });
    }

    optional.release();
    await page.waitForFunction(() => document.querySelector('#health')?.textContent === '已连接');
    // A pending project catalog must not stop a second conversation opening.
    slowMetadata = gate();
    await row(second.id).click();
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'progressive-second' }).waitFor();
    assert.equal(await page.locator('#text').isDisabled(), true);
    slowMetadata.release(); slowMetadata = null;
    await page.waitForFunction(() => !document.querySelector('#text')?.disabled);
    await page.locator('#text').fill('保留输入草稿');
    assert(waiting.has('project/list'));
    projects.release();
    await page.waitForFunction(() => Object.keys(localStorage).some(key => key.startsWith('codex-agent.projects-native:')));
    await page.locator(`[data-project-id="${project.id}"]`).waitFor();
    await row(first.id).waitFor();
    await row(second.id).waitFor();
    assert.equal(await page.locator('#text').inputValue(), '保留输入草稿');

    // A late history response from the first chat cannot overwrite the second.
    slowHistory = gate();
    await row(first.id).click();
    await page.locator('[data-slot="aui_thread-history-skeleton"]').waitFor();
    await row(second.id).click();
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'progressive-second' }).waitFor();
    slowHistory.release(); slowHistory = null;
    await page.waitForFunction(() => !document.querySelector('#text')?.disabled);
    assert.equal(await page.locator('#session-title').getAttribute('title'), second.id);
    assert.equal(await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'progressive-first' }).count(), 0);
    assert.deepEqual(errors, []);
    console.log('Progressive loading passed: sidebar before history, chat before metadata/projects, guarded composer, draft retention, and rapid switching.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const errors = [];
    // The Apps directory must launch an interactive App from a fresh browser
    // without making the user select a chat; the sidebar selects its backing session.
    const standalone = await browser.newPage({ viewport: { width: 1582, height: 1022 } });
    const created = []; const standaloneCalls = [];
    standalone.on('pageerror', error => errors.push(error.message));
    standalone.on('response', async response => {
      if (response.url().endsWith('/api/sessions') && response.request().method() === 'POST')
        created.push(await response.json());
    });
    standalone.on('request', request => {
      if (request.url().endsWith('/api/native/call')) standaloneCalls.push(request.postDataJSON());
    });
    await standalone.goto(process.env.CONSOLE_TEST_URL);
    await standalone.locator('#text').fill('/apps'); await standalone.locator('#text').press('Enter');
    await standalone.getByRole('tabpanel', {name: 'Apps面板', exact: true}).waitFor();
    const standaloneCard = standalone.locator('[data-mcp-app="fixture-app:lookup:ui://fixture/app"]');
    await standaloneCard.getByRole('button', {name: '打开应用', exact: true}).click();
    await standaloneCard.frameLocator('iframe').locator('#status').getByText('native app tool result', {exact: true}).waitFor();
    assert.equal(created.length, 1, 'opening an App creates exactly one backing session');
    const firstLaunch = standaloneCalls.find(c => c.method === 'mcpServer/tool/call');
    assert.equal(firstLaunch.params.threadId, created[0].thread_id);
    assert.equal(await standalone.evaluate(() => localStorage.getItem('codex-agent.active')), created[0].id, 'the sidebar selects its backing chat');
    assert.equal(new URL(standalone.url()).searchParams.get('session'), created[0].id);
    assert.equal(await standaloneCard.locator('iframe').getAttribute('src'), '/api/mcp-app?' + new URLSearchParams({threadId: firstLaunch.params.threadId, server: 'fixture-app', uri: 'ui://fixture/app'}));
    const previousStandaloneFrame = await standaloneCard.locator('iframe').elementHandle();
    await standalone.getByRole('button', {name: '返回应用列表', exact: true}).click();
    await standaloneCard.getByRole('button', {name: '打开应用', exact: true}).click();
    await standalone.waitForFunction(previous => !previous.isConnected, previousStandaloneFrame);
    await standaloneCard.frameLocator('iframe').locator('#status').getByText('native app tool result', {exact: true}).waitFor();
    assert.equal(created.length, 1, 'reopening reuses the backing session');

    let staleThread; let rejected = false;
    await standalone.route('**/api/native/call', async route => {
      const body = route.request().postDataJSON();
      if (body.method === 'mcpServer/tool/call' && !rejected) {
        rejected = true; staleThread = body.params.threadId;
        return route.fulfill({status: 422, contentType: 'application/json', body: JSON.stringify({error: 'thread not found: ' + staleThread})});
      }
      return route.continue();
    });
    await standalone.getByRole('button', {name: '返回应用列表', exact: true}).click();
    await standaloneCard.getByRole('button', {name: '打开应用', exact: true}).click();
    await standalone.waitForFunction(previous => {
      const source = document.querySelector('[data-mcp-app] iframe')?.getAttribute('src');
      return source && new URL(source, location.href).searchParams.get('threadId') !== previous;
    }, firstLaunch.params.threadId);
    await standaloneCard.frameLocator('iframe').locator('#status').getByText('native app tool result', {exact: true}).waitFor();
    assert.equal(created.length, 2, 'a missing thread is replaced automatically');
    assert.equal(staleThread, firstLaunch.params.threadId);
    assert.equal(standaloneCalls.filter(c => c.method === 'mcpServer/tool/call').at(-1).params.threadId, created[1].thread_id);
    assert.equal(await standalone.evaluate(() => localStorage.getItem('codex-agent.active')), created[0].id);
    await standalone.close();
    assert.deepEqual(errors, []);
    console.log('Apps launch without a selected chat, backing session reuse and missing-thread recovery passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

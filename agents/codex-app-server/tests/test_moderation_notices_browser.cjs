'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [], observed = [];
    let legacySnapshots = 0;
    page.on('pageerror', error => errors.push(error.message));
    // Older running Hosts already contain warning projections of the same events.
    await page.route(/\/api\/sessions\/[^/]+\/snapshot(?:\?|$)/, async route => {
      const response = await route.fetch(), data = await response.json();
      data.steps = [...(data.steps || []), ...Array.from({ length: 5 }, (_, index) => ({
        id: `legacy-moderation-${index}`, type: 'warning', first_seq: '1', last_seq: '1',
        detail: { method: 'turn/moderationMetadata', metadata: { marker: 'legacy-moderation-data' } },
      }))];
      legacySnapshots++;
      return route.fulfill({ response, json: data });
    });
    await page.goto(process.env.CONSOLE_TEST_URL);
    // Observe the actual SSE transport, so the absence of banners cannot pass
    // merely because the test never received the metadata notifications.
    await page.exposeFunction('recordModerationEvent', event => observed.push(event));
    await page.evaluate(() => {
      const consume = window.ConsoleTransport.consumeSSE;
      window.ConsoleTransport.consumeSSE = (body, receive, ...args) => consume(body, frame => {
        if (frame.event === 'observation') {
          const data = JSON.parse(frame.data);
          if (data.method === 'turn/moderationMetadata') void window.recordModerationEvent(data);
        }
        return receive(frame);
      }, ...args);
    });
    await page.locator('#text').fill('开始提示回归测试');
    await page.locator('#send').click();
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    await page.locator('#text').fill('moderation-notice-fixture');
    const sent = page.waitForRequest(request => /\/api\/sessions\/[^/]+\/messages$/.test(request.url()));
    await page.locator('#send').click();
    const sessionPath = new URL((await sent).url()).pathname.replace(/\/messages$/, '');
    await page.getByText('你好，完整回复。', { exact: true }).nth(1).waitFor();
    const notices = page.locator('.native-notice-center');
    await notices.getByText('自动审查警告', { exact: true }).waitFor();
    assert.equal(await notices.locator('summary').count(), 1, 'metadata must not crowd out actionable warnings');
    assert.equal(await page.getByText('内容审查状态', { exact: true }).count(), 0);
    assert.equal(await page.locator('.native-activity').filter({ hasText: 'turn/moderationMetadata' }).count(), 0);
    assert.ok(legacySnapshots > 0, 'loaded history includes legacy moderation warning projections');
    assert.equal(await page.getByText('legacy-moderation-data', { exact: true }).count(), 0);
    assert.equal(observed.length, 8, 'all metadata notifications reached the live event stream');
    await notices.locator('summary').click();
    await notices.getByText('fixture approval warning', { exact: true }).waitFor();
    await notices.getByRole('button', { name: '关闭提示', exact: true }).click();
    assert.equal(await notices.locator('summary').count(), 0);
    const historyUrl = new URL(`${sessionPath}/history?method=turn%2FmoderationMetadata&limit=100`, page.url());
    const history = await (await page.request.get(historyUrl.href)).json();
    assert.equal(history.events.length, 8, 'raw diagnostic events remain available');
    assert.deepEqual(history.events.map(event => event.payload.params.metadata.sequence), [0, 1, 2, 3, 4, 5, 6, 7]);
    await page.locator('#text').fill('保留聊天草稿');
    assert.equal(await page.locator('#text').inputValue(), '保留聊天草稿');
    const snapshotsBeforeReload = legacySnapshots;
    await page.reload();
    await page.getByText('你好，完整回复。', { exact: true }).nth(1).waitFor();
    assert.equal(await page.getByText('内容审查状态', { exact: true }).count(), 0);
    assert.equal(await page.locator('.native-activity').filter({ hasText: 'turn/moderationMetadata' }).count(), 0);
    assert.ok(legacySnapshots > snapshotsBeforeReload, 'refresh also loads legacy warning projections');
    if (process.env.CONSOLE_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/moderation-notices-fixed.png` });
    }
    assert.deepEqual(errors, []);
    console.log('Moderation notices: live metadata and legacy history stay out of chat; actionable warnings and raw diagnostics remain available.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

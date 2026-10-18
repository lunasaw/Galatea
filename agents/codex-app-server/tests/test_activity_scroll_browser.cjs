'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const viewport = page.locator('[data-slot="aui_thread-viewport"]');
    const rows = page.locator('.native-activity').filter({ hasText: 'scroll-row-' });
    const measure = () => page.evaluate(() => {
      const viewport = document.querySelector('[data-slot="aui_thread-viewport"]');
      const footer = document.querySelector('.aui-thread-viewport-footer');
      const input = document.querySelector('#text');
      const rect = input.getBoundingClientRect();
      return {
        top: viewport.scrollTop,
        bottomGap: viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop,
        viewportBottom: viewport.getBoundingClientRect().bottom,
        footerTop: footer.getBoundingClientRect().top,
        footerBottom: footer.getBoundingClientRect().bottom,
        inputReachable: document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2) === input,
        pageOverflow: document.documentElement.scrollHeight > innerHeight,
      };
    });
    const assertDocked = async () => {
      const state = await measure();
      assert(state.inputReachable, 'streaming activity must not cover the input');
      assert(!state.pageOverflow, 'only the conversation scrolls, not the page');
      assert(Math.abs(state.footerBottom - state.viewportBottom) <= 1, 'composer stays at the viewport bottom');
    };
    const waitForBottom = () => page.waitForFunction(() => {
      const el = document.querySelector('[data-slot="aui_thread-viewport"]');
      return el.scrollHeight - el.clientHeight - el.scrollTop <= 2;
    }, null, { timeout: 3000 });
    await page.goto(process.env.CONSOLE_TEST_URL || 'http://127.0.0.1:18889');
    await page.locator('#text').fill('scroll-fixture');
    await page.locator('#send').click();
    await rows.nth(25).waitFor();
    await assertDocked();
    await waitForBottom();
    assert((await measure()).top > 100, 'expanded streaming activity scrolls as it grows');

    // Reading earlier activity pauses follow even while more native events arrive.
    const bounds = await viewport.boundingBox();
    await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 120);
    await page.mouse.wheel(0, -500);
    await page.waitForTimeout(250);
    const before = await measure();
    const count = await rows.count();
    await rows.nth(count + 5).waitFor();
    const paused = await measure();
    assert(Math.abs(paused.top - before.top) < 2, 'new records preserve the reader position');
    assert(paused.bottomGap > before.bottomGap, 'records continue to arrive while follow is paused');
    await assertDocked();

    await page.locator('.aui-thread-scroll-to-bottom').click();
    await waitForBottom();
    const resumed = await measure();
    await rows.nth((await rows.count()) + 5).waitFor();
    await waitForBottom();
    assert((await measure()).top > resumed.top, 'returning to bottom resumes follow');
    await page.locator('#text').fill('保留追加说明草稿');

    for (const [name, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844]]) {
      await page.setViewportSize({ width, height });
      await waitForBottom();
      await rows.nth((await rows.count()) + 3).waitFor();
      await waitForBottom();
      await assertDocked();
      const last = await rows.last().boundingBox();
      assert(last.y + last.height <= (await measure()).footerTop, 'latest activity remains above the composer');
      assert.equal(await page.locator('#text').inputValue(), '保留追加说明草稿');
      if (process.env.CONSOLE_SCREENSHOTS) {
        fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/activity-scroll-${name}.png` });
      }
    }
    await page.waitForFunction(() => !document.querySelector('#interrupt'), null, { timeout: 30000 });
    await page.locator('.activity-trigger').first().click();
    await assertDocked();
    await page.locator('.aui-thread-scroll-to-bottom').click();
    await waitForBottom();
    await assertDocked();
    assert.deepEqual(errors, []);
    console.log('Activity scrolling passed: streaming follow, reader position, resume, composer hit testing, desktop/mobile, draft retention and completed disclosure.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

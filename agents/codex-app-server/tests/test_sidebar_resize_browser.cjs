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
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.locator('#text').fill('调整宽度时保留草稿');
    const handle = page.getByRole('separator', { name: '调整会话列表宽度', exact: true });
    const sidebar = page.locator('[data-slot="sidebar-container"]');
    const expectWidth = async width => {
      await page.waitForFunction(expected => {
        const sidebar = document.querySelector('[data-slot="sidebar-container"]');
        const gap = document.querySelector('[data-slot="sidebar-gap"]');
        return sidebar && gap && Math.round(sidebar.getBoundingClientRect().width) === expected
          && Math.round(gap.getBoundingClientRect().width) === expected;
      }, width);
      assert.equal(Number(await handle.getAttribute('aria-valuenow')), width);
      assert.equal(Math.round((await page.locator('[data-slot="sidebar-gap"]').boundingBox()).width), width);
    };
    const dragBy = async delta => {
      const box = await handle.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 + delta, box.y + box.height / 2, { steps: 8 });
      assert.equal(await page.locator('body').getAttribute('data-sidebar-resizing'), 'true');
      assert.equal(await sidebar.evaluate(el => getComputedStyle(el).transitionDuration), '0s');
      await page.mouse.up();
      assert.equal(await page.locator('body').getAttribute('data-sidebar-resizing'), null);
    };

    await expectWidth(320);
    await dragBy(120);
    await expectWidth(440);
    assert.equal(await page.locator('#text').inputValue(), '调整宽度时保留草稿');
    assert.equal(await page.evaluate(() => getSelection().toString()), '');
    await page.getByRole('button', { name: '收起侧栏', exact: true }).click();
    await handle.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: '切换侧栏', exact: true }).click();
    await expectWidth(440);
    await page.reload();
    await expectWidth(440);

    await dragBy(-600);
    await expectWidth(240);
    await dragBy(1000);
    await expectWidth(640);
    await handle.dblclick();
    await expectWidth(320);
    await handle.focus();
    await page.keyboard.press('ArrowRight');
    await expectWidth(336);
    await page.keyboard.press('ArrowLeft');
    await expectWidth(320);
    await page.keyboard.press('Home');
    await expectWidth(240);
    await page.keyboard.press('End');
    await expectWidth(640);
    await page.setViewportSize({ width: 800, height: 900 });
    await expectWidth(350);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.setViewportSize({ width: 1440, height: 900 });
    await expectWidth(640);

    await page.getByRole('button', { name: '切换任务面板', exact: true }).click();
    await expectWidth(510);
    assert((await page.locator('.workbench-chat-column').boundingBox()).width >= 399);
    await page.getByRole('button', { name: '切换任务面板', exact: true }).click();
    await expectWidth(640);

    const box = await handle.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await handle.evaluate(el => el.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true, pointerId: 1 })));
    assert.equal(await page.locator('body').getAttribute('data-sidebar-resizing'), null);
    await page.mouse.up();
    await handle.dblclick();
    await expectWidth(320);

    if (process.env.CONSOLE_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await dragBy(120);
      await expectWidth(440);
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/sidebar-resized.png` });
      await page.getByRole('button', { name: '切换深色主题', exact: true }).click();
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/sidebar-resized-dark.png` });
    }

    const desktopWidth = Number(await handle.getAttribute('aria-valuenow'));
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await handle.count(), 0);
    await page.getByRole('button', { name: '切换侧栏', exact: true }).click();
    const drawer = page.locator('[data-sidebar="sidebar"][data-mobile="true"]');
    await drawer.waitFor();
    assert.equal(Math.round((await drawer.boundingBox()).width), 288);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await drawer.getByRole('button', { name: '收起侧栏', exact: true }).click();
    await drawer.waitFor({ state: 'hidden' });
    await page.setViewportSize({ width: 1440, height: 900 });
    await expectWidth(desktopWidth);
    assert.deepEqual(errors, []);
    console.log('Sidebar resize: drag, bounds, persistence, collapse/expand, reset, keyboard, cancellation, viewport changes and mobile passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE,
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    const errors = [];
    const submissions = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => {
      if (/\/api\/sessions\/[^/]+\/messages$/.test(request.url())) submissions.push(request.postDataJSON());
      if (request.url().endsWith('/api/native/call')
        && ['thread/goal/set', 'turn/steer'].includes(request.postDataJSON().method)) {
        submissions.push(request.postDataJSON());
      }
    });
    await page.goto(process.env.CONSOLE_TEST_URL);
    const input = page.locator('#text');
    await input.fill('');
    const cdp = await page.context().newCDPSession(page);
    await input.evaluate((element) => {
      window.imeInput = element;
      window.imeEvents = [];
      for (const type of ['compositionstart', 'compositionend']) {
        element.addEventListener(type, (event) => window.imeEvents.push({ type, data: event.data }));
      }
    });
    const settled = () => page.evaluate(() =>
      new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const compose = async (text) => {
      // CDP creates a real browser composition range. Synthetic input events
      // cannot detect React assignments that silently cancel that range.
      await cdp.send('Input.imeSetComposition', { text, selectionStart: text.length, selectionEnd: text.length });
      await settled();
    };
    const confirmKey = async (init) => {
      assert.equal(await input.evaluate((element, init) =>
        element.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', ...init })), init),
      true, 'Confirming an IME candidate must leave Enter to the input method');
      assert.equal(submissions.length, 0);
    };
    const commit = async (text) => {
      await cdp.send('Input.insertText', { text });
      await settled();
    };

    for (const text of ['n', 'ni', 'nih', 'niha', 'nihao']) {
      await compose(text);
      assert.equal(await input.inputValue(), text, 'Each update replaces the same composition range');
      assert.equal(await input.evaluate((element) => element === window.imeInput && document.activeElement === element), true);
    }
    await confirmKey({ isComposing: true });
    await confirmKey({ keyCode: 229 });
    await commit('你好');
    assert.equal(await input.inputValue(), '你好');
    assert.deepEqual(await page.evaluate(() => window.imeEvents), [
      { type: 'compositionstart', data: '' },
      { type: 'compositionend', data: '你好' },
    ], 'One continuous pinyin sequence must produce one composition');

    // Compose in the middle of an existing draft, across a workspace render.
    await input.fill('前缀 后缀');
    await input.evaluate((element) => element.setSelectionRange(3, 3));
    for (const text of ['z', 'zh', 'zhong', 'zhongwen']) {
      await compose(text);
      assert.equal(await input.inputValue(), `前缀 ${text}后缀`);
    }
    await page.setViewportSize({ width: 1100, height: 800 });
    await settled();
    await compose('中文');
    await commit('中文');
    assert.equal(await input.inputValue(), '前缀 中文后缀');
    await compose('shi');
    await compose('');
    assert.equal(await input.inputValue(), '前缀 中文后缀', 'Cancelling removes only provisional text');
    await input.evaluate((element) => element.setSelectionRange(5, 5));
    await input.pressSequentially(' test');
    assert.equal(await input.inputValue(), '前缀 中文 test后缀');
    await input.press('Backspace');
    assert.equal(await input.inputValue(), '前缀 中文 tes后缀');

    // A Skill token must not take over candidate confirmation.
    await input.fill('$');
    await page.getByRole('listbox', { name: '引用 Skill', exact: true }).waitFor();
    await compose('da');
    await confirmKey({ isComposing: true });
    await compose('dai');
    await commit('代码');
    assert.equal(await input.inputValue(), '$代码');

    await input.fill('');
    await page.getByRole('button', { name: '添加到聊天', exact: true }).click();
    await page.getByRole('menuitem', { name: /^目标/ }).click();
    await page.getByRole('button', { name: '退出目标输入', exact: true }).waitFor();
    await input.focus();
    await settled();
    for (const text of ['m', 'mu', 'mubiao']) {
      await compose(text);
      assert.equal(await input.inputValue(), text);
    }
    await confirmKey({ isComposing: true });
    await commit('目标');
    assert.equal(await input.inputValue(), '目标');
    await page.getByRole('button', { name: '退出目标输入', exact: true }).click();

    // Normal Enter still submits the committed draft exactly once.
    await input.fill('你好，中文连续输入');
    await input.press('Enter');
    await page.locator('[data-slot=aui_message-group]').getByText('你好，完整回复。', { exact: true }).waitFor();
    assert.equal(submissions.length, 1);
    assert.equal(submissions[0].text, '你好，中文连续输入');
    assert.deepEqual(errors, []);
    console.log('Composer IME: continuous pinyin, candidate confirmation, caret, cancellation, Skills, goal mode and send passed.');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });

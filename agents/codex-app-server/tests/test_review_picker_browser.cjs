'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], calls = [];
    let gitMode = '', loseReceipt = false, releaseStart, holdStart = false;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (request.url().endsWith('/api/native/call')) calls.push({ ...request.postDataJSON(), key: request.headers()['idempotency-key'] });
    });
    await page.route('**/api/native/call', async route => {
      const { method, params } = route.request().postDataJSON();
      if (method === 'command/exec' && params.command[0] === 'git') {
        if (gitMode === 'error' && params.command[1] === 'for-each-ref') {
          gitMode = '';
          return route.fulfill({ json: { exitCode: 128, stdout: '', stderr: 'fixture Git unavailable' } });
        }
        if (gitMode === 'empty' && params.command[1] === 'rev-parse')
          return route.fulfill({ json: { exitCode: 1, stdout: '', stderr: '' } });
      }
      if (method === 'review/start') {
        if (holdStart) await new Promise(resolve => { releaseStart = resolve; });
        if (loseReceipt) {
          loseReceipt = false;
          await route.fetch();
          return route.fulfill({ status: 502, json: { error: 'fixture lost review receipt' } });
        }
      }
      return route.continue();
    });
    const dialog = () => page.getByRole('dialog').filter({ has: page.getByRole('button', { name: '关闭审查', exact: true }) });
    const open = async () => {
      await page.getByRole('button', { name: '会话操作', exact: true }).click();
      await page.getByRole('menuitem', { name: '审查更改', exact: true }).click();
      await page.getByRole('dialog', { name: '选择审查类型', exact: true }).waitFor();
      await page.waitForFunction(() => document.activeElement?.getAttribute('role') === 'listbox');
    };
    const reviews = () => calls.filter(call => call.method === 'review/start');
    const screenshot = async name => {
      if (!process.env.CONSOLE_SCREENSHOTS) return;
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/${name}.png` });
    };
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.locator('#text').fill('审查选择器验收');
    await page.locator('#send').click();
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    const threadStarts = calls.filter(call => call.method === 'thread/start').length;

    // /review enters the same four presets, with keyboard focus on the list.
    await page.locator('#text').fill('/review');
    await page.locator('#text').press('Enter');
    await dialog().waitFor();
    assert.deepEqual(await dialog().getByRole('listbox').getByRole('option').allTextContents(), [
      '1.与基准分支比较PR 风格', '2.审查未提交的更改', '3.审查指定提交', '4.自定义审查要求',
    ]);
    await page.keyboard.press('Enter');
    await page.getByRole('combobox', { name: '搜索分支' }).waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('dialog', { name: '选择审查类型', exact: true }).waitFor();
    await page.keyboard.press('Escape');
    await dialog().waitFor({ state: 'hidden' });

    // Menu invocation preserves the existing chat draft through selection and cancellation.
    const draft = '保留这段尚未发送的草稿';
    await page.locator('#text').fill(draft);
    await open();
    await screenshot('review-presets');
    await page.keyboard.press('Enter');
    await dialog().getByText('当前分支：feature/review', { exact: true }).waitFor();
    const branchSearch = page.getByRole('combobox', { name: '搜索分支' });
    await branchSearch.fill('not-a-branch');
    await dialog().getByText('没有匹配的结果', { exact: true }).waitFor();
    const count = reviews().length;
    await branchSearch.press('Enter');
    assert.equal(reviews().length, count);
    await branchSearch.fill('MAI');
    assert.deepEqual(await dialog().getByRole('listbox').getByRole('option').allTextContents(), ['main']);
    await screenshot('review-branches');
    await branchSearch.press('Enter');
    await dialog().waitFor({ state: 'hidden' });
    assert.deepEqual(reviews().at(-1).params.target, { type: 'baseBranch', branch: 'main' });
    assert.equal(reviews().at(-1).params.delivery, 'inline');
    assert.equal(await page.locator('#text').inputValue(), draft);

    await open();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await dialog().waitFor({ state: 'hidden' });
    assert.deepEqual(reviews().at(-1).params.target, { type: 'uncommittedChanges' });

    await open();
    await dialog().getByRole('option', { name: /审查指定提交/ }).click();
    const commitSearch = page.getByRole('combobox', { name: '搜索提交' });
    await commitSearch.fill('中文');
    assert.equal(await dialog().getByRole('listbox').getByRole('option').count(), 1);
    await commitSearch.fill('a'.repeat(40));
    await commitSearch.press('Enter');
    await dialog().waitFor({ state: 'hidden' });
    assert.deepEqual(reviews().at(-1).params.target, { type: 'commit', sha: 'a'.repeat(40), title: 'Fix review keyboard navigation' });

    await open();
    await page.keyboard.press('End');
    await page.keyboard.press('Enter');
    const instructions = dialog().getByRole('textbox', { name: '审查要求', exact: true });
    await instructions.fill('检查权限边界');
    await instructions.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true, keyCode: 229 });
    assert.equal(reviews().length, count + 3);
    await instructions.press('Shift+Enter');
    await instructions.press('a');
    assert.equal(await instructions.inputValue(), '检查权限边界\na');
    await page.keyboard.press('Escape');
    await page.keyboard.press('Enter');
    assert.equal(await dialog().getByRole('textbox', { name: '审查要求', exact: true }).inputValue(), '检查权限边界\na');
    await page.keyboard.press('Enter');
    await dialog().waitFor({ state: 'hidden' });
    assert.deepEqual(reviews().at(-1).params.target, { type: 'custom', instructions: '检查权限边界\na' });

    // A failed Git query is recoverable; an empty repository cannot submit a commit.
    gitMode = 'error';
    await open();
    await page.keyboard.press('Enter');
    await dialog().getByRole('alert').filter({ hasText: 'fixture Git unavailable' }).waitFor();
    await dialog().getByRole('button', { name: '重试', exact: true }).click();
    await branchSearch.waitFor();
    await page.keyboard.press('Escape');
    gitMode = 'empty';
    await dialog().getByRole('option', { name: /审查指定提交/ }).click();
    await dialog().getByText('当前分支没有可审查的提交', { exact: true }).waitFor();
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    await dialog().waitFor({ state: 'hidden' });
    gitMode = '';

    // Retrying after a lost receipt reuses the request identity, even after reopening.
    loseReceipt = true;
    await open();
    await dialog().getByRole('option', { name: /审查未提交的更改/ }).click();
    await dialog().getByRole('alert').filter({ hasText: 'fixture lost review receipt' }).waitFor();
    const failed = reviews().at(-1);
    await page.keyboard.press('Escape');
    await dialog().waitFor({ state: 'hidden' });
    await open();
    holdStart = true;
    await dialog().getByRole('option', { name: /审查未提交的更改/ }).click();
    await dialog().getByText('正在启动审查…', { exact: true }).waitFor();
    assert.equal(reviews().at(-1).key, failed.key);
    const pendingCount = reviews().length;
    await page.keyboard.press('Escape');
    await page.keyboard.press('Enter');
    assert.equal(await dialog().count(), 1);
    assert.equal(reviews().length, pendingCount);
    releaseStart(); holdStart = false;
    await dialog().waitFor({ state: 'hidden' });

    // Delivery is explicit and remains part of the native request identity.
    await open();
    await dialog().getByRole('combobox', { name: '审查位置', exact: true }).selectOption('detached');
    await dialog().getByRole('option', { name: /审查未提交的更改/ }).click();
    await dialog().waitFor({ state: 'hidden' });
    assert.equal(reviews().at(-1).params.delivery, 'detached');
    assert.notEqual(reviews().at(-1).key, failed.key);

    // Light/dark and narrow layouts keep the selector inside the viewport.
    await page.evaluate(() => document.documentElement.classList.add('dark'));
    await open();
    await screenshot('review-dark');
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 390, height: 844 });
    await open();
    await dialog().getByRole('option', { name: /自定义审查要求/ }).click();
    await screenshot('review-mobile');
    const box = await dialog().boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844);
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    await dialog().waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#text').inputValue(), draft);
    assert.equal(calls.filter(call => call.method === 'thread/start').length, threadStarts);
    assert.ok(calls.filter(call => call.method === 'command/exec').every(call => call.params.cwd === '/fixture'));
    assert.deepEqual(errors, []);
    console.log('Review picker: four presets, Git search, inline/detached delivery, keyboard/IME, draft, retries, busy guard and responsive layouts passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

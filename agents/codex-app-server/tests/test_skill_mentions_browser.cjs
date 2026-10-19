'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    const errors = []; const calls = []; const messages = [];
    let failSkills = false; let delaySkills = false;
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => {
      if (request.url().endsWith('/api/native/call')) calls.push(request.postDataJSON());
      if (/\/api\/sessions\/[^/]+\/messages$/.test(request.url())) messages.push(request.postDataJSON());
    });
    await page.route('**/api/native/call', async (route) => {
      if (route.request().postDataJSON().method !== 'skills/list') return route.continue();
      if (delaySkills) await new Promise((resolve) => setTimeout(resolve, 200));
      if (failSkills) return route.fulfill({ status: 503, json: { error: 'skill-discovery-unavailable' } });
      const alpha = { name: 'alpha-review', description: '检查代码修改', path: '/fixture/alpha/SKILL.md', enabled: true, interface: { displayName: '代码审查' } };
      return route.fulfill({ json: { data: [
        { skills: [alpha, { name: 'plugin:beta', description: '生成测试', path: '/fixture/beta/SKILL.md', enabled: true }, { ...alpha, name: 'disabled', enabled: false }] },
        { skills: [alpha] },
      ] } });
    });
    await page.goto(process.env.CONSOLE_TEST_URL);
    const input = page.locator('#text');
    const list = page.getByRole('listbox', { name: '引用 Skill', exact: true });
    const chip = page.getByRole('button', { name: '移除引用 代码审查', exact: true });
    await input.fill('$');
    await list.getByRole('option').nth(1).waitFor();
    assert.equal(await list.getByRole('option').count(), 2);
    const protocol = await (await page.request.get(process.env.CONSOLE_TEST_URL + '/api/native/protocol')).json();
    assert.ok(calls.some((call) => call.method === 'skills/list' && call.params.cwds[0] === protocol.cwd && call.params.forceReload));
    if (process.env.CONSOLE_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: process.env.CONSOLE_SCREENSHOTS + '/skill-mentions.png' });
    }
    await input.press('ArrowDown');
    assert.match(await list.locator('[aria-selected=true]').textContent(), /plugin:beta/);
    await input.press('ArrowUp');
    await input.press('Enter');
    await chip.waitFor();
    assert.equal(await input.inputValue(), '');
    assert.equal(messages.length, 0, 'Enter selects a skill without sending a message');

    // Both entry points share the same reference identity.
    await page.getByRole('button', { name: '添加到聊天', exact: true }).click();
    await page.getByRole('menuitem', { name: /代码审查/ }).click();
    assert.equal(await chip.count(), 1);
    await input.fill('请用 $alpha-review 完成检查');
    await input.evaluate((element) => { element.setSelectionRange(6, 6); element.dispatchEvent(new Event('select', { bubbles: true })); });
    await list.getByRole('option').first().waitFor();
    await input.press('Tab');
    await page.waitForFunction(() => document.querySelector('#text').value === '请用  完成检查');
    assert.equal(await chip.count(), 1);
    await chip.click();
    assert.equal(await chip.count(), 0);

    await input.fill('请用 $代码');
    await list.getByRole('option').first().click();
    await chip.waitFor();
    assert.equal(await input.inputValue(), '请用 ');
    await input.fill('引用能力测试');
    const sent = page.waitForRequest((request) => /\/api\/sessions\/[^/]+\/messages$/.test(request.url()));
    await page.locator('#send').click();
    const parts = (await sent).postDataJSON().input_items;
    assert.deepEqual(parts.filter((part) => part.type === 'skill'), [{ type: 'skill', name: 'alpha-review', path: '/fixture/alpha/SKILL.md' }]);
    // Running turns accept the same structured skill via steer.
    await input.fill('$plugin:beta');
    await list.getByRole('option').first().waitFor();
    await input.press('Tab');
    await page.getByRole('button', { name: '移除引用 plugin:beta', exact: true }).waitFor();
    await input.fill('继续补充');
    const steered = page.waitForRequest((request) => request.url().endsWith('/api/native/call') && request.postDataJSON().method === 'turn/steer');
    await page.getByRole('button', { name: '发送补充', exact: true }).click();
    assert.ok((await steered).postDataJSON().params.input.some((part) => part.type === 'skill' && part.path === '/fixture/beta/SKILL.md'));
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector('#text').value === '');

    // Escape, IME and a missing match must not silently bind a skill.
    await input.fill('$alpha');
    await list.getByRole('option').first().waitFor();
    await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true });
    assert.equal(await chip.count(), 0);
    await input.press('Escape');
    await list.waitFor({ state: 'hidden' });
    assert.equal(await input.inputValue(), '$alpha');
    await input.fill('$missing');
    await page.getByRole('status').getByText('没有匹配的可用 Skill', { exact: true }).waitFor();
    await input.press('Escape');
    for (const literal of ['\\$alpha', '${HOME}', '$(pwd)', '$HOME/bin', '$alpha ']) {
      await input.fill(literal);
      assert.equal(await list.count(), 0, literal);
    }
    await input.fill('/');
    await page.getByRole('listbox', { name: '快捷命令', exact: true }).waitFor();
    await input.press('Escape');

    failSkills = true;
    await input.fill('$');
    await page.getByRole('alert').getByText(/skill-discovery-unavailable/).waitFor();
    assert.equal(await input.inputValue(), '$');
    await input.press('Escape');
    failSkills = false; delaySkills = true;
    await input.fill('重新选择 $');
    await input.press('Enter');
    await list.getByRole('option').first().waitFor();
    assert.equal(messages.length, 1, 'Enter during discovery does not send an incomplete draft');
    await input.press('Escape');

    // Goal input remains text-only.
    await page.getByRole('button', { name: '添加到聊天', exact: true }).click();
    await page.getByRole('menuitem', { name: /^目标/ }).click();
    await input.fill('$alpha');
    assert.equal(await list.count(), 0);
    await page.getByRole('button', { name: '退出目标输入', exact: true }).click();
    await input.fill('');
    await page.setViewportSize({ width: 390, height: 844 });
    await input.fill('$');
    await list.getByRole('option').first().waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await list.getByRole('option').first().click();
    await chip.waitFor();
    assert.deepEqual(errors, []);
    console.log('Skill mentions: discovery, caret completion, keyboard/mouse, deduplication, native send/steer, IME, errors and mobile passed.');
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });

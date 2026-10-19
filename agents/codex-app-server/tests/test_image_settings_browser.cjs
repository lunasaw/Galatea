'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    let saved = { enabled: false, connection: 'current', baseUrl: '', model: 'gpt-image-2', quality: 'medium', size: '1024x1024', revision: '1', editable: true, keyConfigured: false, currentBaseUrl: 'https://gateway.example/v1', currentKeyConfigured: true, scriptAvailable: true, sdkAvailable: true, skillPath: null };
    let failNext = false; const writes = [];
    await page.route('**/api/image-generation/settings', async route => {
      if (route.request().method() === 'GET') return route.fulfill({ json: saved });
      const body = route.request().postDataJSON(); writes.push(body);
      assert.ok(route.request().headers()['idempotency-key']);
      if (failNext) { failNext = false; return route.fulfill({ status: 409, json: { error: '图像设置已在其他页面修改，请重新读取后再保存。' } }); }
      const { apiKey, ...fields } = body;
      saved = { ...saved, ...fields, revision: String(Number(saved.revision) + 1), keyConfigured: !!apiKey || saved.keyConfigured, skillPath: body.enabled ? '/fixture/skills/imagegen-api/SKILL.md' : null };
      return route.fulfill({ json: saved });
    });
    await page.route('**/api/image-generation/check', route => route.fulfill({ json: { status: 'blocked', models: ['gpt-image-1.5', 'gpt-image-2'], httpStatus: 403, message: '当前 API Key 所属分组未开启图像生成。请在网关为该分组启用绘图权限。' } }));
    const control = name => page.getByLabel(name, { exact: true });
    const button = name => page.getByRole('button', { name, exact: true });
    const open = async () => {
      if (page.viewportSize().width < 768) await button('切换侧栏').click();
      await button('设置').click();
      await page.getByRole('navigation', { name: '设置分类' }).getByRole('button', { name: '图像生成', exact: true }).click();
      await control('绘图模型').waitFor();
    };
    const ready = () => page.waitForFunction(() => !document.querySelector('.image-generation-settings fieldset')?.disabled);
    await page.goto(process.env.CONSOLE_TEST_URL); await open();
    assert.equal(await control('图像 API 地址').getAttribute('readonly'), '');
    assert.equal(await control('图像 API Key').count(), 0);
    await button('检查连接与模型').click();
    await page.getByRole('alert').filter({ hasText: '分组未开启图像生成' }).waitFor();
    assert.equal(await page.locator('#image-api-models option').count(), 2);
    await control('启用 API 绘图').check(); await control('绘图模型').fill('gpt-image-1.5');
    await button('保存并启用 API 绘图').click(); await ready();
    assert.equal(writes[0].connection, 'current'); assert.equal(writes[0].apiKey, undefined);
    await page.reload(); await open(); assert.equal(await control('绘图模型').inputValue(), 'gpt-image-1.5');
    await control('图像连接方式').selectOption('custom'); await control('图像 API 地址').fill('https://images.example/v1');
    await control('图像 API Key').fill('fixture-password');
    assert.equal(await control('图像 API Key').getAttribute('type'), 'password');
    await button('保存并启用 API 绘图').click(); await ready();
    assert.equal(writes.at(-1).apiKey, 'fixture-password'); assert.equal(await control('图像 API Key').inputValue(), '');
    failNext = true; await control('图片质量').selectOption('high'); await button('保存并启用 API 绘图').click();
    await page.getByRole('alert').filter({ hasText: '其他页面修改' }).waitFor();
    assert.equal(await control('图片质量').inputValue(), 'high');
    await button('重新读取').click(); await ready(); assert.equal(await control('图片质量').inputValue(), 'medium');
    const shots = process.env.CONSOLE_SCREENSHOTS;
    await page.locator('.settings-body').evaluate(element => { element.scrollTop = 0; });
    if (shots) { fs.mkdirSync(shots, { recursive: true }); await page.screenshot({ path: shots + '/image-settings-desktop.png', fullPage: true }); }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => { const box = document.querySelector('.settings-page').getBoundingClientRect(); return box.left >= 0 && box.right <= window.innerWidth; });
    assert.equal(await page.locator('.settings-body').evaluate(element => element.scrollWidth <= element.clientWidth + 1), true);
    if (shots) await page.screenshot({ path: shots + '/image-settings-mobile.png', fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1080 });
    await page.evaluate(() => document.documentElement.classList.add('dark'));
    if (shots) await page.screenshot({ path: shots + '/image-settings-dark.png', fullPage: true });
    assert.deepEqual(errors, []);
    console.log('Image settings: model/connection save, key handling, permission feedback, conflicts, reload and responsive layout passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });

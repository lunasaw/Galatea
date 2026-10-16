'use strict';
// Run against console_browser_fixture.py; all extension calls are synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(12000);
    const errors = [], calls = [];
    page.on('pageerror', (error) => errors.push(error.message));
    // Exercise the authorization-link fallback when popups are blocked.
    await page.addInitScript(() => { window.open = () => null; });
    let enabled = false, installed = false, rejectInstall = true, rejectReload = false;
    const base = { enabled: true, installed: false, authPolicy: 'ON_USE', installPolicy: 'AVAILABLE' };
    const remotePlugin = () => ({ ...base, id: 'remote/analysis', name: 'analysis', installed, interface: {
      displayName: 'Analysis Tools', shortDescription: 'Skills and MCP together', capabilities: ['research'],
      websiteUrl: 'https://example.com/plugin', privacyPolicyUrl: 'javascript:alert(1)',
    } });
    await page.route('**/api/native/call', async (route) => {
      const { method, params } = route.request().postDataJSON();
      calls.push({ method, params });
      let result;
      if (method === 'skills/list') result = { data: [{ cwd: params.cwds[0], skills: [
        { name: 'disabled-skill', path: '/fixture/disabled/SKILL.md', enabled, description: 'A switchable workflow', scope: 'user' },
        ...(installed ? [{ name: 'plugin-skill', path: '/plugins/analysis/SKILL.md', enabled: true, pluginId: 'remote/analysis', description: 'Installed plugin workflow' }] : []),
      ], errors: [{ path: '/fixture/broken/SKILL.md', message: 'Invalid skill metadata' }] }] };
      else if (method === 'skills/config/write') { enabled = params.enabled; result = { effectiveEnabled: enabled }; }
      else if (method === 'mcpServerStatus/list') result = params.cursor ? { data: [
        { name: 'failed-service', authStatus: 'unknown', runtimeStatus: 'failed', toolsError: 'Connection refused', tools: {} },
        { name: 'unknown-service', authStatus: 'unsupported', runtimeStatus: null, tools: {} },
      ], nextCursor: null } : { data: [
        { name: 'connected-service', authStatus: 'unsupported', runtimeStatus: 'connected', tools: { echo: { name: 'echo', description: 'Echo text' } }, resources: [{ name: 'Guide', uri: 'file:///guide' }] },
        { name: 'oauth-service', authStatus: 'notLoggedIn', runtimeStatus: 'authenticationRequired', tools: {} },
      ], nextCursor: 'second-page' };
      else if (method === 'mcpServer/oauth/login') result = { authorizationUrl: 'https://example.com/authorize?state=keep-me' };
      else if (method === 'config/mcpServer/reload') {
        assert.equal(params, null);
        if (rejectReload) return route.fulfill({ status: 422, json: { error: 'Reload rejected by runtime' } });
        result = {};
      } else if (method === 'plugin/list') result = { marketplaces: [
        { name: 'remote-catalog', path: null, plugins: [remotePlugin()] },
        { name: 'local-catalog', path: '/fixture/marketplace', plugins: [
          { ...base, id: 'local/builtin', name: 'builtin', installed: true, installPolicy: 'INSTALLED_BY_DEFAULT' },
          { ...base, id: 'local/blocked', name: 'blocked', installPolicy: 'NOT_AVAILABLE', disabledReason: 'Unavailable in this deployment' },
        ] },
      ], marketplaceLoadErrors: [{ marketplacePath: '/bad/marketplace', message: 'Marketplace unavailable' }], featuredPluginIds: ['remote/analysis'] };
      else if (method === 'plugin/read') {
        assert.deepEqual(params, { pluginName: 'analysis', remoteMarketplaceName: 'remote-catalog' });
        result = { plugin: { marketplaceName: 'remote-catalog', summary: remotePlugin(), description: 'Plugin details from runtime',
          skills: [{ name: 'plugin-skill', path: '/plugins/analysis/SKILL.md', enabled: true, description: 'Workflow' }], apps: [], mcpServers: ['analysis-mcp'] } };
      } else if (method === 'plugin/install') {
        assert.deepEqual(params, { pluginName: 'analysis', remoteMarketplaceName: 'remote-catalog' });
        if (rejectInstall) { rejectInstall = false; return route.fulfill({ status: 422, json: { error: 'Install rejected by runtime' } }); }
        installed = true;
        result = { authPolicy: 'ON_INSTALL', appsNeedingAuth: [{ id: 'app', name: 'Research App', installUrl: 'https://example.com/connect' }] };
      } else if (method === 'plugin/uninstall') {
        assert.deepEqual(params, { pluginId: 'remote/analysis' }); installed = false; result = {};
      } else if (method === 'plugin/reconcile') result = {
        changedPlugins: [{ id: 'remote/analysis', hasSkills: true, hasMcps: true, hasApps: false, hasHooks: false }],
        failedRemotePluginIds: ['remote/failed'], failedMaterializationRemotePluginIds: ['remote/download-failed'],
      };
      else return route.continue();
      return route.fulfill({ json: result });
    });
    const open = async (tab) => {
      if (!(await page.getByRole('dialog').count())) await page.getByRole('button', { name: '设置', exact: true }).click();
      await page.getByRole('dialog').getByRole('button', { name: tab, exact: true }).click();
    };
    const screenshot = async (name) => {
      const dir = process.env.CONSOLE_SCREENSHOTS;
      if (dir) { fs.mkdirSync(dir, { recursive: true }); await page.screenshot({ path: `${dir}/${name}.png` }); }
    };
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.locator('#text').fill('integrations-fixture');
    await page.locator('#send').click();
    await page.locator('[data-slot=aui_message-group]').getByText('你好，完整回复。', { exact: true }).waitFor();
    await open('Skills');
    const skill = page.locator('.native-card').filter({ hasText: 'disabled-skill' });
    await skill.waitFor();
    assert.equal(await skill.getByRole('button', { name: '在对话中使用' }).isDisabled(), true);
    await page.getByRole('alert').filter({ hasText: 'Invalid skill metadata' }).waitFor();
    await skill.getByRole('button', { name: '启用', exact: true }).click();
    await skill.getByRole('button', { name: '停用', exact: true }).waitFor();
    await page.getByLabel('搜索 Skills', { exact: true }).fill('no-match');
    await page.getByText('没有匹配的 Skills').waitFor();
    await page.getByLabel('搜索 Skills', { exact: true }).fill('');
    const skillRequest = page.waitForRequest((request) => request.url().endsWith('/messages') && request.method() === 'POST');
    await skill.getByRole('button', { name: '在对话中使用' }).click();
    assert.ok((await skillRequest).postDataJSON().input_items.some((item) => item.type === 'skill' && item.path === '/fixture/disabled/SKILL.md'));
    await page.getByRole('dialog').waitFor({ state: 'hidden' });

    await open('MCP');
    await page.locator('[data-mcp=unknown-service]').getByText('状态未知', { exact: true }).waitFor();
    await page.locator('[data-mcp=failed-service]').getByRole('alert').getByText('Connection refused').waitFor();
    await page.getByText('1/4 个服务已连接').waitFor();
    await page.locator('[data-mcp=connected-service] summary').click();
    await page.getByText('Echo text', { exact: true }).waitFor();
    await page.getByRole('button', { name: '登录 oauth-service' }).click();
    const auth = page.getByRole('link', { name: '打开授权页面', exact: true });
    await auth.waitFor();
    assert.equal(await auth.getAttribute('href'), 'https://example.com/authorize?state=keep-me');
    assert.ok(calls.find((call) => call.method === 'mcpServer/oauth/login').params.threadId);
    await page.getByRole('button', { name: '重新加载全部', exact: true }).click();
    await page.getByText('已请求重新加载 MCP 服务', { exact: true }).waitFor();
    rejectReload = true;
    await page.getByRole('button', { name: '重新加载全部', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Reload rejected by runtime' }).waitFor();
    await screenshot('mcp');

    await open('插件');
    const remote = page.locator('[data-plugin="remote/analysis"]');
    await remote.waitFor();
    assert.equal(await page.locator('[data-plugin="local/builtin"]').getByRole('button', { name: '卸载' }).count(), 0);
    assert.equal(await page.locator('[data-plugin="local/blocked"]').getByRole('button', { name: '安装', exact: true }).count(), 0);
    await page.getByRole('alert').filter({ hasText: 'Marketplace unavailable' }).waitFor();
    await remote.getByRole('button', { name: '查看详情' }).click();
    const detail = page.getByRole('region', { name: '插件详情', exact: true });
    await detail.getByText('Plugin details from runtime', { exact: true }).waitFor();
    assert.equal(await detail.getByRole('link', { name: '隐私政策' }).count(), 0);
    await remote.getByRole('button', { name: '安装', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Install rejected by runtime' }).waitFor();
    await remote.getByRole('button', { name: '安装', exact: true }).click();
    await remote.getByRole('button', { name: '卸载', exact: true }).waitFor();
    await detail.getByRole('button', { name: '卸载', exact: true }).waitFor();
    await page.getByRole('link', { name: '打开应用授权页面' }).waitFor();
    assert.ok(calls.filter((call) => call.method === 'plugin/list' && call.params.forceRefetch).length > 0);
    await page.getByRole('button', { name: '同步已安装', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'remote/download-failed' }).waitFor();
    await screenshot('plugins');
    await open('Skills');
    await page.getByText('plugin-skill', { exact: true }).waitFor();
    await open('插件');
    await remote.getByRole('button', { name: '卸载', exact: true }).click();
    await remote.getByRole('button', { name: '安装', exact: true }).waitFor();
    await page.getByLabel('搜索插件', { exact: true }).fill('does-not-exist');
    await page.getByText('没有匹配的插件').waitFor();
    await page.getByLabel('搜索插件', { exact: true }).fill('');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await screenshot('plugins-mobile');

    // The same inventory remains readable in an immutable deployment.
    await page.route('**/api/native/protocol', async (route) => {
      const response = await route.fetch();
      await route.fulfill({ json: { ...(await response.json()), configuration_editable: false } });
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.reload();
    await open('插件');
    await remote.waitFor();
    assert.equal(await remote.getByRole('button', { name: '安装', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: '同步已安装', exact: true }).isDisabled(), true);
    await open('Skills');
    await skill.waitFor();
    assert.equal(await skill.getByRole('button', { name: '停用', exact: true }).isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('Integrations browser: skill actions, native input, MCP pagination/status/OAuth/reload errors, remote plugins/details/install/uninstall/sync, invalidation, policy, readonly and mobile passed.');
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });

'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1582, height: 1022 } });
    const errors = [], calls = [], fonts = [];
    let completed = false, unavailable = false, delayA = false;
    const thread = id => ({ id, cwd: '/fixture', agentNickname: id === 'child-a' ? 'Singer' : 'Euler', agentRole: 'worker',
      source: { subAgent: { thread_spawn: { agent_path: '/root/' + (id === 'child-a' ? 'cli_inspect' : 'definition_explanation') } } },
      preview: id === 'child-a' ? '检查 CLI 与数学表达式' : '使用自然数的后继定义解释加法',
      status: { type: id === 'child-a' && !completed ? 'active' : 'idle', activeFlags: [] } });
    const turn = () => ({ id: 'current', status: completed ? 'completed' : 'inProgress', startedAt: Math.floor(Date.now() / 1000) - 39,
      ...(completed ? { durationMs: 39000 } : {}), items: [
        { id: 'summary', type: 'reasoning', summary: ['已检查 CLI 入口。'], content: ['PRIVATE_REASONING_MUST_NOT_APPEAR'] },
        { id: 'progress', type: 'agentMessage', phase: 'commentary', text: '正在检查 Codex CLI 的多 Agent 入口。' },
        { id: 'command', type: 'commandExecution', command: 'codex features list', cwd: '/fixture', status: completed ? 'completed' : 'inProgress',
          aggregatedOutput: completed ? 'multi_agent true\nCLI inspection complete' : 'multi_agent true', ...(completed ? { exitCode: 0, durationMs: 39000 } : {}) },
        ...(completed ? [{ id: 'result', type: 'agentMessage', phase: 'final_answer', text: '检查完成。\n\n\\[a+0=a,\\qquad a+S(b)=S(a+b)\\]\n\n行内公式 \\(1+1=2\\)。代码 `$x$` 保持原样。' }] : []),
      ] });
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.url().endsWith('.woff2')) fonts.push(response.status()); });
    await page.addInitScript(() => {
      localStorage.setItem('codex-agent.active', 's');
      document.addEventListener('securitypolicyviolation', event => (window.cspViolations ||= []).push({ directive: event.violatedDirective, blocked: event.blockedURI, source: event.sourceFile, line: event.lineNumber, sample: event.sample }));
    });
    await page.route('**/api/sessions', async route => {
      if (route.request().method() !== 'GET') return route.continue();
      const response = await route.fetch();
      const sessions = await response.json();
      return route.fulfill({ response, json: sessions.map(s => s.id === 's' ? { ...s, thread_id: 'parent', name: '多 Agent 展示', cwd: '/fixture' } : s) });
    });
    await page.route('**/api/sessions/s/snapshot', async route => {
      const response = await route.fetch(); const snapshot = await response.json();
      return route.fulfill({ response, json: { ...snapshot,
        messages: [
          { id: 'question', role: 'user', text: '开一个多 Agent 会话，分别用两种方式解释为什么 1+1=2', turn_id: 'main-turn', first_seq: '1', last_seq: '1' },
          { id: 'answer', role: 'assistant', phase: 'final_answer', text: '两个 Agent 分别给出了两种解释：\n\n**直观计数法**\n\n一个苹果加一个苹果，共有两个苹果。\n\n**严格定义法**\n\n设后继运算为 \\(S\\)，加法定义为：\n\n\\[a+0=a,\\qquad a+S(b)=S(a+b)\\]\n\n因此：\n\n\\[1+1=1+S(0)=S(1+0)=S(1)=2\\]', turn_id: 'main-turn', first_seq: '3', last_seq: '3' },
        ],
        turns: [{ id: 'main-turn', status: 'completed' }],
        steps: [{ id: 'spawn', type: 'subAgentActivity', turn_id: 'main-turn', first_seq: '2', last_seq: '2', detail: { agentThreadId: 'child-a', agentPath: '/root/cli_inspect', kind: 'started' } }],
      } });
    });
    await page.route('**/api/native/call', async route => {
      const { method, params } = route.request().postDataJSON(); calls.push({ method, params });
      if (method === 'thread/list' && params.ancestorThreadId) {
        assert.equal(params.ancestorThreadId, 'parent'); assert.ok(params.sourceKinds.includes('subAgent'));
        return route.fulfill({ json: { data: [thread('child-a'), thread('child-b')], nextCursor: null } });
      }
      if (!['child-a', 'child-b'].includes(params?.threadId)) return route.continue();
      if (unavailable) return route.fulfill({ status: 503, json: { error: 'temporary-unavailable' } });
      if (delayA && params.threadId === 'child-a') await new Promise(resolve => setTimeout(resolve, 650));
      if (method === 'thread/read') {
        assert.equal(params.includeTurns, false);
        return route.fulfill({ json: { thread: thread(params.threadId) } });
      }
      if (method === 'thread/turns/list') {
        if (params.threadId === 'child-b') return route.fulfill({ json: { data: [{ id: 'b-turn', status: 'completed', durationMs: 1200, items: [
          { id: 'b-message', type: 'agentMessage', phase: 'final_answer', text: '第二个子 Agent：\\(S(1)=2\\)。' },
        ] }], nextCursor: null } });
        if (params.cursor) return route.fulfill({ json: { data: [{ id: 'earlier', status: 'completed', startedAt: 1, durationMs: 1000,
          items: [{ id: 'old-message', type: 'agentMessage', text: '更早的检查记录。', phase: 'final_answer' }] }], nextCursor: null } });
        return route.fulfill({ json: { data: [turn()], nextCursor: 'earlier-page' } });
      }
      throw Error('Unexpected child mutation: ' + method);
    });
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.locator('[data-slot="aui_assistant-message-root"] .katex-display').first().waitFor();
    assert.equal(await page.locator('[data-slot="aui_assistant-message-root"] .katex-display').count(), 2);
    await page.locator('#text').fill('保留这份主聊天草稿');
    await page.getByRole('button', { name: /处理过程/ }).click();
    await page.locator('.native-agent-activity').getByRole('button', { name: 'Cli inspect', exact: true }).click();
    const panel = page.getByRole('tabpanel', { name: '子 Agent面板', exact: true });
    await panel.getByRole('heading', { name: 'Cli inspect', exact: true }).waitFor();
    await panel.locator('.subagent-conversation-status').getByText('运行中', { exact: true }).waitFor();
    await panel.locator('.native-activity summary').filter({ hasText: 'codex features list' }).click();
    await panel.getByText('multi_agent true', { exact: false }).last().waitFor();
    completed = true;
    await panel.locator('.katex-display').waitFor();
    await panel.getByText('CLI inspection complete', { exact: false }).waitFor();
    await panel.locator('.subagent-conversation-status').getByText('已完成', { exact: true }).waitFor();
    assert.equal(await page.locator('#text').inputValue(), '保留这份主聊天草稿');
    assert.equal(await page.evaluate(() => localStorage.getItem('codex-agent.active')), 's');
    assert.equal(await panel.getByText('PRIVATE_REASONING_MUST_NOT_APPEAR', { exact: false }).count(), 0);
    await panel.getByRole('button', { name: '加载更早的消息', exact: true }).click();
    await panel.getByText('更早的检查记录。', { exact: true }).waitFor();
    await panel.getByRole('button', { name: '刷新子 Agent 会话', exact: true }).click();
    assert.equal(await panel.getByText('更早的检查记录。', { exact: true }).count(), 1);
    unavailable = true;
    await panel.getByRole('alert').filter({ hasText: '已保留当前内容' }).waitFor();
    assert.equal(await panel.locator('.katex-display').count(), 1);
    unavailable = false;
    await panel.getByRole('button', { name: '刷新子 Agent 会话', exact: true }).click();
    await panel.getByRole('alert').waitFor({ state: 'hidden' });
    if (process.env.CONSOLE_SCREENSHOTS) {
      await fs.mkdir(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: process.env.CONSOLE_SCREENSHOTS + '/subagents-light.png' });
      await page.getByRole('button', { name: '切换深色主题', exact: true }).click();
      await page.screenshot({ path: process.env.CONSOLE_SCREENSHOTS + '/subagents-dark.png', animations: 'disabled' });
    }
    delayA = true;
    await panel.getByRole('button', { name: '返回子 Agent 列表', exact: true }).click();
    await panel.getByRole('button', { name: '查看 Cli inspect', exact: true }).click();
    await panel.getByRole('button', { name: '返回子 Agent 列表', exact: true }).click();
    await panel.getByRole('button', { name: '查看 Definition explanation', exact: true }).click();
    await panel.getByText('第二个子 Agent：', { exact: false }).waitFor();
    await page.waitForTimeout(750);
    assert.equal(await panel.getByRole('heading').textContent(), 'Definition explanation');
    assert.equal(await panel.getByText('检查完成。', { exact: true }).count(), 0);
    assert.equal(calls.some(c => ['thread/resume', 'turn/start', 'thread/archive', 'thread/fork'].includes(c.method)), false);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('dialog', { name: '子 Agent', exact: true }).waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.evaluate(() => document.fonts.ready);
    assert.ok(fonts.length > 0 && fonts.every(status => status === 200), JSON.stringify(fonts));
    assert.deepEqual(await page.evaluate(() => window.cspViolations || []), []);
    assert.deepEqual(errors, []);
    console.log('Subagents: main-chat preservation, native activity, live updates, math, pagination, recovery, switching and mobile passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
 const browser = await chromium.launch({ headless:true, executablePath:process.env.CHROMIUM_EXECUTABLE, args:['--no-sandbox'] });
 try {
  const page = await browser.newPage({viewport:{width:1500,height:980}});
  const errors=[], calls=[];
  page.on('pageerror', e=>errors.push(e.message));
  page.on('request', r=> { if(r.url().endsWith('/api/native/call')) calls.push(r.postDataJSON()); });
  const base = process.env.CONSOLE_TEST_URL || 'http://127.0.0.1:18883';
  const projectName = 'Lifecycle-' + Date.now();
  const created = await page.request.post(base + '/api/native/call', {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    data: { method: 'project/create', params: { idempotencyKey: crypto.randomUUID(), name: projectName, roots: [{ path: '/fixture/lifecycle' }] } },
  });
  assert.ok(created.ok(), await created.text());
  await page.goto(process.env.CONSOLE_TEST_URL || 'http://127.0.0.1:18883');
  await page.waitForFunction(()=>document.body.textContent.includes('Fixture model'));
  await page.getByRole('button',{name:'在 ' + projectName + ' 中新建会话',exact:true}).click();
  const send = async text => {
    await page.locator('#text').fill(text); await page.locator('#send').click();
    await page.waitForFunction(()=>!document.querySelector('#interrupt'));
    await page.getByText(text,{exact:true}).last().waitFor();
    await page.waitForFunction(()=>!document.querySelector('#interrupt'));
  };
  await send('lifecycle-one'); await send('lifecycle-two'); await send('lifecycle-three');
  const original = await page.evaluate(()=>localStorage.getItem('codex-agent.active'));
  const row = id=>page.locator(".project-thread-row").filter({has:page.locator(`[data-slot="aui_thread-list-item-trigger"][title="${id}"]`)});
  const menu = async (id,label) => { await row(id).hover(); await row(id).getByRole('button',{name:'会话更多选项'}).click(); await page.getByRole('menuitem',{name:label,exact:true}).click(); };
  const confirm = async label => { const dialog = page.getByRole('dialog',{name:label,exact:true}); await dialog.getByRole('button',{name:'确认'+label,exact:true}).click(); await dialog.waitFor({state:'hidden'}); };
  // A message fork includes that turn and leaves later source turns intact.
  const second = page.locator('[data-slot="aui_user-message-root"]').filter({hasText:'lifecycle-two'});
  await second.hover(); await second.getByRole('button',{name:'从此轮分叉',exact:true}).click();
  await confirm('分叉会话');
  const fork = await page.evaluate(()=>localStorage.getItem('codex-agent.active'));
  assert.notEqual(fork, original);
  await page.getByText('lifecycle-two',{exact:true}).last().waitFor();
  assert.equal(await page.locator('[data-slot="aui_user-message-root"]').filter({hasText:'lifecycle-three'}).count(),0);
  assert(calls.some(c=>c.method==='thread/fork' && c.params.lastTurnId));
  // The sidebar action targets the selected row, not whichever chat is open.
  await menu(original,'回退到指定轮次');
  const select=page.getByRole('dialog').getByLabel('回退轮次');
  await select.locator('option').nth(2).waitFor({state:'attached'});
  const options=await select.locator('option').evaluateAll(nodes=>nodes.map(n=>({value:n.value,text:n.textContent})));
  assert.equal(options.length,3);
  await select.selectOption(options[1].value);
  await page.screenshot({path:'/tmp/codex-thread-rollback.png'});
  await confirm('回退到指定轮次');
  assert.equal(await page.evaluate(()=>localStorage.getItem('codex-agent.active')),fork);
  assert(calls.some(c=>c.method==='thread/revert' && c.params.threadId===original && c.params.beforeTurnId===options[1].value));
  await row(original).getByRole('button',{name:'lifecycle-one',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-slot="aui_user-message-root"]').length===1);
  await send('after-rollback');
  await menu(original,'回退上一轮'); await confirm('回退上一轮');
  await page.waitForFunction(()=>document.querySelectorAll('[data-slot="aui_user-message-root"]').length===1);
  await menu(original,'归档会话'); await confirm('归档会话');
  await row(original).waitFor({state:'hidden'});
  await page.getByRole('button',{name:'会话历史',exact:true}).click();
  await page.getByLabel('已归档',{exact:true}).check();
  const card=page.locator('.native-card').filter({hasText:original});
  await card.getByRole('button',{name:'取消归档',exact:true}).click(); await confirm('取消归档');
  // Return from the history page before interacting with the sidebar.
  await page.getByRole('button',{name:'返回聊天',exact:true}).click();
  await row(original).waitFor();
  await menu(fork,'删除会话'); await confirm('删除会话');
  await row(fork).waitFor({state:'hidden'});
  // Removal keeps chats visible under other conversations, including after refresh.
  const project=row(original).locator('xpath=ancestor::*[@data-project-id]').first();
  const pid=await project.getAttribute('data-project-id');
  await project.getByRole('button',{name:/项目菜单$/}).click();
  await page.getByRole('menuitem',{name:'移除项目',exact:true}).click(); await confirm('移除项目');
  await page.locator(`[data-project-id="${pid}"]`).waitFor({state:'hidden'});
  await row(original).waitFor();
  await page.reload();
  await page.waitForFunction(()=>document.body.textContent.includes('Fixture model'));
  assert.equal(await page.locator(`[data-project-id="${pid}"]`).count(),0);
  assert.equal(await row(fork).count(),0);
  await row(original).waitFor();
  assert.deepEqual(errors,[]);
  await page.screenshot({path:'/tmp/codex-thread-lifecycle.png'});
  console.log('Lifecycle browser passed: per-turn fork, sidebar target identity, arbitrary/last rollback, continue, archive/unarchive, delete, project removal and reload.');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});

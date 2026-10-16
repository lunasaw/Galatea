'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('../frontend/node_modules/playwright');
(async () => {
 const browser = await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE,args:['--no-sandbox']});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.env.CONSOLE_TEST_URL);
  await page.locator('#text').fill('approval-fixture');await page.locator('#send').click();
  await page.getByRole('button',{name:'允许本次',exact:true}).waitFor();
  const dir=process.env.CONSOLE_SCREENSHOTS;
  if(dir){fs.mkdirSync(dir,{recursive:true});await page.screenshot({path:dir+'/approval.png'});}
  await page.getByRole('button',{name:'允许本次',exact:true}).click();
  await page.getByRole('button',{name:'测试目录',exact:true}).click();
  await page.getByRole('button',{name:'提交回答',exact:true}).click();
  await page.getByRole('textbox',{name:'name',exact:true}).fill('fixture');
  await page.getByRole('button',{name:'确认提交',exact:true}).click();
  await page.locator('[data-slot=aui_message-group]').getByText('你好，完整回复。',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('等待回应的请求').count(),0);
  await page.getByRole('button',{name:'设置',exact:true}).click();
  await page.getByLabel('模型',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('模型',{exact:true}).inputValue(),'fixture-model');
  if(dir){await page.waitForTimeout(400);await page.screenshot({path:dir+'/workbench.png'});}
  await page.getByRole('dialog').getByRole('button',{name:'Skills',exact:true}).click();
  await page.getByText('fixture-skill',{exact:true}).waitFor();
  await page.getByRole('button',{name:'终端',exact:true}).click();
  await page.getByLabel('Shell 命令').fill('printf fixture-command-output');
  await page.getByRole('button',{name:'执行命令',exact:true}).click();
  await page.locator('.native-terminal').getByText('fixture-command-output',{exact:true}).waitFor();
  await page.getByRole('button',{name:'文件',exact:true}).click();
  await page.getByLabel('文件或目录的绝对路径').fill('/fixture/file');
  await page.getByRole('button',{name:'读取文件',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.native-editor').value==='fixture-file');
  await page.getByRole('button',{name:'开发者工具',exact:true}).click();
  assert.ok(await page.getByLabel('原生方法',{exact:true}).locator('option').count()>140);
  await page.getByLabel('原生方法',{exact:true}).selectOption('thread/read');
  assert.match(await page.getByLabel('原生方法参数 JSON').inputValue(),/threadId/);
  await page.getByRole('button',{name:'关闭详情',exact:true}).click();
  await page.locator('#text').fill('/skills');await page.locator('#send').click();
  await page.getByText('fixture-skill',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  if(dir){await page.waitForTimeout(400);await page.screenshot({path:dir+'/mobile-workbench.png'});}
  await page.getByRole('button',{name:'关闭详情',exact:true}).click();
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button',{name:'添加图片',exact:true}).click();
  await (await chooser).setFiles({name:'pixel.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6QmcAAAAASUVORK5CYII=','base64')});
  await page.locator('#text').fill('image fixture');
  const request=page.waitForRequest(r=>r.url().endsWith('/messages') && r.method()==='POST');
  await page.locator('#send').click();
  assert.ok((await request).postDataJSON().input_items.some(p=>p.type==='image'&&p.url.startsWith('data:image/png;base64,')));
  assert.deepEqual(errors,[]);
  console.log('Native browser: approvals, user questions, MCP form, settings, skills, terminal, files, developer tools, slash commands, image upload and mobile passed.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

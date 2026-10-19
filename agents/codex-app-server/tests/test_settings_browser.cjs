'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
 const browser = await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try {
  const page = await browser.newPage({viewport:{width:1440,height:1080}});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  page.on('console',message=>{if(message.type()==='error'&&!message.text().startsWith('Failed to load resource:'))errors.push(message.text());});
  let version=1, editable=true, failNext=false;
  const config={model:'fixture-model',model_reasoning_effort:'medium',web_search:'cached',approval_policy:'on-request',sandbox_mode:'read-only',model_verbosity:'high',cli_auth_credentials_store:'[redacted:credential]',developer_instructions:'[redacted:private_content]',model_providers:{private:{name:'Private',api_key:'[redacted:credential]'}}};
  const user={type:'user',file:'/fixture/config.toml'}, project={type:'project',dotCodexFolder:'/fixture/.codex'};
  const writes=[],reads=[];
  await page.route('**/api/native/protocol',async route=>{const response=await route.fetch();const data=await response.json();await route.fulfill({response,json:{...data,configuration_editable:editable}});});
  await page.route('**/api/native/call',async route=>{
   const {method,params}=route.request().postDataJSON();
   if(method==='config/read'){reads.push(params);return route.fulfill({json:{config:{...config,model_verbosity:'low'},origins:{model_verbosity:{name:project,version:'p1'},cli_auth_credentials_store:'[redacted:credential]',developer_instructions:'[redacted:private_content]',model_provider:{}},...(params.includeLayers?{layers:[{name:project,version:'p1',config:{model_verbosity:'low'}},{name:user,version:'v'+version,config:structuredClone(config)}]}:{})}});}
   if(method==='configRequirements/read')return route.fulfill({json:{requirements:{allowedSandboxModes:['read-only','workspace-write'],allowedWebSearchModes:['cached','live'],allowedApprovalPolicies:['on-request','never']}}});
   if(method==='config/batchWrite'){
    writes.push(params);assert.equal(params.filePath,user.file);
    if(failNext||params.expectedVersion!=='v'+version){failNext=false;return route.fulfill({status:422,json:{error:'ConfigVersionConflict'}});}
    for(const edit of params.edits){const parts=edit.keyPath.split('.');let parent=config;for(const part of parts.slice(0,-1))parent=parent[part]||={};if(edit.value===null)delete parent[parts.at(-1)];else parent[parts.at(-1)]=edit.value;}
    version++;return route.fulfill({json:{status:'okOverridden',version:'v'+version,filePath:user.file,overriddenMetadata:{name:project}}});
   }
   return route.continue();
  });
  const control=name=>page.getByLabel(name,{exact:true});
  const button=name=>page.getByRole('button',{name,exact:true});
  const open=async(wait=true)=>{if(page.viewportSize().width<768)await button('切换侧栏').click();await button('设置').click();await page.getByRole('navigation',{name:'设置分类'}).getByRole('button',{name:'配置',exact:true}).click();await control('输出详细程度').waitFor();if(wait)await page.waitForFunction(()=>!document.querySelector('.configuration-settings fieldset')?.disabled);};
  const saved=async()=>{await page.getByText('已保存；部分值被更高优先级配置覆盖，请查看当前生效值。',{exact:true}).waitFor();await page.waitForFunction(()=>!document.querySelector('.configuration-settings fieldset')?.disabled);};
  await page.goto(process.env.CONSOLE_TEST_URL);await open();
  assert.equal(await control('输出详细程度').inputValue(),'high');
  assert.match(await page.locator('.configuration-settings').innerText(),/当前生效：低.*项目配置/);
  const authSetting=page.locator('.complete-config-settings > form > fieldset > details').filter({has:page.getByRole('checkbox',{name:'配置 cli_auth_credentials_store',exact:true,includeHidden:true})});
  await authSetting.locator(':scope > summary').click();
  await authSetting.getByText('当前生效值 · 来源信息已隐藏',{exact:true}).waitFor();
  await authSetting.locator(':scope > summary').click();
  assert.equal(await page.getByText('内容加载失败。',{exact:false}).count(),0);
  assert.equal(await control('沙盒设置').locator('option').filter({hasText:'完全访问'}).isDisabled(),true);
  assert.ok(reads.some(p=>p.includeLayers&&p.cwd));
  await control('输出详细程度').selectOption('medium');await control('推理摘要').selectOption('detailed');await page.getByLabel('同时请求原生重载').check();await button('保存默认配置').click();await saved();
  assert.equal(writes[0].expectedVersion,'v1');assert.equal(writes[0].reloadUserConfig,true);assert.deepEqual(writes[0].edits.map(e=>e.keyPath),['model_verbosity','model_reasoning_summary']);
  assert.equal(config.developer_instructions,'[redacted:private_content]');assert.equal(config.model_providers.private.api_key,'[redacted:credential]');assert.equal(config.cli_auth_credentials_store,'[redacted:credential]');
  await control('输出详细程度').selectOption('');await button('保存默认配置').click();await saved();assert.equal(writes[1].edits[0].value,null);assert.ok(!('model_verbosity' in config));
  failNext=true;await control('网页搜索').selectOption('live');await button('保存默认配置').click();await page.getByRole('alert').filter({hasText:'ConfigVersionConflict'}).waitFor();assert.equal(config.web_search,'cached');assert.equal(await control('网页搜索').inputValue(),'live');
  await button('放弃修改并重新读取').click();await page.waitForFunction(()=>!document.querySelector('.configuration-settings fieldset')?.disabled);assert.equal(await control('网页搜索').inputValue(),'cached');
  await control('配置来源').selectOption('/fixture/.codex/config.toml');assert.equal(await control('输出详细程度').isDisabled(),true);assert.equal(await control('输出详细程度').inputValue(),'low');
  await control('配置来源').selectOption(user.file);
  await button('打开 config.toml ↗').click();await page.locator('.configuration-file').getByText('fixture-file',{exact:true}).waitFor();await button('关闭配置文件').click();
  await page.getByText('模型与运行设置',{exact:true}).click();await control('上下文窗口').fill('120000');await control('计划模式推理强度').selectOption('high');
  await page.getByText('沙箱与命令环境',{exact:true}).click();await control('额外可写目录').fill('/workspace/output');await control('允许命令访问网络').selectOption('false');
  await page.getByText('服务商配置',{exact:true}).click();await button('保存默认配置').click();await saved();
  await page.getByText('服务商配置',{exact:true}).click();await control('编辑服务商 ID').fill('private');await control('服务商名称').fill('Updated private');await button('保存默认配置').click();await saved();
  assert.equal(config.model_context_window,120000);assert.equal(config.plan_mode_reasoning_effort,'high');assert.deepEqual(config.sandbox_workspace_write.writable_roots,['/workspace/output']);
  assert.equal(config.model_providers.private.name,'Updated private');assert.equal(config.model_providers.private.api_key,'[redacted:credential]');
  await control('配置来源').selectOption(user.file);await page.locator('.configuration-efforts > summary').click();await control('显示低推理强度').uncheck();assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('codex-agent.hidden-efforts'))),['low']);
  await page.reload();await open();assert.equal(await control('推理摘要').inputValue(),'detailed');await page.locator('.configuration-efforts > summary').click();assert.equal(await control('显示低推理强度').isChecked(),false);await page.locator('.configuration-efforts > summary').click();
  await page.locator('.configuration-settings h3').scrollIntoViewIfNeeded();
  const shots=process.env.CONSOLE_SCREENSHOTS;if(shots){fs.mkdirSync(shots,{recursive:true});await page.screenshot({path:shots+'/settings-desktop.png',fullPage:true});}
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);if(shots)await page.screenshot({path:shots+'/settings-mobile.png',fullPage:true});
  await page.evaluate(()=>localStorage.setItem('codex-agent.theme','dark'));await page.reload();await open();if(shots)await page.screenshot({path:shots+'/settings-dark.png',fullPage:true});
  editable=false;await page.setViewportSize({width:1440,height:1080});await page.reload();await open(false);await page.getByText('此运行实例的发布配置为只读。',{exact:true}).waitFor();assert.equal(await control('输出详细程度').isDisabled(),true);assert.deepEqual(errors,[]);
  console.log('Settings browser passed: save/reset, versions, overrides, conflict preservation, refresh, visibility preferences, readonly, mobile.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

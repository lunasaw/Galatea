'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');
(async () => {
 const browser = await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE,args:['--no-sandbox']});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1080}}), errors=[], writes=[], calls=[];
  page.on('pageerror',e=>errors.push(e.message));
  let version=1, handoff=false, attachments=[], revision='r1';
  const config={permissions:{'custom.with.dot':{description:'Before',extends:'read-only',filesystem:{'/private':'none'}}},model_providers:{private:{name:'Private',api_key:'[redacted:credential]'}}};
  await page.route('**/api/native/auth-handoffs',r=>r.fulfill({json:handoff?[{providerId:'gateway',authUrl:'https://gateway.example/authorize?state=once'}]:[]}));
  await page.route('**/api/native/call',async route=>{
   const {method,params}=route.request().postDataJSON();calls.push({method,params});
   if(method==='config/read')return route.fulfill({json:{config:structuredClone(config),origins:{},layers:[{name:{type:'user',file:'/fixture/config.toml'},version:'v'+version,config:structuredClone(config)}]}});
   if(method==='config/batchWrite') {writes.push(params);return route.fulfill(params.expectedVersion==='v'+version?{json:{status:'ok',version:'v'+(++version),filePath:'/fixture/config.toml'}}:{status:422,json:{error:'ConfigVersionConflict'}});}
   if(method==='account/login/start')return route.fulfill({json:{type:'chatgptDeviceCode',loginId:'device',verificationUrl:'https://auth.example/device',userCode:'ABCD-EFGH'}});
   if(method==='account/login/cancel')return route.fulfill({json:{status:'canceled'}});
   if(method==='account/gatewayOAuth/read')return route.fulfill({json:{providerId:'gateway',providerName:'Gateway',required:true,status:handoff?'started':'notReady',error:null}});
   if(method==='account/gatewayOAuth/login'){handoff=true;return route.fulfill({json:{}});}
   if(method==='account/gatewayOAuth/cancel'){handoff=false;return route.fulfill({json:{}});}
   if(method==='thread/read') {const response=await route.fetch(), data=await response.json();data.readState={revision,firstUnread:null};return route.fulfill({response,json:data});}
   if(method==='thread/readState/update'){assert.equal(params.expectedRevision,revision);revision='r2';return route.fulfill({json:{readState:{revision,firstUnread:null}}});}
   if(method==='thread/attachment/list')return route.fulfill({json:{data:attachments,nextCursor:null}});
   if(method==='thread/attachment/add'){attachments=[{id:'attachment-1',...params}];return route.fulfill({json:{attachment:attachments[0]}});}
   if(method==='thread/attachment/remove'){attachments=[];return route.fulfill({json:{}});}
   return route.continue();
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const category=async name=>page.getByRole('navigation',{name:'设置分类'}).getByRole('button',{name,exact:true}).click();
  await page.goto(process.env.CONSOLE_TEST_URL);
  await page.locator('#text').fill('native-completion-fixture');await page.locator('#send').click();
  await page.getByText('你好，完整回复。',{exact:true}).waitFor();
  await button('设置').click();await category('配置');
  const complete=page.locator('.complete-config-settings');
  await complete.getByText(/^permissions · 已配置$/).click();
  await complete.getByLabel('description',{exact:true}).fill('After');
  version=2;await page.evaluate(()=>window.dispatchEvent(new Event('codex-native-resources-changed')));
  await page.waitForResponse(async r=>r.url().endsWith('/api/native/call')&&r.request().postDataJSON().method==='config/read');
  await complete.getByRole('button',{name:'保存原生设置',exact:true}).click();
  await complete.getByRole('alert').filter({hasText:'ConfigVersionConflict'}).waitFor();
  assert.equal(writes[0].expectedVersion,'v1');assert.deepEqual(writes[0].edits,[{keyPath:'permissions."custom.with.dot".description',value:'After',mergeStrategy:'replace'}]);
  assert.equal(await complete.getByLabel('description',{exact:true}).inputValue(),'After');
  assert.equal(config.model_providers.private.api_key,'[redacted:credential]');
  await complete.getByRole('button',{name:'放弃修改并刷新',exact:true}).click();
  await category('账户与登录');
  await page.getByLabel('登录账户类型',{exact:true}).selectOption({label:'ChatGPT（设备码）'});
  await button('登录账户').click();await page.getByRole('link',{name:'打开登录页面 ↗',exact:true}).waitFor();
  assert.equal(await page.getByRole('link',{name:'打开登录页面 ↗',exact:true}).getAttribute('href'),'https://auth.example/device');
  await page.locator('strong').getByText('ABCD-EFGH',{exact:true}).waitFor();await button('取消登录').click();
  await page.getByRole('link',{name:'打开登录页面 ↗',exact:true}).waitFor({state:'hidden'});
  await page.getByText('Gateway OAuth',{exact:true}).click();await button('登录 Gateway').click();
  await page.getByRole('link',{name:'打开 Gateway 登录页面 ↗',exact:true}).waitFor();await button('取消 Gateway 登录').click();
  await page.getByRole('link',{name:'打开 Gateway 登录页面 ↗',exact:true}).waitFor({state:'hidden'});
  await button('返回聊天').click();await page.locator('#text').fill('/resources');await page.locator('#text').press('Enter');await page.getByRole('dialog',{name:'聊天资源',exact:true}).waitFor();await button('标记已读').click();await page.getByText('阅读状态已更新',{exact:true}).waitFor();
  await page.getByLabel('attachmentType',{exact:true}).fill('reference');await page.getByLabel('identityKey',{exact:true}).fill('issue:123');
  await page.getByLabel('payload',{exact:true}).fill('{"title":"Persistent resource"}');await button('添加持久附件').click();
  await page.getByText('reference · issue:123',{exact:true}).waitFor();await button('移除附件').click();
  await page.getByText('reference · issue:123',{exact:true}).waitFor({state:'hidden'});
  assert.ok(calls.some(c=>c.method==='account/login/start'&&c.params.type==='chatgptDeviceCode'));
  assert.ok(calls.some(c=>c.method==='thread/readState/update'&&c.params.operation.type==='read'));
  assert.deepEqual(errors,[]);
  console.log('Native completion browser: nested named permissions, pinned version conflict, device/Gateway login cancellation, read state, persistent attachments passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('../frontend/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_EXECUTABLE, args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    const errors=[];
    page.on('pageerror', e=>errors.push(e.message));
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.locator('#text').fill('parity-fixture');
    await page.locator('#send').click();
    await page.getByText('验收完成。',{exact:true}).waitFor();
    await page.waitForFunction(()=>document.querySelector('.activity-trigger')?.getAttribute('aria-expanded') === 'false');
    assert.equal(await page.locator('[data-slot=aui_assistant-message-footer]').count(),1);
    await page.locator('.activity-trigger').first().click();
    await page.getByText('正在检查命令与文件。',{exact:true}).waitFor();
    const command=page.locator('.native-activity').filter({hasText:'printf parity-output'});
    await command.locator('summary').first().click();
    await command.getByText('退出码 0 · 0.1 秒',{exact:true}).waitFor();
    assert.match(await command.locator('pre').innerText(),/parity-output/);
    const failure=page.locator('.native-activity').filter({hasText:'exit 1'});
    assert.equal(await failure.getAttribute('data-state'),'failed');
    const plan=page.locator('.native-activity').filter({hasText:'更新计划'});
    await plan.locator('summary').first().click();
    assert.equal(await plan.locator('li[data-status=completed]').innerText(),'读取配置');
    const patch=page.locator('.native-activity').filter({hasText:'修改 1 个文件'});
    await patch.locator('summary').first().click();
    assert.match(await patch.locator('[data-change=add]').innerText(),/\+new/);
    assert.match(await patch.locator('[data-change=remove]').innerText(),/-old/);
    const request=page.waitForRequest(r=>r.url().endsWith('/api/native/call') && r.postDataJSON()?.method==='fs/readFile');
    await page.getByRole('button',{name:'查看源码',exact:true}).click();
    assert.equal((await request).postDataJSON().params.path,'/fixture/file.py');
    await page.getByRole('dialog').getByText('fixture-file',{exact:true}).waitFor();
    await page.keyboard.press('Escape');
    await page.reload();
    await page.getByText('验收完成。',{exact:true}).waitFor();
    assert.equal(await page.locator('.activity-trigger').first().getAttribute('aria-expanded'),'false');
    for(const [name,width,height,dark] of [['desktop',1440,960,false],['dark',1440,960,true],['mobile',390,844,true]]) {
      await page.setViewportSize({width,height});
      if(dark && name==='dark') await page.getByRole('button',{name:'切换深色主题',exact:true}).click();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth > innerWidth),false);
      if(process.env.CONSOLE_SCREENSHOTS) {
        fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS,{recursive:true});
        await page.screenshot({path:`${process.env.CONSOLE_SCREENSHOTS}/${name}.png`});
      }
    }
    assert.deepEqual(errors,[]);
    console.log('Conversation parity: activity collapse, final actions, commands/exit codes, failure, plan, diff, file preview, replay, themes and mobile passed.');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});

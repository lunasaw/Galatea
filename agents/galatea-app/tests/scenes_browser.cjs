const assert = require("node:assert/strict");
const { chromium } = require("../frontend/node_modules/playwright");
(async () => {
  const b = await chromium.launch({
    executablePath:
      process.env.CHROMIUM_EXECUTABLE ||
      "/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
    args: ["--no-sandbox"],
  });
  try {
    const p = await b.newPage({ viewport: { width: 1440, height: 1050 } });
    await p.goto(process.env.GALATEA_TEST_URL || "http://127.0.0.1:18887");
    const a = p.frameLocator("iframe");
    await a
      .locator("#notice")
      .filter({ hasText: /模拟数据/ })
      .waitFor();
    const scenes = [
      "Skill 数据处理",
      "编写代码",
      "确认训练",
      "训练过程",
      "验收结果",
      "两阶段数据任务",
      "动态重规划",
    ];
    const fs = require("node:fs");
    const out = process.env.GALATEA_SCREENSHOTS || "/tmp/galatea-scenes";
    fs.mkdirSync(out, { recursive: true });
    const projects = await p.evaluate(async () => {
      const r = await fetch('/preview/tool', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:'list_projects',arguments:{}})});
      return (await r.json()).structuredContent.items;
    });
    for (const name of scenes) {
      const project = projects.find(item => item.name.includes(name));
      assert.ok(project, name);
      const url = new URL(process.env.GALATEA_TEST_URL || 'http://127.0.0.1:18887');
      url.searchParams.set('task_id', project.task_sessions[0].id);
      await p.goto(url.href);
      await a.locator('#task-goal').filter({hasText: project.task_sessions[0].goal}).waitFor();
      if (name === "两阶段数据任务") {
        assert.equal(await a.locator(".stage").count(), 2);
        assert.equal(await a.locator(".proposal").count(), 0);
      }
      if (name === "动态重规划")
        assert.equal(await a.locator(".stage").count(), 3);
      if (name === "确认训练") {
        await a.getByRole("button", { name: "调整方案", exact: true }).click();
        await a.locator('#notice').filter({hasText: '请在当前聊天输入调整要求'}).waitFor();
        assert.equal(await a.locator('#draft').count(), 0);
        assert.equal(
          await a
            .getByRole("button", { name: "确认本次执行并训练", exact: true })
            .isDisabled(),
          true,
        );
      }
      await p.screenshot({
        path: out + "/" + scenes.indexOf(name) + ".png",
        fullPage: true,
      });
    }
    console.log(
      JSON.stringify({ passed: true, scenes: scenes.length, screenshots: out }),
    );
  } finally {
    await b.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

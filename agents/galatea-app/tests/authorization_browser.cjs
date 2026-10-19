const assert = require("node:assert/strict");
const { chromium } = require("../../codex-app-server/frontend/node_modules/playwright");

(async () => {
  const insecureOrigin = process.env.GALATEA_INSECURE_ORIGIN === "1";
  const baseUrl = process.env.GALATEA_SIGNED_URL ||
    (insecureOrigin ? "http://galatea-http.test:18920" : "http://127.0.0.1:18890");
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE ||
      "/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
    args: ["--no-sandbox", ...(insecureOrigin
      ? ["--host-resolver-rules=MAP galatea-http.test 127.0.0.1", "--no-proxy-server"] : [])],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const failures = [];
    page.on("pageerror", e => failures.push(e.message));
    await page.goto(baseUrl);
    const schedulingOnly = process.env.GALATEA_MEMORY_MODE === "scheduling_only";
    await page.locator("#text").fill(schedulingOnly
      ? "training-scheduling-only-fixture" : "training-authorization-fixture");
    await page.locator("#send").click();
    const panel = page.getByRole("tabpanel", { name: "Apps面板", exact: true });
    const app = panel.frameLocator("iframe");
    await app.locator("#task-status").filter({ hasText: "等待授权" }).waitFor();
    const authorize = app.getByRole("button", { name: "授权本次方案", exact: true });
    const confirm = app.getByRole("button", { name: "确认本次执行并训练", exact: true });
    assert.equal(await authorize.isEnabled(), true);
    assert.equal(await confirm.isDisabled(), true);
    if (insecureOrigin) {
      const context = await authorize.evaluate(() => ({
        secure: window.isSecureContext, uuid: typeof crypto.randomUUID,
      }));
      assert.deepEqual(context, { secure: false, uuid: "undefined" });
    }
    if (schedulingOnly)
      await app.getByText("600 秒 · 未设置作业内存硬上限（仅调度预留）", { exact: true }).waitFor();
    // Denial must leave both the durable grant and execution untouched.
    await authorize.click();
    await page.getByRole("button", { name: "拒绝", exact: true }).click({ timeout: 10000 });
    await app.locator("#notice").filter({ hasText: "用户未允许" }).waitFor();
    assert.equal(await confirm.isDisabled(), true);
    await authorize.click();
    await page.getByRole("button", { name: "允许本次", exact: true }).click();
    await app.locator("#task-status").filter({ hasText: "等待你的决定" }).waitFor();
    await app.getByText("本次方案已有授权；确认执行后才会开始训练。", { exact: true }).waitFor();
    assert.equal(await confirm.isEnabled(), true);
    assert.equal(await app.locator("#detail-stage").textContent().then(s => s.includes("尚未开始")), true);
    await page.reload();
    await app.locator("#task-status").filter({ hasText: "等待你的决定" }).waitFor();
    assert.equal(await confirm.isEnabled(), true);
    // A separate, signed confirmation is required even after the grant survived reload.
    await confirm.click();
    await page.getByRole("button", { name: "拒绝", exact: true }).click();
    await app.locator("#notice").filter({ hasText: "用户未允许" }).waitFor();
    assert.equal(await app.locator("#task-status").textContent(), "等待你的决定");
    const out = process.env.GALATEA_SCREENSHOTS || "/tmp/galatea-authorization";
    require("node:fs").mkdirSync(out, { recursive: true });
    await page.screenshot({ path: out + "/authorized-awaiting-execution.png", fullPage: true });
    assert.deepEqual(failures, []);
    console.log(JSON.stringify({ passed: true, insecureOrigin,
      scope: "real Host signatures, grant denial/acceptance, reload, separate execution denial; no training" }));
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });

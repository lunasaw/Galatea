const assert = require("node:assert/strict");
const {
  chromium,
} = require("../../codex-app-server/frontend/node_modules/playwright");
(async () => {
  const browser = await chromium.launch({
    executablePath:
      process.env.CHROMIUM_EXECUTABLE ||
      "/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
    args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1100 },
    });
    const failures = [];
    page.on("pageerror", (e) => failures.push(e.message));
    await page.goto(process.env.GALATEA_SIGNED_URL || "http://127.0.0.1:18890");
    await page.locator("#text").fill("conversation-task-fixture");
    await page.locator("#send").click();
    const panel = page.getByRole("tabpanel", { name: "Apps面板", exact: true });
    const app = panel.frameLocator("iframe");
    await app.locator("#task-goal").filter({ hasText: "检查这份 CSV" }).waitFor();
    const session = new URL(page.url()).searchParams.get("session");
    assert.ok(session && !session.startsWith("app-activation_"));
    assert.equal(await app.locator("#launch-form").count(), 0);
    assert.equal(await app.locator("#projects").count(), 0);
    assert.equal(await app.locator("#draft").count(), 0);
    const original = await panel.locator("iframe").elementHandle();
    await app.locator("#task-status").filter({ hasText: "已完成" }).waitFor({ timeout: 20000 });
    await app.locator(".artifact").first().click();
    await app.locator("#artifact-preview").getByText(/"rows": 2/).waitFor();
    assert.equal(await original.evaluate(el => el.isConnected), true);
    assert.equal(new URL(page.url()).searchParams.get("session"), session);
    // Read traffic also enters the public timeline; exercise recovery after the
    // original presentation has left the default history page.
    const snapshot = await page.evaluate(async session => {
      for (let index = 0; index < 210; index++) await fetch('/api/console/status');
      return (await fetch('/api/sessions/' + session + '/snapshot?limit=1')).json();
    }, session);
    assert.equal(snapshot.app_step.detail.tool, "start_task");
    await page.reload();
    await panel.frameLocator("iframe").locator("#task-status").filter({ hasText: "已完成" }).waitFor();
    const out = process.env.GALATEA_SCREENSHOTS || "/tmp/galatea-conversation";
    require("node:fs").mkdirSync(out, { recursive: true });
    await page.screenshot({ path: out + "/conversation-task.png", fullPage: true });
    assert.deepEqual(failures, []);
    console.log(
      JSON.stringify({
        passed: true,
        checks: 12,
        scope:
          "same-chat task, automatic native App display, live progress, verified artifact, Host signatures and paginated reload; synthetic model",
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

const assert = require("node:assert/strict");
const {
  chromium,
} = require("../../codex-app-server/frontend/node_modules/playwright");
const fs = require("node:fs");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch({
    executablePath:
      process.env.CHROMIUM_EXECUTABLE ||
      "/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1050 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await page.goto(process.env.GALATEA_TEST_URL || "http://127.0.0.1:18887");
    const app = page.frameLocator("iframe");
    await app.locator("#task-goal").waitFor();
    await app
      .locator("#notice")
      .filter({ hasText: /真实平台证据|模拟数据/ })
      .waitFor();
    assert.equal(await app.locator(".stage").count(), 3);
    assert.equal(await app.locator("#launch-form, #projects, #draft").count(), 0);
    await app.locator(".stage").nth(1).click();
    await app.locator(".file").first().click();
    await app.locator(".diff-line").first().waitFor();
    assert.ok((await app.locator(".diff-line").count()) > 2);
    await app.getByRole("tab", { name: "日志", exact: true }).click();
    await app
      .getByText("当前接口未提供原始训练日志", { exact: true })
      .waitFor();
    await app.getByRole("tab", { name: "项目绑定", exact: true }).click();
    await app.getByText("Agent 会话", { exact: true }).waitFor();
    await app.locator("#toggle-details").click();
    assert.equal(await app.locator("#details").isVisible(), false);
    await app.locator("#toggle-details").click();
    assert.equal(
      await app
        .getByRole("tab", { name: "项目绑定", exact: true })
        .getAttribute("aria-selected"),
      "true",
    );
    await app.locator(".stage").last().click();
    await app.locator(".artifact").first().click();
    await app.locator("#artifact-preview pre").waitFor();
    assert.ok(
      (await app.locator("#artifact-preview pre").textContent()).length > 10,
    );
    const out =
      process.env.GALATEA_SCREENSHOTS || "/tmp/galatea-workbench-screenshots";
    fs.mkdirSync(out, { recursive: true });
    await page.screenshot({
      path: path.join(out, "desktop.png"),
      fullPage: true,
    });
    for (const width of [850, 580, 390]) {
      await page.setViewportSize({ width, height: 1050 });
      await page.screenshot({
        path: path.join(out, `width-${width}.png`),
        fullPage: true,
      });
      const frame = page.frames()[1];
      assert.equal(
        await frame.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
        true,
        `horizontal overflow ${width}`,
      );
    }
    await page.emulateMedia({ colorScheme: "dark" });
    await page.screenshot({ path: path.join(out, "dark.png"), fullPage: true });
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, checks: 12, screenshots: out }));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

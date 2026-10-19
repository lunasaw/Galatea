const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { chromium } = require("../../../frontend/node_modules/playwright");
const { build } = require("../../../frontend/node_modules/esbuild");

(async () => {
  const root = path.resolve(__dirname, "..");
  const appHtml = readFileSync(path.join(root, "dist/app.html"), "utf8");
  const harness = await build({
    stdin: {
      contents: `
        import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";
        const frame = document.querySelector("iframe");
        const bridge = new AppBridge(null, {name: "Test Host", version: "1"}, {serverTools: {}},
          {hostContext: {theme: "light", displayMode: "inline"}});
        window.bridge = bridge;
        window.calls = [];
        bridge.oncalltool = async args => {
          window.calls.push(args);
          return {content: [{type: "text", text: "Hello, " + args.arguments.name + "!"}],
            structuredContent: {name: args.arguments.name}};
        };
        bridge.oninitialized = async () => {
          await bridge.sendToolInput({arguments: {}});
          await bridge.sendToolResult({content: [{type: "text", text: "Hello, World!"}],
            structuredContent: {name: "World", message: "Hello, World!"}});
        };
        await bridge.connect(new PostMessageTransport(frame.contentWindow, frame.contentWindow));
        frame.src = "/app";
      `,
      resolveDir: path.resolve(root, "../../frontend"),
    },
    bundle: true, write: false, format: "esm", minify: true,
  });
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE, args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 840, height: 620 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("https://hello.test/**", route => {
      if (new URL(route.request().url()).pathname === "/app") {
        return route.fulfill({ contentType: "text/html", body: appHtml, headers: {
          "Content-Security-Policy": "default-src 'none'; sandbox allow-scripts; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'",
        } });
      }
      return route.fulfill({ contentType: "text/html", body:
        `<iframe sandbox="allow-scripts" style="border:0;width:100%;height:580px"></iframe>
        <script type="module">${harness.outputFiles[0].text.replace(/<\/script/gi, "<\\/script")}</script>` });
    });
    await page.goto("https://hello.test/");
    const app = page.frameLocator("iframe");
    await app.locator("#status").filter({ hasText: "已绘制" }).waitFor();
    assert.equal(await app.locator("#greeting").textContent(), "Hello, World!");
    if (process.env.HELLO_SCREENSHOT) await page.screenshot({ path: process.env.HELLO_SCREENSHOT });
    await app.locator("#name").fill("世界");
    await app.locator("#draw").click();
    await app.locator("#greeting").filter({ hasText: "Hello, 世界!" }).waitFor();
    assert.equal(await page.evaluate(() => window.calls.at(-1).name), "draw_hello_world");
    await app.locator("#name").fill("世".repeat(40));
    await app.locator("#name").press("Enter");
    await app.locator("#greeting").filter({ hasText: "世".repeat(40) }).waitFor();
    assert.ok(await app.locator("#greeting").evaluate(element => element.getComputedTextLength() <= 640));
    await app.locator("#name").fill("<img src=x onerror=alert(1)>");
    await app.locator("#draw").click();
    await app.locator("#greeting").filter({ hasText: "<img" }).waitFor();
    assert.equal(await app.locator("img").count(), 0);
    await page.evaluate(() => window.bridge.setHostContext({ theme: "dark", displayMode: "inline" }));
    await app.locator('html[data-theme="dark"]').waitFor();
    await page.setViewportSize({ width: 390, height: 620 });
    const frame = page.frames().find(frame => frame.url().endsWith("/app"));
    assert.ok(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.equal(await frame.evaluate(() => { try { return !!parent.document; } catch { return false; } }), false);
    await page.goto("https://hello.test/app");
    await page.locator("#status").filter({ hasText: "本地预览" }).waitFor();
    await page.locator("#name").fill("Local");
    await page.locator("#draw").click();
    assert.equal(await page.locator("#greeting").textContent(), "Hello, Local!");
    assert.deepEqual(errors, []);
    console.log("Hello World: SDK handshake, tool call, theme, escaping, mobile and preview passed.");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

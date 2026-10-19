const { test } = require("node:test");
const assert = require("node:assert/strict");
const { mkdtemp, readFile, rm } = require("node:fs/promises");
const { createServer } = require("node:http");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { build } = require("esbuild");
const { chromium } = require("playwright");

test("renders local KaTeX fonts and dynamic layout under the console CSP", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "codex-math-browser-"));
  let server, browser;
  try {
    await build({
      stdin: {
        contents: `import React from 'react';
          import { createRoot } from 'react-dom/client';
          import { StaticMarkdown } from './src/components/assistant-ui/elements/markdown-text';
          window.violations = [];
          document.addEventListener('securitypolicyviolation', e => window.violations.push(e.violatedDirective));
          createRoot(document.getElementById('root')).render(React.createElement(StaticMarkdown, {
            text: ${JSON.stringify("Inline $x^2$.\n\n$$\\frac{1}{2}+\\sqrt{x}+\\sum_{i=1}^{n}i$$\n\n\\[a+b\\]\n\nLiteral `$x$` and $5 and $10.")}
          }));`,
        resolveDir: path.resolve(__dirname, ".."),
        loader: "tsx",
      },
      outfile: path.join(directory, "app.js"),
      bundle: true,
      jsx: "automatic",
      loader: { ".woff2": "file" },
      assetNames: "math-fonts/[name]",
      plugins: [{
        name: "katex-csp-html-parser",
        setup(build) {
          build.onLoad({ filter: /hast-util-from-html-isomorphic\/lib\/browser\.js$/ }, () => ({
            contents: 'export { fromHtml as fromHtmlIsomorphic } from "hast-util-from-html";',
            loader: "js",
            resolveDir: path.resolve(__dirname, "../node_modules/hast-util-from-html-isomorphic"),
          }));
        },
      }, {
        name: "katex-local-fonts",
        setup(build) {
          build.onLoad({ filter: /katex\/dist\/katex\.min\.css$/ }, async ({ path: file }) => ({
            contents: (await readFile(file, "utf8")).replace(/,url\([^)]*\.(?:woff|ttf)\) format\("[^"]+"\)/g, ""),
            loader: "css", resolveDir: path.dirname(file),
          }));
        },
      }],
    });
    server = createServer(async (request, response) => {
      response.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob: https:; media-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'");
      const url = new URL(request.url, "http://localhost");
      if (url.pathname === "/") {
        response.setHeader("Content-Type", "text/html; charset=utf-8");
        response.end('<!doctype html><html><head><link rel="stylesheet" href="/app.css"></head><body><div id="root"></div><script src="/app.js"></script></body></html>');
        return;
      }
      if (!/^\/(?:app\.(?:js|css)|math-fonts\/KaTeX_[\w-]+\.woff2)$/.test(url.pathname)) {
        response.writeHead(404).end(); return;
      }
      response.setHeader("Content-Type", url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "font/woff2");
      response.end(await readFile(path.join(directory, url.pathname)));
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    browser = await chromium.launch({ headless: true,
      ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}),
      args: ["--no-sandbox"] });
    const page = await browser.newPage();
    const errors = [];
    const fonts = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("response", (response) => {
      if (response.url().endsWith(".woff2")) fonts.push(response.status());
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForSelector(".katex-display .frac-line");
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.locator(".katex").count(), 3);
    assert.equal(await page.locator(".katex-display").count(), 2);
    assert.equal(await page.locator(".katex [style]").count(), 0);
    assert.equal(await page.locator("code").textContent(), "$x$");
    const layout = await page.locator(".frac-line").evaluate((node) => ({
      width: node.getBoundingClientRect().width,
      border: parseFloat(getComputedStyle(node).borderBottomWidth),
    }));
    assert.ok(layout.width > 0 && layout.border > 0, JSON.stringify(layout));
    assert.ok(fonts.length > 0);
    assert.ok(fonts.every((status) => status === 200));
    assert.deepEqual(await page.evaluate(() => window.violations), []);
    assert.deepEqual(errors, []);
  } finally {
    if (browser) await browser.close();
    if (server) await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  }
});

import { build } from "esbuild";
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
const root = dirname(fileURLToPath(import.meta.url));
const result = await build({
  entryPoints: [resolve(root, "app.ts")],
  bundle: true,
  write: false,
  format: "iife",
  target: "es2022",
  minify: true,
});
const css = await readFile(resolve(root, "style.css"), "utf8");
const html = await readFile(resolve(root, "index.html"), "utf8");
await writeFile(
  resolve(root, "../src/galatea_app/static/workbench.html"),
  html
    .replace("<!-- STYLE -->", () => `<style>${css}</style>`)
    .replace(
      "<!-- SCRIPT -->",
      () =>
        `<script>${result.outputFiles[0].text.replace(/<\/script/gi, "<\\/script")}</script>`,
    ),
);
console.log("Built MCP App resource");

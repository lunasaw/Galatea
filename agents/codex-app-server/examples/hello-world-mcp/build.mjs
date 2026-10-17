import { build } from "../../frontend/node_modules/esbuild/lib/main.js";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const result = await build({
  entryPoints: [resolve(root, "app.js")],
  nodePaths: [resolve(root, "../../frontend/node_modules")],
  bundle: true,
  write: false,
  format: "iife",
  target: "es2022",
  minify: true,
  legalComments: "inline",
});
const template = await readFile(resolve(root, "app.html"), "utf8");
const script = result.outputFiles[0].text.replace(/<\/script/gi, "<\\/script");
await mkdir(resolve(root, "dist"), { recursive: true });
await writeFile(resolve(root, "dist/app.html"), template.replace("<!-- APP_SCRIPT -->", () => `<script>${script}</script>`));
console.log("Built dist/app.html");

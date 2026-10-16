import { build } from "esbuild";
import {
  appendFile,
  copyFile,
  readFile,
  readdir,
  unlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
const out = "../src/codex_agent/console/static";
const css = await postcss([tailwindcss({ optimize: true })]).process(
  await readFile("src/style.css", "utf8"),
  { from: path.resolve("src/style.css") },
);
const result = await build({
  entryPoints: ["src/main.tsx"],
  outfile: `${out}/app.js`,
  bundle: true,
  mainFields: ["module", "main"],
  minify: true,
  format: "esm",
  target: ["es2022"],
  supported: { "template-literal": false },
  legalComments: "inline",
  metafile: true,
  define: { "process.env.NODE_ENV": '"production"' },
  plugins: [
    {
      name: "tailwind",
      setup(build) {
        build.onLoad({ filter: /src\/style\.css$/ }, () => ({
          contents: css.css,
          loader: "css",
          resolveDir: path.resolve("src"),
        }));
      },
    },
    {
      // Radix scroll locking uses react-style-singleton. Its lifecycle stays
      // upstream-owned; constructable stylesheets work with style-src 'self'.
      name: "csp-style-singleton",
      setup(build) {
        build.onLoad(
          { filter: /react-style-singleton\/dist\/es2015\/singleton\.js$/ },
          async ({ path: file }) => {
            const source = await readFile(file, "utf8");
            const boundary = source.indexOf("export var stylesheetSingleton");
            const remove =
              "stylesheet.parentNode && stylesheet.parentNode.removeChild(stylesheet);";
            if (boundary < 0 || !source.includes(remove))
              throw new Error(
                "Review react-style-singleton CSP compatibility after upgrade",
              );
            return {
              contents:
                `function makeStyleTag() { return new CSSStyleSheet(); }
function injectStyles(sheet, css) { sheet.replaceSync(css); }
function insertStyleTag(sheet) { document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet]; }
` +
                source
                  .slice(boundary)
                  .replace(
                    remove,
                    "document.adoptedStyleSheets = document.adoptedStyleSheets.filter(sheet => sheet !== stylesheet);",
                  ),
              loader: "js",
            };
          },
        );
      },
    },
  ],
});
// Ship the complete copyright and license texts for every bundled package.
const packages = new Map();
for (const input of Object.keys(result.metafile.inputs)) {
  if (!input.includes("node_modules/")) continue;
  let directory = path.dirname(path.resolve(input));
  while (directory.includes("node_modules")) {
    let pkg;
    try {
      pkg = JSON.parse(
        await readFile(path.join(directory, "package.json"), "utf8"),
      );
    } catch {
      directory = path.dirname(directory);
      continue;
    }
    if (!pkg.name) {
      directory = path.dirname(directory);
      continue;
    }
    if (!packages.has(pkg.name + "@" + pkg.version)) {
      const files = (await readdir(directory))
        .filter((name) => /^(licen[sc]e|copying|notice)(\.|$)/i.test(name))
        .sort();
      const licenses = files.length
        ? await Promise.all(
            files.map((name) => readFile(path.join(directory, name), "utf8")),
          )
        : [await readFile(path.join("licenses", pkg.name + ".txt"), "utf8")];
      packages.set(
        pkg.name + "@" + pkg.version,
        `${pkg.name}@${pkg.version} (${pkg.license || "see license below"})\n${licenses.join("\n")}`,
      );
    }
    break;
  }
}
const notices =
  "Third-party notices for the Codex Agent Console bundle\n\n" +
  "Vendored assistant-ui components (MIT), commit 2bdd534a3d8da45e6c7a9ee6553862b83713059d\n" +
  (await readFile("licenses/assistant-ui-components.txt", "utf8")) +
  "\n\n" +
  [...packages]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, text]) => text)
    .join("\n\n----------------------------------------\n\n");
const normalizedNotices = notices
  .replaceAll("\r\n", "\n")
  .replace(/[ \t]+$/gm, "");
await writeFile("THIRD_PARTY_NOTICES.txt", normalizedNotices);
await appendFile(
  `${out}/app.js`,
  "\n/*!\n" + normalizedNotices.replaceAll("*/", "* /") + "\n*/\n",
);
await copyFile("index.html", `${out}/index.html`);
await copyFile(`${out}/app.css`, `${out}/style.css`);
await unlink(`${out}/app.css`);

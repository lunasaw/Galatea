import { build } from "esbuild";
import {
  copyFile,
  readFile,
  readdir,
  mkdir,
  unlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
const sourceStatic = "../src/codex_agent/console/static";
const out = process.env.CONSOLE_BUILD_DIR || sourceStatic;
await mkdir(out, { recursive: true });
const css = await postcss([tailwindcss({ optimize: true })]).process(
  await readFile("src/style.css", "utf8"),
  { from: path.resolve("src/style.css") },
);
const result = await build({
  entryPoints: ["src/main.tsx"],
  outdir: out,
  entryNames: "assets/[name]-[hash]",
  chunkNames: "assets/[name]-[hash]",
  splitting: true,
  bundle: true,
  loader: { ".woff2": "file", ".svg": "dataurl" },
  assetNames: "math-fonts/[name]",
  mainFields: ["module", "main"],
  minify: true,
  format: "esm",
  target: ["es2022"],
  supported: { "template-literal": false },
  legalComments: "inline",
  banner: { js: "/*! Third-party license texts: /THIRD_PARTY_NOTICES.txt */" },
  metafile: true,
  define: { "process.env.NODE_ENV": '"production"' },
  plugins: [
    {
      name: "katex-csp-html-parser",
      setup(build) {
        build.onLoad({ filter: /hast-util-from-html-isomorphic\/lib\/browser\.js$/ }, () => ({
          // The default browser parser assigns KaTeX markup to innerHTML,
          // triggering CSP before our style-to-class transform can run.
          contents: 'export { fromHtml as fromHtmlIsomorphic } from "hast-util-from-html";',
          loader: "js",
          resolveDir: path.resolve("node_modules/hast-util-from-html-isomorphic"),
        }));
      },
    },
    {
      name: "katex-local-fonts",
      setup(build) {
        build.onLoad({ filter: /katex\/dist\/katex\.min\.css$/ }, async ({ path: file }) => ({
          // Supported browsers load WOFF2; avoid shipping unused legacy fonts.
          contents: (await readFile(file, "utf8")).replace(/,url\([^)]*\.(?:woff|ttf)\) format\("[^"]+"\)/g, ""),
          loader: "css",
          resolveDir: path.dirname(file),
        }));
      },
    },
    {
      name: "xterm-csp-styles",
      setup(build) {
        build.onLoad({ filter: /@xterm\/xterm\/lib\/xterm\.mjs$/ }, async ({ path: file }) => {
          const source = await readFile(file, "utf8");
          const pattern = /([\w.]+)\.createElement\("style"\)/g;
          if ([...source.matchAll(pattern)].length !== 3)
            throw new Error("Review xterm CSP styles after upgrade");
          // Keep the pinned renderer's stylesheet lifecycle, without allowing
          // inline stylesheets in the console's Content-Security-Policy.
          const helper = `function xtermStyle(document) {
            const node = document.createElement('span'); node.hidden = true;
            const sheet = new CSSStyleSheet();
            document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
            Object.defineProperty(node, 'textContent', { set(css) { sheet.replaceSync(css); } });
            node.remove = () => {
              document.adoptedStyleSheets = document.adoptedStyleSheets.filter(s => s !== sheet);
              if (node.parentNode) node.parentNode.removeChild(node);
            };
            return node;
          }\n`;
          return { contents: helper + source.replace(pattern, 'xtermStyle($1)'), loader: 'js' };
        });
      },
    },
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
await writeFile(`${out}/THIRD_PARTY_NOTICES.txt`, normalizedNotices);
const entry = Object.entries(result.metafile.outputs).find(([, value]) => value.entryPoint === "src/main.tsx");
if (!entry) throw new Error("Missing console entry point");
const publicPath = (filename) => "/" + path.relative(path.resolve(out), path.resolve(filename)).split(path.sep).join("/");
const entryUrl = publicPath(entry[0]);
const styleUrl = publicPath(entry[1].cssBundle);
const transport = await readFile(`${sourceStatic}/transport.js`);
const transportUrl = `/assets/transport-${createHash("sha256").update(transport).digest("hex").slice(0, 12)}.js`;
await writeFile(`${out}${transportUrl}`, transport);
if (path.resolve(out) !== path.resolve(sourceStatic)) await copyFile(`${sourceStatic}/transport.js`, `${out}/transport.js`);
// Keep old HTML and external static-resource checks compatible across updates.
await writeFile(`${out}/app.js`, `import ${JSON.stringify(entryUrl)};\n`);
await copyFile(entry[1].cssBundle, `${out}/style.css`);
// Embed the brand assets so the static bundle also works with existing Hosts.
let html = await readFile("index.html", "utf8");
html = html.replace('href="/style.css"', `href="${styleUrl}"`)
  .replace('src="/app.js"', `src="${entryUrl}"`)
  .replace('src="/transport.js"', `src="${transportUrl}"`);
// Start all initial module downloads together instead of discovering them serially.
const preloads = new Set();
function preload(filename) {
  for (const dependency of result.metafile.outputs[filename]?.imports || []) {
    if (dependency.kind !== "import-statement" || dependency.external || preloads.has(dependency.path)) continue;
    preloads.add(dependency.path);
    preload(dependency.path);
  }
}
preload(entry[0]);
html = html.replace("</head>", [...preloads].map(file => `  <link rel="modulepreload" href="${publicPath(file)}" />`).join("\n") + "\n  </head>");
for (const [filename, mime] of [
  ["favicon.svg", "image/svg+xml"],
  ["favicon.ico", "image/x-icon"],
  ["apple-touch-icon.png", "image/png"],
]) {
  const data = await readFile(`public/${filename}`);
  html = html.replaceAll(`href="/${filename}"`, `href="data:${mime};base64,${data.toString("base64")}"`);
}
await writeFile(`${out}/index.html`, html);
await copyFile("src/audio-capture-worklet.js", `${out}/audio-capture-worklet.js`);
await writeFile(`${out}/build-meta.json`, JSON.stringify(result.metafile));
// Remove superseded bundles only after the current build has been written.
const assetsDirectory = path.resolve(out, "assets");
const currentAssets = new Set(
  Object.keys(result.metafile.outputs)
    .filter((filename) => path.dirname(path.resolve(filename)) === assetsDirectory)
    .map((filename) => path.basename(filename)),
);
currentAssets.add(path.basename(transportUrl));
for (const entry of await readdir(assetsDirectory, { withFileTypes: true })) {
  if (entry.isFile() && !currentAssets.has(entry.name))
    await unlink(path.join(assetsDirectory, entry.name));
}

import { build } from '../frontend/node_modules/esbuild/lib/main.js';
import {writeFile} from 'node:fs/promises';import {resolve} from 'node:path';
const root=resolve(import.meta.dirname,'..');
const result=await build({entryPoints:[root+'/frontend/preview-host.ts'],bundle:true,write:false,format:'esm',target:'es2022',minify:true});
await writeFile(root+'/src/galatea_app/static/preview-host.html',`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Galatea · 复现证据展示</title><style>body{margin:0}iframe{border:0;width:100%;height:100vh;display:block}</style></head><body><iframe title="Galatea 工作台" sandbox="allow-scripts"></iframe><script type="module">${result.outputFiles[0].text.replace(/<\/script/gi,'<\\/script')}</script></body></html>`);

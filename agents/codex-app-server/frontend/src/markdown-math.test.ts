import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import { markdownRemarkPlugins, markdownRehypePlugins, preprocessMath } from "./markdown-math.ts";

function render(text: string): string {
  return renderToStaticMarkup(createElement(ReactMarkdown, {
    remarkPlugins: markdownRemarkPlugins,
    rehypePlugins: markdownRehypePlugins,
    children: preprocessMath(text),
  }));
}
const countMath = (html: string) => (html.match(/class="katex"/g) || []).length;

test("renders dollar and LaTeX bracket delimiters with inline/display semantics", () => {
  for (const source of ["$x^2$", String.raw`\(x^2\)`]) {
    const html = render(source);
    assert.equal(countMath(html), 1);
    assert.match(html, /<math/);
    assert.doesNotMatch(html, /class="katex-display"/);
  }
  for (const source of ["$$x^2$$", "$$\nx^2\n$$", String.raw`\[x^2\]`, "\\[\nx^2\n\\]"]) {
    const html = render(source);
    assert.equal(countMath(html), 1);
    assert.match(html, /class="katex-display"/);
    assert.match(html, /display="block"/);
  }
});

test("keeps code spans, fenced math samples, nested fences and indented code literal", () => {
  for (const source of [
    "`$x$ \\(y\\) \\[z\\]`",
    "```math\n$x$ \\(y\\) \\[z\\]\n```",
    "```python\nprint('$x$ \\(y\\)')\n```",
    "> ```text\n> \\[x\\] $5\n> ```",
    "    \\[x\\] $5\n",
    "``contains ` and \\(x\\)``",
  ]) {
    assert.equal(preprocessMath(source), source);
    assert.equal(countMath(render(source)), 0);
    assert.match(render(source), /<code/);
  }
});

test("preserves ordinary amounts and escaped dollars alongside numeric formulas", () => {
  const html = render(String.raw`Costs $5 and $19.99, range $5-$10; escaped \$7 and \$8. Formulas: $0$ and $5x = 10$.`);
  assert.equal(countMath(html), 2);
  assert.match(html, /Costs \$5 and \$19.99, range \$5-\$10; escaped \$7 and \$8/);
  assert.match(html, /<annotation encoding="application\/x-tex">5x = 10<\/annotation>/);
});

test("keeps GFM tables and lists while rendering their formulas", () => {
  const html = render("| Formula | Value |\n| --- | --- |\n| $x^2$ | 4 |\n\n- \\(a+b\\)\n- $$\n  \\frac{1}{2}\n  $$");
  assert.equal(countMath(html), 3);
  assert.match(html, /<table>/);
  assert.match(html, /<ul>/);
  assert.match(html, /katex-display/);
});

test("malformed and streamed expressions stay renderable without trusted HTML", () => {
  for (const text of ["before $x", "\\(x", "$$\n\\frac{", "$\\notACommand{x}$", "$\\href{javascript:alert(1)}{x}$", "$\\htmlStyle{background:url(https://example.com)}{x}$"]) {
    const html = render(text);
    assert.equal(typeof html, "string");
    assert.doesNotMatch(html, /<script|<iframe|href="javascript:| style=/);
  }
  assert.match(render("$\\notACommand{x}$"), /notACommand/);
});

test("moves KaTeX layout into stable CSP-safe classes instead of inline styles", () => {
  const text = String.raw`$$\frac{1}{2} + \sqrt{x} + \sum_{i=1}^{n} i$$`;
  const first = render(text);
  assert.match(first, /aui-math-/);
  assert.doesNotMatch(first, / style=/);
  assert.equal(render(text), first);
});

import {
  escapeCurrencyDollars,
  normalizeMathDelimiters,
} from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import type { Root as MdastRoot } from "mdast";
import type { Element, Root as HastRoot } from "hast";
import type { VFile } from "vfile";
import type { PluggableList } from "unified";

/** Upstream normalization skips fenced/indented code, code spans and HTML. */
export function preprocessMath(text: string): string {
  return escapeCurrencyDollars(normalizeMathDelimiters(text));
}

/** remark-math parses one-line $$…$$ as inline; retain its display intent. */
function remarkDisplayMath() {
  return (tree: MdastRoot, file: VFile) => {
    const source = String(file);
    const visit = (node: MdastRoot | MdastRoot["children"][number]) => {
      if (node.type === "inlineMath" && node.position) {
        const start = node.position.start.offset;
        const end = node.position.end.offset;
        if (start !== undefined && end !== undefined && /^\${2,}/.test(source.slice(start, end))) {
          node.data!.hProperties = { className: ["language-math", "math-display"] };
        }
      }
      if ("children" in node)
        for (const child of node.children) visit(child as MdastRoot["children"][number]);
    };
    visit(tree);
  };
}

const mathStyles = new Map<string, string>();
let mathSheet: CSSStyleSheet | undefined;

function mathStyleClass(style: string): string {
  // Only renderer-produced declarations reach this function. Keep rule boundaries
  // and resource fetches out of this stylesheet even if KaTeX changes its output.
  if (/[{}<>]/.test(style) || /(?:url|expression)\s*\(/i.test(style)) return "";
  let hash = 2166136261;
  for (let i = 0; i < style.length; i++) hash = Math.imul(hash ^ style.charCodeAt(i), 16777619);
  const prefix = `aui-math-${(hash >>> 0).toString(36)}`;
  let name = prefix;
  let suffix = 0;
  while (mathStyles.has(name) && mathStyles.get(name) !== style) name = `${prefix}-${++suffix}`;
  const exists = mathStyles.has(name);
  mathStyles.set(name, style);
  if (typeof document !== "undefined" && !mathSheet) {
    // Match the console's existing CSP integration: CSSOM stylesheets do not
    // require unsafe-inline or a <style> element under style-src 'self'.
    mathSheet = new CSSStyleSheet();
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, mathSheet];
    for (const [className, declarations] of mathStyles)
      mathSheet.insertRule(`.${className}{${declarations}}`, mathSheet.cssRules.length);
  } else if (mathSheet && !exists) {
    mathSheet.insertRule(`.${name}{${style}}`, mathSheet.cssRules.length);
  }
  return name;
}

/** Replace KaTeX layout style attributes with CSP-compatible stylesheet rules. */
function rehypeMathStyles() {
  return (tree: HastRoot) => {
    const visit = (node: HastRoot | Element, inMath = false) => {
      if (node.type === "element") {
        const classes = Array.isArray(node.properties.className) ? node.properties.className : [];
        inMath ||= classes.includes("katex") || classes.includes("katex-error");
        if (inMath && typeof node.properties.style === "string") {
          const name = mathStyleClass(node.properties.style);
          if (name) node.properties.className = [...classes, name];
          delete node.properties.style;
        }
      }
      for (const child of node.children)
        if (child.type === "element") visit(child, inMath);
    };
    visit(tree);
  };
}

function rehypeDelimitedMath() {
  const render = rehypeKatex({ trust: false, strict: "ignore", maxExpand: 1000, maxSize: 100 });
  return (tree: HastRoot, file: VFile) => {
    const protectedCode: Array<[Element, Element["properties"]["className"]]> = [];
    const visit = (node: HastRoot | Element) => {
      if (node.type === "element") {
        const classes = node.properties.className;
        if (Array.isArray(classes) && classes.includes("language-math") &&
            !classes.includes("math-inline") && !classes.includes("math-display")) {
          // A fenced code sample labelled `math` is still a code sample.
          protectedCode.push([node, classes]);
          node.properties.className = classes.filter((name) => name !== "language-math");
        }
      }
      for (const child of node.children)
        if (child.type === "element") visit(child);
    };
    visit(tree);
    try { render(tree, file); }
    finally {
      for (const [node, classes] of protectedCode) node.properties.className = classes;
    }
  };
}

export const markdownRemarkPlugins: PluggableList = [remarkGfm, remarkMath, remarkDisplayMath];
export const markdownRehypePlugins: PluggableList = [
  rehypeDelimitedMath,
  rehypeMathStyles,
];

import assert from "node:assert/strict";
import test from "node:test";
import { localFileTarget } from "./file-target";
import { commandActions } from "./command-actions";

test("file references preserve line, column and line range locations", () => {
  assert.deepEqual(localFileTarget("/workspace/app.tsx:42:8"), {
    path: "/workspace/app.tsx",
    line: 42,
  });
  assert.deepEqual(localFileTarget("/workspace/app.tsx#L4-L9"), {
    path: "/workspace/app.tsx",
    line: 4,
    endLine: 9,
  });
  assert.deepEqual(localFileTarget("/workspace/app.tsx:0"), {
    path: "/workspace/app.tsx",
  });
  assert.deepEqual(localFileTarget("/workspace/app.tsx#L9-L4"), {
    path: "/workspace/app.tsx",
    line: 9,
  });
});
test("encoded Unicode paths resolve against the actual workspace", () => {
  assert.deepEqual(localFileTarget("README.md:3", "/workspace"), {
    path: "/workspace/README.md",
    line: 3,
  });
  assert.deepEqual(localFileTarget("Makefile:20", "/workspace"), {
    path: "/workspace/Makefile",
    line: 20,
  });
  assert.deepEqual(
    localFileTarget(
      "docs/%E7%95%8C%E9%9D%A2%20%E8%AF%B4%E6%98%8E.md",
      "/workspace",
    ),
    { path: "/workspace/docs/界面 说明.md" },
  );
  assert.deepEqual(localFileTarget("../src/./app.py:3", "/workspace/docs"), {
    path: "/workspace/src/app.py",
    line: 3,
  });
  assert.equal(localFileTarget("src/app.py"), null);
});
test("local file URIs work without accepting remote file servers", () => {
  assert.deepEqual(localFileTarget("file:///workspace/app.py#L2"), {
    path: "/workspace/app.py",
    line: 2,
  });
  assert.deepEqual(localFileTarget("file://localhost/workspace/app.py"), {
    path: "/workspace/app.py",
  });
  assert.equal(
    localFileTarget("file://example.com/workspace/app.py", "/workspace"),
    null,
  );
});
test("web links, anchors, malformed escapes and decoded controls are not files", () => {
  for (const href of [
    "https://example.com/a.py",
    "mailto:user@example.com",
    "javascript:alert(1)",
    "//example.com/a",
    "%2f%2fexample.com/a",
    "/a%00.py",
    "/a%0a.py",
    "/a%7f.py",
    "/bad%GG.py",
    "#heading",
    "/",
  ]) {
    assert.equal(localFileTarget(href, "/workspace"), null, href);
  }
});
test("native command summaries preserve structured file and search references", () => {
  assert.deepEqual(
    commandActions({
      commandActions: [
        { type: "read", path: "/a.py", name: "a.py", command: "cat a.py" },
        { type: "search", path: "/src", query: "test" },
      ],
    }),
    [
      { type: "read", path: "/a.py", name: "a.py" },
      { type: "search", path: "/src", query: "test" },
    ],
  );
});
test("unclassified or mixed shell actions keep the actual command presentation", () => {
  assert.deepEqual(commandActions({ command: "cat a.py" }), []);
  assert.deepEqual(
    commandActions({
      commandActions: [
        { type: "read", path: "/a.py" },
        { type: "unknown", command: "rm b.py" },
      ],
    }),
    [],
  );
  assert.deepEqual(commandActions({ commandActions: [null] }), []);
});

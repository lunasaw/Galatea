import { test } from "node:test";
import assert from "node:assert/strict";
import {
  emptyLayout,
  legacyProjectId,
  projectForPath,
  mergeProjects,
  moveProject,
  moveThread,
  normalizeProjectPath,
  parseProjectLayout,
} from "./project-state.ts";
import {
  identifyPermission,
  permissionSettings,
} from "./permission-presets.ts";

test("saved projects retain empty projects, groups and ordering when sessions refresh", () => {
  const projects = mergeProjects(emptyLayout(), ["/alpha", "/beta", "/alpha"]);
  const moved = moveProject(
    projects,
    legacyProjectId("/beta"),
    "work",
    legacyProjectId("/alpha"),
  );
  const layout = {
    ...emptyLayout(),
    projects: moved,
    sections: [{ id: "work", name: "工作" }],
  };
  const restored = parseProjectLayout(JSON.stringify(layout));
  assert.deepEqual(mergeProjects(restored, ["/alpha", "/gamma"]), [
    ...moved,
    {
      id: legacyProjectId("/gamma"),
      path: "/gamma",
      name: "gamma",
      section: "",
    },
  ]);
  assert.equal(restored.projects[0].section, "work");
  assert.deepEqual(parseProjectLayout("broken"), emptyLayout());
  assert.deepEqual(moveThread(["one", "two", "three"], "three", "one"), [
    "three",
    "one",
    "two",
  ]);
});

test("paths normalize duplicate directories and reject relative input", () => {
  assert.equal(
    normalizeProjectPath(" /data//repo/../project/ "),
    "/data/project",
  );
  assert.equal(normalizeProjectPath("/"), "/");
  assert.throws(() => normalizeProjectPath("~/repo"));
  assert.throws(() => normalizeProjectPath("relative"));
});

test("permission presets distinguish automatic review and full access from custom restrictions", () => {
  for (const preset of ["ask", "auto", "full"] as const) {
    assert.equal(identifyPermission(permissionSettings(preset)), preset);
    assert.equal(identifyPermission(permissionSettings(preset, true)), preset);
  }
  assert.equal(
    identifyPermission({ sandbox: "read-only", approvalPolicy: "on-request" }),
    "custom",
  );
  assert.equal(
    identifyPermission({
      sandbox: "danger-full-access",
      approvalPolicy: "on-request",
    }),
    "custom",
  );
  assert.equal(
    identifyPermission({
      ...permissionSettings("ask"),
      permissions: "restricted",
    }),
    "custom",
  );
});

test("legacy path-based projects migrate without losing names, order or collapse state", () => {
  const layout = parseProjectLayout(
    JSON.stringify({
      projects: [{ path: "/shared", name: "原项目", section: "work" }],
      sections: [{ id: "work", name: "工作" }],
      collapsed: { "/shared": true },
      threadOrder: ["one"],
    }),
  );
  assert.equal(layout.projects[0].id, legacyProjectId("/shared"));
  assert.equal(layout.projects[0].name, "原项目");
  assert.equal(layout.collapsed[legacyProjectId("/shared")], true);
  assert.deepEqual(parseProjectLayout(JSON.stringify(layout)), layout);
});

test("projects sharing a directory move independently and never change legacy fallback by sorting", () => {
  const projects = [
    {
      id: legacyProjectId("/shared"),
      path: "/shared",
      name: "旧项目",
      section: "",
    },
    { id: "project-b", path: "/shared", name: "项目 B", section: "" },
  ];
  const moved = moveProject(projects, "project-b", "work", projects[0].id);
  assert.equal(moved.length, 2);
  assert.equal(moved[0].section, "work");
  assert.equal(moved[1].section, "");
  assert.equal(projectForPath(moved, "/shared")?.id, projects[0].id);
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { configEdits, configKeyPath, configPath, configWriteParams, layerLabel, positiveInteger, structuredConfigEdits, visibleEfforts } from "./configuration-data.ts";

test("configuration sources tolerate redacted and missing metadata without hiding usable sources", () => {
  assert.equal(layerLabel({ name: { type: "user", profile: "work" }, version: "v1" }), "用户配置 · work");
  assert.equal(layerLabel({ name: { type: "project", dotCodexFolder: "/repo/.codex" } }), "项目配置");
  assert.equal(layerLabel("[redacted:credential]"), "来源信息已隐藏");
  assert.equal(layerLabel("[redacted:private_content]"), "来源信息已隐藏");
  for (const value of [undefined, null, "", {}, { name: null }, { name: {} }, { name: { type: 1 } }]) {
    assert.equal(layerLabel(value), "未知配置来源");
  }
});

test("nested settings preserve hidden siblings and quote names using native TOML key paths", () => {
  const original = { model_providers: { "private.v2": { name: "Old", api_key: "[redacted:credential]" } }, permissions: { test: { filesystem: { "/work/out": "write" } } } };
  const updated = structuredClone(original); updated.model_providers["private.v2"].name = "New";
  updated.permissions.test.filesystem["/work/out"] = "read";
  assert.deepEqual(structuredConfigEdits(original, updated), [
    { keyPath: 'model_providers."private.v2".name', value: "New", mergeStrategy: "replace" },
    { keyPath: 'permissions.test.filesystem."/work/out"', value: "read", mergeStrategy: "replace" },
  ]);
  assert.equal(configKeyPath(["projects", 'a"b\\c', "trust_level"]), 'projects."a\\"b\\\\c".trust_level');
  assert.equal(configKeyPath(["projects", "line\nname"]), 'projects."line\nname"');
  assert.throws(() => configKeyPath(["projects", ""]), /空字段名/);
  assert.throws(() => structuredConfigEdits({}, { agents: [{ secret: "[redacted:credential]" }] }), /隐藏内容/);
  assert.deepEqual(structuredConfigEdits(original, {}), [
    { keyPath: "model_providers", value: null, mergeStrategy: "replace" }, { keyPath: "permissions", value: null, mergeStrategy: "replace" },
  ]);
});

test("configuration patches preserve unrelated and redacted provider fields", () => {
  const original = { model_verbosity: "high", model_providers: { private: { name: "Old", api_key: "[redacted:credential]" } } };
  assert.deepEqual(configEdits({ model_verbosity: null, "model_providers.private.name": "New" }, original), [
    { keyPath: "model_verbosity", value: null, mergeStrategy: "replace" },
    { keyPath: "model_providers.private.name", value: "New", mergeStrategy: "replace" },
  ]);
  assert.deepEqual(configEdits({ model_verbosity: "high", web_search: null }, original), []);
  assert.throws(() => configEdits({ developer_instructions: "[redacted:private_content]" }, {}), /占位符/);
});
test("writes use the observed user layer version and refuse project or disabled layers", () => {
  const layer = { name: { type: "user", file: "/home/config.toml" }, version: "sha256:observed", config: {} };
  const edits = configEdits({ web_search: "live" }, {});
  assert.deepEqual(configWriteParams(layer, edits, true), { edits, filePath: "/home/config.toml", expectedVersion: "sha256:observed", reloadUserConfig: true });
  assert.throws(() => configWriteParams({ ...layer, name: { type: "project", dotCodexFolder: "/repo/.codex" } }, edits, false));
  assert.throws(() => configWriteParams({ ...layer, disabledReason: "policy" }, edits, false));
  assert.equal(configPath({ name: { type: "project", dotCodexFolder: "/repo/.codex/" } }), "/repo/.codex/config.toml");
});
test("reasoning visibility retains the active effort and never invents model capabilities", () => {
  assert.deepEqual(visibleEfforts(["low", "high"], ["high"], "high"), ["low", "high"]);
  assert.deepEqual(visibleEfforts(["low", "high"], ["high", "ultra"]), ["low"]);
  assert.deepEqual(visibleEfforts(["high"], ["high"]), ["high"]);
  assert.deepEqual(visibleEfforts([], [], "high"), []);
});
test("token limits accept positive integers and blank reset only", () => {
  assert.equal(positiveInteger(""), null);
  assert.equal(positiveInteger("100000"), 100000);
  for (const value of ["0", "-1", "0.5", "NaN", "9007199254740992"]) assert.throws(() => positiveInteger(value));
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { defaultForSchema, matchesSchema, resolveSchema, variantsOf } from "./schema-data.ts";

test("loaded union values retain their branch including nulls and tagged account flows", () => {
  const root = { definitions: { Login: { type: "object", required: ["type", "key"], properties: { type: { enum: ["apiKey"] }, key: { type: "string" } } } } };
  const schema = { oneOf: [{ type: "null" }, { $ref: "#/definitions/Login" }, { type: "object", required: ["type"], properties: { type: { enum: ["chatgpt"] } } }] };
  const variants = variantsOf(schema)!;
  assert.equal(variants.findIndex((s) => matchesSchema(s, { type: "apiKey", key: "configured" }, root)), 1);
  assert.equal(variants.findIndex((s) => matchesSchema(s, { type: "chatgpt" }, root)), 2);
  assert.equal(variants.findIndex((s) => matchesSchema(s, null, root)), 0);
  assert.deepEqual(defaultForSchema(variants[1], root), { type: "apiKey", key: "" });
});
test("allOf resolves scalar aliases without turning them into objects and merges required fields", () => {
  const root = { definitions: { Name: { type: "string", minLength: 1 }, Required: { type: "object", required: ["name"], properties: { name: { $ref: "#/definitions/Name" } } } } };
  const scalar = resolveSchema({ allOf: [{ $ref: "#/definitions/Name" }], description: "name" }, root);
  assert.equal(scalar.type, "string"); assert.equal(scalar.properties, undefined);
  assert.deepEqual(defaultForSchema({ allOf: [{ $ref: "#/definitions/Required" }, { type: "object", required: ["enabled"], properties: { enabled: { type: "boolean", default: true } } }] }, root), { name: "", enabled: true });
  const original = { type: "object", default: { nested: [] as string[] } };
  defaultForSchema(original, root).nested.push("draft");
  assert.deepEqual(original.default.nested, []);
});

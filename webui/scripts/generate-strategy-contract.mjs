// D12 additive contract generation. Does not touch v1-v3 or dist assets.
import { readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import standaloneCode from "ajv/dist/standalone/index.js";

const evidence = new URL("../../docs/evidence/m1-d12/", import.meta.url);
const raw = await readFile(new URL("openapi_v4.json", evidence));
const expected = (await readFile(new URL("openapi_v4.sha256", evidence), "utf8")).trim();
if (!/^[0-9a-f]{64}$/.test(expected) || createHash("sha256").update(raw).digest("hex") !== expected) {
  throw Error("Unreviewed D12 schema");
}
const doc = JSON.parse(raw);
const definitions = doc.components.schemas;
function shape(node) {
  if (Array.isArray(node)) return node.map(shape);
  if (!node || typeof node !== "object") return node;
  const result = Object.fromEntries(Object.entries(node).map(([key, value]) => [key, shape(value)]));
  if (result.$ref) result.$ref = result.$ref.replace("#/components/schemas/", "#/$defs/");
  if (result.propertyNames) result.propertyNames.type ??= "string";
  return result;
}
function type(s) {
  if (s.$ref) return s.$ref.split("/").at(-1);
  if ("const" in s) return JSON.stringify(s.const);
  if (s.enum) return s.enum.map(JSON.stringify).join(" | ");
  if (s.anyOf) return s.anyOf.map(type).join(" | ");
  switch (s.type) {
    case "array": return "Array<" + type(s.items) + ">";
    case "object":
      if (s.properties) return "{ " + Object.entries(s.properties).map(([k,v]) =>
        JSON.stringify(k) + (s.required?.includes(k) ? ": " : "?: ") + type(v) + ";").join(" ") + " }";
      if (typeof s.additionalProperties === "object") return "Record<string, " + type(s.additionalProperties) + ">";
      break;
    case "integer": case "number": return "number";
    case "string": case "boolean": case "null": return s.type;
  }
  throw Error("Unsupported strategy schema: " + JSON.stringify(s));
}
const generated = new URL("../src/generated/", import.meta.url);
const types = "// Generated from frozen D12 OpenAPI. Do not edit.\n"
  + "export const STRATEGY_CONTRACT_SHA256 = " + JSON.stringify(expected) + ";\n"
  + "export const STRATEGY_LIMITS = " + JSON.stringify(doc["x-strategy-limits"]) + " as const;\n"
  + "export const STRATEGY_UNITS = " + JSON.stringify(doc["x-strategy-units"]) + " as const;\n"
  + Object.entries(definitions).map(([name,s]) => "export type " + name + " = " + type(s) + ";").join("\n") + "\n";
const ajv = new Ajv2020({strict:true, strictNumbers:true, code:{source:true, esm:true}, allErrors:false});
addFormats(ajv, ["date", "date-time"]);
const mappings = {
  validateStrategyPayoffRequest: "StrategyPayoffRequestDTO",
  validateStrategyValuationRequest: "StrategyValuationRequestDTO",
  validateStrategyEnvelope: "StrategyEnvelopeDTO",
};
const ids = {};
for (const [name, dto] of Object.entries(mappings)) {
  const id = "urn:optionmaster:d12:" + dto;
  ids[name] = id;
  ajv.addSchema({$id:id, ...shape(definitions[dto]), $defs:shape(definitions)});
}
const declarations = "import type { " + Object.values(mappings).join(", ") + " } from './strategy-types';\n"
  + Object.entries(mappings).map(([name,dto]) => "export function " + name + "(value: unknown): value is " + dto + ";").join("\n") + "\n";
for (const [name, content] of [
  ["strategy-types.ts", types],
  ["strategy-validators.mjs", standaloneCode(ajv, ids)],
  ["strategy-validators.d.mts", declarations],
]) {
  const path = new URL(name, generated);
  if (process.argv.includes("--check")) {
    if (await readFile(path, "utf8") !== content) throw Error("Generated strategy contract drift: " + name);
  } else {
    await writeFile(path, content);
  }
}
console.log("D12 contract generated/verified", expected);

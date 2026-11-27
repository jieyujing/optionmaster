// Generate strict frontend wire types and CSP-compatible validators from D06.
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import standaloneCode from "ajv/dist/standalone/index.js";
const expected = "1d8aca0aa045b5a731c25aa46b61d60dab68c8714088877f2e105a300ae8fbc6";
const raw = await readFile(new URL("../../docs/evidence/m1-d06/openapi_v1.json", import.meta.url));
if (createHash("sha256").update(raw).digest("hex") !== expected) throw Error("Unreviewed D06 schema");
const doc = JSON.parse(raw);
const definitions = doc.components.schemas;
const request = doc.paths["/api/v1/research/valuations"].post.requestBody.content["application/json"].schema;
// D06 serialization emits every declared field, including defaults. Require
// them in the client to avoid a missing production flag or unit becoming implicit.
function strictShape(node) {
  if (Array.isArray(node)) return node.map(strictShape);
  if (!node || typeof node !== "object") return node;
  const result = Object.fromEntries(Object.entries(node).map(([key,value]) => [key,strictShape(value)]));
  if (result.$ref) result.$ref = result.$ref.replace("#/components/schemas/","#/$defs/");
  if (result.properties) result.required = Object.keys(result.properties);
  return result;
}
function type(s) {
  if (s.$ref) return s.$ref.split("/").at(-1);
  if ("const" in s) return JSON.stringify(s.const);
  if (s.enum) return s.enum.map(JSON.stringify).join(" | ");
  if (s.anyOf) return s.anyOf.map(type).join(" | ");
  switch(s.type) {
    case "array": return "Array<" + type(s.items) + ">";
    case "object":
      if (s.properties) return "{ " + Object.entries(s.properties).map(([k,v])=>JSON.stringify(k)+": "+type(v)+";").join(" ")+" }";
      if (typeof s.additionalProperties === "object") return "Record<string, "+type(s.additionalProperties)+">";
      break;
    case "integer": case "number": return "number";
    case "string": case "boolean": case "null": return s.type;
  }
  throw Error("Unsupported schema construct: "+JSON.stringify(s));
}
const generated = new URL("../src/generated/",import.meta.url);
await mkdir(generated,{recursive:true});
const types = "// Generated from pinned D06 OpenAPI. Do not edit.\n"
  + "export const CONTRACT_SHA256 = "+JSON.stringify(expected)+";\n"
  + Object.entries({...definitions,ValuationRequestDTO:request}).map(([name,s])=>"export type "+name+" = "+type(s)+";").join("\n")+"\n";
await writeFile(new URL("types.ts",generated),types);
const ajv = new Ajv2020({strict:true,strictNumbers:true,code:{source:true,esm:true},allErrors:false});
addFormats(ajv,["date","date-time"]);
ajv.addSchema({$id:"urn:optionmaster:response",...strictShape(definitions.ApiEnvelope),$defs:strictShape(definitions)});
ajv.addSchema({$id:"urn:optionmaster:request",...strictShape(request)});
await writeFile(new URL("validators.mjs",generated),standaloneCode(ajv,{validateEnvelope:"urn:optionmaster:response",validateRequest:"urn:optionmaster:request"}));
await writeFile(new URL("validators.d.mts",generated),"import type { ApiEnvelope, ValuationRequestDTO } from './types';\nexport function validateEnvelope(value: unknown): value is ApiEnvelope;\nexport function validateRequest(value: unknown): value is ValuationRequestDTO;\n");
console.log("D06 contract generated",expected);
const chainExpected = "46b37b055d5ee8e275877d1feb4aaedf94e807f3bd50b1661f67c7632ce77e1e";
const chainRaw = await readFile(new URL("../../docs/evidence/m1-d08/cu2211-20220907/openapi_v2.json", import.meta.url));
if (createHash("sha256").update(chainRaw).digest("hex") !== chainExpected) throw Error("Unreviewed D08 schema");
const chainDoc = JSON.parse(chainRaw);
const chainDefs = chainDoc.components.schemas;
const chainRequest = chainDoc.paths["/api/v2/research/valuations"].post.requestBody.content["application/json"].schema;
const chainTypes = "// Generated from pinned D08 OpenAPI. Do not edit.\n"
  + "export const CHAIN_CONTRACT_SHA256 = "+JSON.stringify(chainExpected)+";\n"
  + Object.entries({...chainDefs,ChainValuationRequestDTO:chainRequest}).map(([name,s])=>"export type "+name+" = "+type(s)+";").join("\n")+"\n";
await writeFile(new URL("chain-types.ts",generated),chainTypes);
const chainAjv = new Ajv2020({strict:true,strictNumbers:true,code:{source:true,esm:true},allErrors:false});
addFormats(chainAjv,["date","date-time"]);
chainAjv.addSchema({$id:"urn:optionmaster:chain-response",...strictShape(chainDefs.ChainApiEnvelope),$defs:strictShape(chainDefs)});
chainAjv.addSchema({$id:"urn:optionmaster:chain-request",...strictShape(chainRequest)});
await writeFile(new URL("chain-validators.mjs",generated),standaloneCode(chainAjv,{validateChainEnvelope:"urn:optionmaster:chain-response",validateChainRequest:"urn:optionmaster:chain-request"}));
await writeFile(new URL("chain-validators.d.mts",generated),"import type { ChainApiEnvelope, ChainValuationRequestDTO } from './chain-types';\nexport function validateChainEnvelope(value: unknown): value is ChainApiEnvelope;\nexport function validateChainRequest(value: unknown): value is ChainValuationRequestDTO;\n");
console.log("D08 contract generated",chainExpected);
const catalogExpected = "a3cb4e79acc127a13d69565494259c232d65835a50edaf2c6f6e50268ca5b7f3";
const catalogRaw = await readFile(new URL("../../docs/evidence/m1-d09/openapi_v3.json", import.meta.url));
if (createHash("sha256").update(catalogRaw).digest("hex") !== catalogExpected) throw Error("Unreviewed D09 schema");
const catalogDoc = JSON.parse(catalogRaw);
const catalogDefs = catalogDoc.components.schemas;
const catalogRequest = catalogDoc.paths["/api/v3/research/valuations"].post.requestBody.content["application/json"].schema;
const catalogTypes = "// Generated from pinned D09 OpenAPI. Do not edit.\n"
  + "export const CATALOG_CONTRACT_SHA256 = "+JSON.stringify(catalogExpected)+";\n"
  + Object.entries({...catalogDefs,CatalogValuationRequestDTO:catalogRequest}).map(([name,s])=>"export type "+name+" = "+type(s)+";").join("\n")+"\n";
await writeFile(new URL("catalog-types.ts",generated),catalogTypes);
const catalogAjv = new Ajv2020({strict:true,strictNumbers:true,code:{source:true,esm:true},allErrors:false});
addFormats(catalogAjv,["date","date-time"]);
catalogAjv.addSchema({$id:"urn:optionmaster:catalog-response",...strictShape(catalogDefs.CatalogApiEnvelope),$defs:strictShape(catalogDefs)});
catalogAjv.addSchema({$id:"urn:optionmaster:catalog-request",...strictShape(catalogRequest)});
await writeFile(new URL("catalog-validators.mjs",generated),standaloneCode(catalogAjv,{validateCatalogEnvelope:"urn:optionmaster:catalog-response",validateCatalogRequest:"urn:optionmaster:catalog-request"}));
await writeFile(new URL("catalog-validators.d.mts",generated),"import type { CatalogApiEnvelope, CatalogValuationRequestDTO } from './catalog-types';\nexport function validateCatalogEnvelope(value: unknown): value is CatalogApiEnvelope;\nexport function validateCatalogRequest(value: unknown): value is CatalogValuationRequestDTO;\n");
console.log("D09 contract generated",catalogExpected);

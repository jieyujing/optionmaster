import { build } from "esbuild";
import { spawnSync } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
await mkdir("dist",{recursive:true});
const legacy = {
  "contract.test": "m1-d06/actual_api_result.json",
  "chain-contract.test": "m1-d08/cu2211-20220907/actual_chain_api_result.json",
  "catalog-contract.test": "m1-d09/browser/actual_catalog_api_result.json",
};
const available = Object.entries(legacy).flatMap(([name, fixture]) => {
  if (existsSync(`../docs/evidence/${fixture}`)) return [name];
  console.log(`Skipping ${name}: local reviewed replay fixture absent`);
  return [];
});
if (available.length) {
  await build({entryPoints:available.map(name => `tests/${name}.ts`),bundle:true,platform:"node",format:"cjs",outdir:"dist/tests",outExtension:{".js":".cjs"}});
  const result = spawnSync(process.execPath, ["--test", ...available.map(name => `dist/tests/${name}.cjs`)], {stdio:"inherit"});
  if (result.status !== 0) process.exit(result.status ?? 1);
}
const strategy = spawnSync(process.execPath, ["scripts/test-strategy.mjs"], {stdio:"inherit"});
process.exitCode = strategy.status ?? 1;

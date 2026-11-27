// Separate from served dist. Bundle generated AJV dependencies as the UI does.
import { build } from "esbuild";
import { spawnSync } from "node:child_process";
import { mkdir } from "node:fs/promises";
await mkdir("test-results/d12", {recursive:true});
const suites = {
  "strategy-contract.test": "tests/strategy-contract.test.mjs",
  "strategy-state.test": "tests/strategy-state.test.ts",
  "strategy-lane.test": "tests/strategy-lane.test.ts",
};
await build({entryPoints:suites,bundle:true,platform:"node",format:"esm",
  outdir:"test-results/d12",outExtension:{".js":".mjs"},
  banner:{js:'import {createRequire} from "node:module"; const require = createRequire(import.meta.url);'}});
const result = spawnSync(process.execPath, ["--test", ...Object.keys(suites).map(suite => `test-results/d12/${suite}.mjs`)],
  {stdio:"inherit"});
process.exitCode = result.status ?? 1;

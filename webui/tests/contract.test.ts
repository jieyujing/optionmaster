import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { decodeCoverage, decodeValuation, buildRequest, ApiError } from "../src/api";

const real=JSON.parse(readFileSync("../docs/evidence/m1-d06/actual_api_result.json","utf8"));
const coverage=()=>decodeCoverage(real.coverage_response);
const req=()=>real.request;
const valuation=()=>structuredClone(real.valuation_response);

test("actual D06 envelope and 25-cell grid accepted",()=>{
  const c=coverage();
  const v=decodeValuation(valuation(),c,req());
  assert.equal(v.grid.heatmap_rows.flat().length,25);
  assert.equal(v.grid.price_curve.length,9);
  assert.equal(v.production_valuation_ready,false);
  assert.equal(v.grid.configured_value_change,0);
  assert.ok(Math.abs(v.grid.baseline_value-24400)<1e-4);
});
test("build explicit signed request in canonical source order and market clock",()=>{
  const c=coverage();
  const r=buildRequest(c,["2","-1"],"2022-09-08T15:00","10","6");
  assert.deepEqual(Object.values(r.positions),[2,-1]);
  assert.equal(r.scenario_at,"2022-09-08T15:00:00+08:00");
  assert.deepEqual(r.heatmap_price_shocks,[-0.1,-0.05,0,0.05,0.1]);
  assert.deepEqual(r.heatmap_iv_shocks,[-0.06,-0.03,0,0.03,0.06]);
  assert.equal(r.curve_price_shocks.length,9);
});
for (const quantity of ["0","-0","1.5","1001","1e2","", " 1"]) {
  test("reject quantity "+JSON.stringify(quantity),()=>{
    assert.throws(()=>buildRequest(coverage(),[quantity,"1"],"2022-09-07T15:00","10","6"),ApiError);
  });
}
for (const at of ["2022-09-07T14:59","2022-10-25T00:00","2022-02-30T15:00","2022-09-07T15:00Z",""]) {
  test("reject scenario "+at,()=>{
    assert.throws(()=>buildRequest(coverage(),["1","1"],at,"10","6"),ApiError);
  });
}
for (const p of ["0","101","1e2","-1","NaN"]) {
  test("reject shock "+p,()=>{
    assert.throws(()=>buildRequest(coverage(),["1","1"],"2022-09-07T15:00",p,"6"),ApiError);
  });
}
test("UNAVAILABLE envelope is not numerical data",()=>{
  assert.throws(()=>decodeValuation({schema_version:"1.0",calculation_id:"a".repeat(32),status:"UNAVAILABLE",reason_code:"BUSY",coverage:null,valuation:null},coverage(),req()),e=>e instanceof ApiError&&e.code==="BUSY");
});
for(const [name,mutate] of [
  ["missing readiness",v=>delete v.valuation.production_valuation_ready],
  ["changed readiness",v=>v.valuation.production_valuation_ready=true],
  ["nonfinite baseline",v=>v.valuation.grid.baseline_value=Infinity],
  ["missing cell",v=>v.valuation.grid.heatmap_rows[0].pop()],
  ["wrong axis",v=>v.valuation.grid.heatmap_rows[0][0].price_shock=0],
  ["wrong order",v=>v.valuation.legs.reverse()],
  ["wrong quantity",v=>v.valuation.legs[0].quantity=3],
  ["wrong registry",v=>v.valuation.source_versions.registry_sha256="f".repeat(64)],
  ["wrong time",v=>v.valuation.scenario_at="2022-09-08T15:00:00+08:00"],
  ["unavailable but numeric",v=>{const c=v.valuation.grid.heatmap_rows[0][0];c.status="UNAVAILABLE";c.reason_code="INVALID_SCENARIO"}],
  ["invalid origin",v=>v.valuation.grid.heatmap_rows[2][2].value_change=12],
] as [string,(v:any)=>void][]) {
  test("reject malformed response "+name,()=>{
    const v=valuation();mutate(v);assert.throws(()=>decodeValuation(v,coverage(),req()),ApiError);
  });
}
test("unavailable cell remains null, never substituted zero",()=>{
  const v=valuation(),c=v.valuation.grid.heatmap_rows[0][0];
  Object.assign(c,{status:"UNAVAILABLE",reason_code:"INVALID_SCENARIO",baseline_value:null,scenario_value:null,value_change:null,contributions:[]});
  assert.equal(decodeValuation(v,coverage(),req()).grid.heatmap_rows[0]![0]!.value_change,null);
});

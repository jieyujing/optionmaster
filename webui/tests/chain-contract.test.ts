import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { ApiError } from "../src/api";
import { buildChainRequest, decodeChainCoverage, decodeChainValuation } from "../src/chain-api";

const actual=JSON.parse(readFileSync("../docs/evidence/m1-d08/cu2211-20220907/actual_chain_api_result.json","utf8"));
const coverage=()=>decodeChainCoverage(structuredClone(actual.coverage_response));
const four=()=>structuredClone(actual.four_leg.doc);
const request=()=>structuredClone(actual.four_leg.request);

test("actual operator-approved chain coverage, explicit month distinction",()=>{
  const c=coverage();
  assert.equal(c.legs.length,66);
  assert.equal(c.months.reduce((n,m)=>n+m.discovered_contracts,0),340);
  assert.equal(c.months.reduce((n,m)=>n+m.admitted_contracts,0),66);
  assert.equal(c.legs.filter(l=>l.at_intrinsic).length,2);
});
for(const key of ["two_leg","four_leg","eight_leg"]){
  test("actual "+key+" wire payload and exact signed request",()=>{
    const c=coverage(),a=actual[key];
    const positions=Object.fromEntries(Object.entries(a.request.positions).map(([s,q])=>[s,String(q)]));
    const req=buildChainRequest(c,positions,a.request.scenario_at.slice(0,16),"10","6");
    assert.deepEqual(req,a.request);
    const v=decodeChainValuation(a.doc,c,req);
    assert.equal(v.legs.length,Object.keys(positions).length);
    assert.equal(v.grid.heatmap_rows.flat().length,25);
    assert.equal(v.grid.price_curve.length,9);
    assert.ok(v.grid.heatmap_rows.flat().every(p=>p.status==="OK"&&p.contributions.length===v.legs.length));
  });
}
for(const [name,mutate] of [
  ["missing production flag",d=>delete d.coverage.production_valuation_ready],
  ["true production flag",d=>d.coverage.production_valuation_ready=true],
  ["other month admitted",d=>d.coverage.months[0].admitted_contracts=70],
  ["wrong packet",d=>d.coverage.source_versions.packet_sha256="f".repeat(64)],
  ["duplicate contract",d=>d.coverage.legs[0]=d.coverage.legs[1]],
  ["intrinsic status tampered",d=>d.coverage.legs.find(x=>x.at_intrinsic).at_intrinsic=false],
  ["invalid effective version",d=>d.coverage.legs[0].effective_contract_version="unreviewed"],
] as [string,(d:any)=>void][]){
  test("reject chain coverage "+name,()=>{
    const d=structuredClone(actual.coverage_response);mutate(d);
    assert.throws(()=>decodeChainCoverage(d),ApiError);
  });
}
for(const [name,mutate] of [
  ["selected order",d=>d.valuation.legs.reverse()],
  ["quantity drift",d=>d.valuation.legs[0].quantity=7],
  ["settlement drift",d=>d.valuation.legs[0].market_option_price+=1],
  ["version drift",d=>d.valuation.legs[0].effective_contract_version="SHFE-REF:"+"f".repeat(64)],
  ["official record drift",d=>d.valuation.legs[0].official_record_id="unknown"],
  ["artifact drift",d=>d.valuation.source_versions.artifacts[0].sha256="f".repeat(64)],
  ["invalid registry",d=>d.valuation.source_versions.registry_sha256="bad"],
  ["missing contribution",d=>d.valuation.grid.heatmap_rows[0][0].contributions.pop()],
  ["wrong time",d=>d.valuation.scenario_at="2022-09-08T15:00:00+08:00"],
  ["origin changed",d=>d.valuation.grid.heatmap_rows[2][2].value_change=1],
  ["unavailable numerical cell",d=>{d.valuation.grid.heatmap_rows[0][0].status="UNAVAILABLE";d.valuation.grid.heatmap_rows[0][0].reason_code="INVALID_SCENARIO";}],
] as [string,(d:any)=>void][]){
  test("reject chain valuation "+name,()=>{
    const d=four();mutate(d);
    assert.throws(()=>decodeChainValuation(d,coverage(),request()),ApiError);
  });
}
test("invalid cell nulls remain unavailable",()=>{
  const d=four(),cell=d.valuation.grid.heatmap_rows[0][0];
  Object.assign(cell,{status:"UNAVAILABLE",reason_code:"INVALID_SCENARIO",baseline_value:null,scenario_value:null,value_change:null,contributions:[]});
  assert.equal(decodeChainValuation(d,coverage(),request()).grid.heatmap_rows[0]![0]!.value_change,null);
});
for(const [name,positions] of [
  ["one leg",{"CU2211C61000.XSGE":"1"}],
  ["nine legs",Object.fromEntries(coverage().legs.slice(0,9).map(l=>[l.source_symbol,"1"]))],
  ["unknown month",{"CU2211C61000.XSGE":"1","CU2212P61000.XSGE":"1"}],
  ["intrinsic only",{"CU2211P79000.XSGE":"1","CU2211P80000.XSGE":"1"}],
  ["zero quantity",{"CU2211C61000.XSGE":"0","CU2211P61000.XSGE":"1"}],
] as [string,Record<string,string>][]){
  test("reject chain request "+name,()=>{
    assert.throws(()=>buildChainRequest(coverage(),positions,"2022-09-07T15:00","10","6"),ApiError);
  });
}

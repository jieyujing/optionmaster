import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {decodeCatalogCoverage,decodeCatalogValuation,buildCatalogRequest} from "../src/catalog-api";
import {ApiError} from "../src/api";

const actual=JSON.parse(readFileSync("../docs/evidence/m1-d09/browser/actual_catalog_api_result.json","utf8"));
const first=decodeCatalogCoverage(actual.coverage_day1);
const second=decodeCatalogCoverage(actual.coverage_day2,"2022-09-08");
const near="CU2211C61000.XSGE",far="CU2212C61000.XSGE";
const clone=<T>(x:T):T=>structuredClone(x);
const invalid=(fn:()=>unknown)=>assert.throws(fn,e=>e instanceof ApiError&&e.code==="INVALID_RESPONSE");
test("actual approved v3 dated coverage and separate months",()=>{
  assert.equal(first.legs.length,132);assert.equal(second.legs.length,66);
  assert.deepEqual(first.available_dates,["2022-09-07","2022-09-08"]);
  assert.equal(first.approved_contract_dates,198);
  assert.equal(second.months.find(m=>m.underlying_id==="SHFE.cu2212")?.admitted_contracts,0);
});
for(const [name,c] of [["pair",first],["calendar",first],["four_leg",first],["eight_leg",first],["next_day",second]] as const){
  test("actual v3 "+name+" strict wire result",()=>{
    const item=actual[name];
    const v=decodeCatalogValuation(item.doc,c,item.request);
    assert.equal(v.production_valuation_ready,false);
    assert.equal(v.grid.heatmap_rows.flat().length,25);
  });
}
test("build exact calendar request with independent overrides",()=>{
  const r=buildCatalogRequest(first,{[near]:"-1",[far]:"1"},"2022-09-07T15:00","10","6",{"SHFE.cu2211":"2"},{"SHFE|cu2212|2022-11-24":"3"});
  assert.equal(r.trade_date,"2022-09-07");
  assert.deepEqual(r.positions,{[near]:-1,[far]:1});
  assert.deepEqual(r.price_shocks_by_underlying,{"SHFE.cu2211":0.02});
  assert.deepEqual(r.iv_shocks_by_expiry,{"SHFE|cu2212|2022-11-24":0.03});
});
test("client rejects wrong date, earliest expiry, intrinsic and duplicate selection",()=>{
  for(const [c,positions,at] of [
    [second,{[near]:"-1",[far]:"1"},"2022-09-08T15:00"],
    [first,{[near]:"-1",[far]:"1"},"2022-10-25T00:00"],
    [first,{[near]:"-1","CU2211P79000.XSGE":"1"},"2022-09-07T15:00"],
    [first,{[near]:"1"},"2022-09-07T15:00"],
  ] as const) assert.throws(()=>buildCatalogRequest(c,positions,at,"10","6"),e=>e instanceof ApiError&&e.code==="INVALID_INPUT");
});
const coverageMutations:Record<string,(c:any)=>void>={
  "wrong date":c=>{c.trade_date="2022-09-08";},
  "duplicate dates":c=>{c.available_dates=["2022-09-07","2022-09-07"];},
  "wrong child digest":c=>{c.months.find((m:any)=>m.admitted_contracts).child_packet_sha256="a".repeat(64);},
  "disabled month enabled":c=>{c.months.find((m:any)=>m.underlying_id==="SHFE.cu2210").admitted_contracts=70;},
  "cross-month mark drift":c=>{c.legs.find((l:any)=>l.underlying_id==="SHFE.cu2212").underlying_settlement=60580;},
  "missing readiness":c=>{delete c.production_valuation_ready;},
};
for(const [name,mutate] of Object.entries(coverageMutations))test("reject coverage "+name,()=>{
  const x=clone(actual.coverage_day1);mutate(x.coverage);invalid(()=>decodeCatalogCoverage(x));
});
const valuationMutations:Record<string,(v:any)=>void>={
  "cross-date valuation":v=>{v.trade_date="2022-09-08";},
  "wrong underlying mark":v=>{v.legs[0].market_underlying_price+=1;},
  "maturity drift":v=>{v.legs[1].baseline_time_to_expiry+=1/365;},
  "missing expiry":v=>{v.expiries.pop();},
  "aggregated cross-month exposure":v=>{v.underlying_exposures.pop();},
  "greek group drift":v=>{v.underlying_exposures[0].baseline_greeks.delta+=1;},
  "child digest drift":v=>{v.child_packet_sha256[0]="a".repeat(64);},
  "artifact drift":v=>{v.source_versions.artifacts[0].sha256="a".repeat(64);},
  "rate date drift":v=>{v.rate.quote_date="2022-09-07";},
  "origin drift":v=>{v.grid.heatmap_rows[2][2].value_change+=1;},
  "false numeric cell":v=>{v.grid.heatmap_rows[0][0].status="UNAVAILABLE";},
  "mismatched IV shock":v=>{v.iv_shocks_by_expiry["SHFE|cu2212|2022-11-24"]=0.04;},
};
for(const [name,mutate] of Object.entries(valuationMutations))test("reject valuation "+name,()=>{
  const item=actual.four_leg,x=clone(item.doc);mutate(x.valuation);
  invalid(()=>decodeCatalogValuation(x,first,item.request));
});

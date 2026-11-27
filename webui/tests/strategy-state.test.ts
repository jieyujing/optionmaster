import assert from "node:assert/strict";
import test from "node:test";
import type { StrategyChainDTO } from "../src/generated/strategy-types";
import { makeChain, fixtureCoverage } from "./strategy-fixtures";
import { emptyPortfolio, setSnapshot, instrumentForOption, instrumentForUnderlying, setQuantity,
  setEntryPrice, buildPayoffRequest, revaluePortfolio, instrumentKey, optionCount,
  coverageMonthUsable,coverageDayUsable,preferredCoverageDay,coverageChainChoices,coverageChainKey } from "../src/strategy-state";

const a=makeChain("2022-09-07","EX.TEST.F1");
const b=makeChain("2022-09-07","EX.TEST.F2");
const call=(chain:StrategyChainDTO)=>instrumentForOption(chain,chain.rows[0]!.call!);
test("signed quantity, zero removal, cross-month persistence and exact default entry",()=>{
  let state=setSnapshot(emptyPortfolio(),a.snapshot);
  state=setQuantity(state,call(a),"1");
  state=setQuantity(state,call(b),"-2");
  assert.equal(optionCount(state),2);
  const before=state.revision;
  state=setQuantity(state,call(a),"3");
  assert.equal(state.revision,before+1);
  assert.deepEqual(buildPayoffRequest(state)?.options.map(x=>[x.contract_id,x.quantity,x.entry_price]),[
    [a.rows[0]!.call!.contract_id,3,5],[b.rows[0]!.call!.contract_id,-2,5]]);
  state=setQuantity(state,call(a),"0");
  assert.equal(optionCount(state),1);
});
test("invalid draft and quantity cap cannot be sent, entry cost edit remains separate",()=>{
  let state=setSnapshot(emptyPortfolio(),a.snapshot);
  state=setQuantity(state,call(a),"1");
  state=setQuantity(state,call(a),"-");
  assert.equal(buildPayoffRequest(state),null);
  state=setQuantity(state,call(a),"1001");
  assert.equal(buildPayoffRequest(state),null);
  state=setQuantity(state,call(a),"-2");
  const key=instrumentKey(call(a));
  state=setEntryPrice(state,key,"0");
  const req=buildPayoffRequest(state)!;
  assert.equal(req.options[0]!.entry_origin,"USER_EDITED");
  assert.equal(req.options[0]!.entry_price,0);
  assert.equal(req.options[0]!.entry_snapshot_id,a.snapshot.snapshot_id);
  state=setEntryPrice(state,key,"-1");
  assert.equal(buildPayoffRequest(state),null);
});
test("mixed holding preserved but unavailable after corresponding option removed",()=>{
  let state=setSnapshot(emptyPortfolio(),a.snapshot);
  state=setQuantity(state,call(a),"1");
  state=setQuantity(state,instrumentForUnderlying(a),"-1");
  assert.equal(buildPayoffRequest(state)?.underlyings[0]?.quantity,-1);
  state=setQuantity(state,call(a),"0");
  assert.equal(state.positions.length,1);
  assert.equal(buildPayoffRequest(state),null);
  state=setQuantity(state,instrumentForUnderlying(a),"0");
  assert.equal(state.positions.length,0);
});
test("cross-date revalue preserves exact entry; absent month retained and blocks valuation",()=>{
  let state=setSnapshot(emptyPortfolio(),a.snapshot);
  state=setQuantity(state,call(a),"1");
  state=setEntryPrice(state,instrumentKey(call(a)),"6");
  state=setQuantity(state,call(b),"-1");
  const later=makeChain("2022-09-08","EX.TEST.F1");
  state=revaluePortfolio(state,later.snapshot,[later]);
  assert.equal(state.positions.length,2);
  assert.equal(state.positions[0]!.entry.entry_price,6);
  assert.equal(state.positions[0]!.entry.entry_trade_date,"2022-09-07");
  assert.equal(state.positions[1]!.unavailable,"INSTRUMENT_UNAVAILABLE");
  assert.equal(buildPayoffRequest(state),null);
  state=setSnapshot(state,later.snapshot);
  assert.equal(state.positions.length,0);
});
test("limit eight option legs, unadmitted quote rejected",()=>{
  let state=setSnapshot(emptyPortfolio(),a.snapshot);
  const base=a.rows[0]!.call!;
  for(let i=0;i<8;i++){
    state=setQuantity(state,instrumentForOption(a,{...base,contract_id:"EX.C"+i}),"1");
  }
  state=setQuantity(state,instrumentForOption(a,{...base,contract_id:"EX.C8"}),"1");
  assert.equal(optionCount(state),8);
  assert.equal(buildPayoffRequest(state),null);
  state=setQuantity(state,instrumentForOption(a,{...base,contract_id:"EX.C8"}),"0");
  assert.equal(buildPayoffRequest(state)?.options.length,8);
  state=setQuantity(state,instrumentForOption(a,{...base,contract_id:"BAD",payoff_eligibility:{status:"UNAVAILABLE",reason:"CANDIDATE_ONLY"}}),"1");
  assert.equal(optionCount(state),8);
});

test("historical partial coverage is selectable without certifying a complete day",()=>{
  const p=structuredClone(fixtureCoverage.products[0]!);
  const older=p.days[0]!,later=p.days[1]!;
  later.status="INCOMPLETE";later.reason="SOURCE_INCOMPLETE";
  later.months[0]={...later.months[0]!,status:"INCOMPLETE",reason:"SOURCE_INCOMPLETE",admitted_contracts:2};
  assert.equal(coverageMonthUsable(later.months[0]!),true);
  assert.equal(coverageMonthUsable(later.months[1]!),false);
  assert.equal(coverageDayUsable(later),true);
  assert.equal(preferredCoverageDay(p)?.trade_date,later.trade_date);
  assert.equal(p.latest_complete_trade_date,null);
  assert.equal(coverageMonthUsable({...later.months[0]!,admitted_contracts:0}),false);
  assert.equal(coverageMonthUsable({...later.months[0]!,admitted_contracts:10}),false);
  p.latest_complete_trade_date=older.trade_date;
  assert.equal(preferredCoverageDay(p)?.trade_date,older.trade_date);
});
test("direct chain choices include earlier admitted expiries and exclude discovery-only months",()=>{
  const p=structuredClone(fixtureCoverage.products[0]!);
  const choices=coverageChainChoices(p);
  assert.deepEqual(choices.map(c=>c.key),[
    coverageChainKey("2022-09-08","EX.TEST.F1"),
    coverageChainKey("2022-09-07","EX.TEST.F1"),
    coverageChainKey("2022-09-07","EX.TEST.F2"),
  ]);
  assert.equal(choices.find(c=>c.key===coverageChainKey("2022-09-07","EX.TEST.F2"))?.month.underlying_id,"EX.TEST.F2");
  p.days[0]!.status="NO_SESSION";
  assert.equal(coverageChainChoices(p).length,1);
  assert.equal(p.days[1]!.months.length,2); // No mutation or false admission.
});

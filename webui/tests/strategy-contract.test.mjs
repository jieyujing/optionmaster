// D12 structural client validation; server additionally checks semantic invariants.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { validateStrategyEnvelope, validateStrategyPayoffRequest, validateStrategyValuationRequest }
  from "../src/generated/strategy-validators.mjs";
const fixtures = JSON.parse(await readFile("../docs/evidence/m1-d12/contract-fixtures.json", "utf8"));
test("synthetic payoff/model/data contracts", () => {
  assert.equal(validateStrategyPayoffRequest(fixtures.payoff_request), true);
  assert.equal(validateStrategyValuationRequest(fixtures.valuation_request), true);
  for (const key of ["payoff_response","model_partial_response","coverage_response","chain_response"]) {
    assert.equal(validateStrategyEnvelope(fixtures[key]), true, key);
  }
});
test("strict shape forbids trust fields, zero/coerced quantities and oversized axes", () => {
  for (const patch of [
    {source_price:100}, {model:"BLACK_76"}, {portfolio_revision:true}, {snapshot_id:"fake"},
    {options:[]}, {curve_price_returns:Array(12).fill(0)}, {global_price_shock:1.01},
  ]) {
    assert.equal(validateStrategyPayoffRequest({...fixtures.payoff_request,...patch}),false);
  }
  for (const quantity of [0,1001,"1",true]) {
    assert.equal(validateStrategyPayoffRequest({...fixtures.payoff_request,
      options:[{...fixtures.payoff_request.options[0],quantity}]}),false);
  }
  assert.equal(validateStrategyValuationRequest({...fixtures.valuation_request,scenario_at:"2022-09-07T15:00:00"}),false);
});
test("strict response requires explicit null and never accepts non-finite values", () => {
  const missing = structuredClone(fixtures.chain_response);
  delete missing.chain.rows[0].put;
  assert.equal(validateStrategyEnvelope(missing),false);
  const nonfinite = structuredClone(fixtures.payoff_response);
  nonfinite.payoff.terminal.points[0].pnl = Infinity;
  assert.equal(validateStrategyEnvelope(nonfinite),false);
});

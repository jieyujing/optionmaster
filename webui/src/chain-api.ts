import type { ChainApiEnvelope, ChainCoverageDTO, ChainValuationDTO, ChainValuationRequestDTO } from "./generated/chain-types";
import { validateChainEnvelope, validateChainRequest } from "./generated/chain-validators.mjs";
import { ApiError, buildRequestInputs, close, fetchJson, requireInput, requireResponse, sha, validateGrid } from "./api";

export type { ChainCoverageDTO, ChainCoverageLegDTO, ChainValuationDTO, ChainValuationRequestDTO } from "./generated/chain-types";
const PIN="9216e5598c11ef2736c353dddd08bd7c2efedf742935a9a7ee9afd8383ee8846";
const COVERAGE="/api/v2/research/coverage";
const VALUATION="/api/v2/research/valuations";
const EXPECTED_MONTHS:Record<string,number>={"SHFE.cu2210":70,"SHFE.cu2211":66,"SHFE.cu2212":66,"SHFE.cu2301":54,"SHFE.cu2302":44,"SHFE.cu2303":40};

function envelope(value:unknown):ChainApiEnvelope{
  requireResponse(validateChainEnvelope(value));
  if(value.status==="UNAVAILABLE"){
    requireResponse(value.reason_code!==null&&value.coverage===null&&value.valuation===null);
    throw new ApiError(value.reason_code);
  }
  requireResponse(value.reason_code===null&&Number(value.coverage!==null)+Number(value.valuation!==null)===1);
  return value;
}
function sourceValid(v:ChainCoverageDTO["source_versions"]){
  return v.packet_sha256===PIN&&v.artifacts.length===6&&
    new Set(v.artifacts.map(a=>a.artifact)).size===6&&v.artifacts.every(a=>sha(a.sha256));
}
export function decodeChainCoverage(value:unknown):ChainCoverageDTO{
  const e=envelope(value),c=e.coverage;
  requireResponse(c!==null&&e.valuation===null&&c.production_valuation_ready===false&&c.trade_date==="2022-09-07"&&
    c.price_basis==="SETTLEMENT"&&c.requested_legs===66&&c.admitted_legs===66&&c.legs.length===66&&sourceValid(c.source_versions)&&
    Date.parse(c.data_observed_at)<=Date.parse(c.approved_at)&&Date.parse(c.approved_at)<=Date.parse(c.admitted_at));
  requireResponse(c.months.length===6&&new Set(c.months.map(m=>m.underlying_id)).size===6&&
    c.months.every(m=>m.discovered_contracts===EXPECTED_MONTHS[m.underlying_id]&&m.admitted_contracts===(m.underlying_id==="SHFE.cu2211"?66:0)));
  requireResponse(new Set(c.legs.map(x=>x.source_symbol)).size===66&&new Set(c.legs.map(x=>x.contract_id)).size===66&&
    new Set(c.legs.map(x=>x.official_record_id)).size===66&&new Set(c.legs.map(x=>x.strike)).size===33&&
    new Set(c.legs.map(x=>x.strike+":"+x.option_type)).size===66&&
    c.legs.filter(x=>x.option_type==="CALL").length===33&&
    c.legs.every((x,i)=> (i===0||c.legs[i-1]!.source_symbol<x.source_symbol)&&x.underlying_id==="SHFE.cu2211"&&
      x.source_namespace==="quantfeed.option/JQ"&&x.source_exchange==="XSGE"&&x.expiry_date==="2022-10-25"&&
      x.contract_multiplier===5&&x.exercise_style==="AMERICAN"&&x.strike>0&&x.settlement>0&&x.close>0&&
      x.underlying_settlement===60580&&x.volume>=0&&x.open_interest>=0&&
      /^SHFE-REF:[0-9a-f]{64}$/.test(x.effective_contract_version)&&
      x.at_intrinsic===(x.settlement<=Math.max(0,(x.option_type==="CALL"?1:-1)*(x.underlying_settlement-x.strike)))));
  return c;
}
export function buildChainRequest(c:ChainCoverageDTO,positions:Record<string,string>,scenario:string,priceRange:string,ivRange:string):ChainValuationRequestDTO{
  const legs=c.legs.filter(l=>Object.hasOwn(positions,l.source_symbol));
  requireInput(legs.length===Object.keys(positions).length,"存在未准入的合约");
  requireInput(legs.every(l=>!l.at_intrinsic),"所选合约结算价等于或低于内在价值，无法可靠反解 IV");
  const req:ChainValuationRequestDTO={
    schema_version:"2.0",slice_id:c.slice_id,
    ...buildRequestInputs({trade_date:c.trade_date,legs},legs.map(l=>positions[l.source_symbol]!),scenario,priceRange,ivRange),
  };
  requireInput(validateChainRequest(req),"请求超出 v2 接口限制");
  return req;
}
export function decodeChainValuation(value:unknown,c:ChainCoverageDTO,req:ChainValuationRequestDTO):ChainValuationDTO{
  const e=envelope(value),v=e.valuation;
  const legs=c.legs.filter(l=>Object.hasOwn(req.positions,l.source_symbol));
  requireResponse(v!==null&&e.coverage===null&&v.production_valuation_ready===false&&v.slice_id===c.slice_id&&
    v.trade_date===c.trade_date&&v.price_basis===c.price_basis&&Date.parse(v.scenario_at)===Date.parse(req.scenario_at)&&
    v.approved_at===c.approved_at&&v.data_observed_at===c.data_observed_at&&sourceValid(v.source_versions)&&
    sha(v.source_versions.registry_sha256)&&v.source_versions.artifacts.every(a=>c.source_versions.artifacts.some(b=>a.artifact===b.artifact&&a.sha256===b.sha256))&&
    v.legs.length===legs.length&&legs.length===Object.keys(req.positions).length&&v.underlying_exposures.length===1&&v.underlying_exposures[0]?.underlying_id==="SHFE.cu2211");
  requireResponse(v.legs.every((x,i)=>{
    const p=legs[i];
    return p!==undefined&&!p.at_intrinsic&&x.source_symbol===p.source_symbol&&x.contract_id===p.contract_id&&
      x.source_namespace===p.source_namespace&&x.source_exchange===p.source_exchange&&x.underlying_id===p.underlying_id&&
      x.expiry_date===p.expiry_date&&x.option_type===p.option_type&&x.exercise_style===p.exercise_style&&
      x.strike===p.strike&&x.contract_multiplier===p.contract_multiplier&&x.quantity===req.positions[x.source_symbol]&&
      x.effective_contract_version===p.effective_contract_version&&x.official_record_id===p.official_record_id&&
      x.market_option_price===p.settlement&&x.market_underlying_price===p.underlying_settlement&&
      x.implied_volatility>0&&x.scenario_iv===x.implied_volatility&&x.raw_contract_version===""&&x.raw_exercise_style==="UNKNOWN"&&
      close(x.baseline_value,x.quantity*x.contract_multiplier*x.market_option_price);
  }));
  validateGrid(v,req);
  return v;
}
export async function fetchChainCoverage(token:string,signal:AbortSignal):Promise<ChainCoverageDTO>{
  return decodeChainCoverage(await fetchJson(COVERAGE,token,signal,undefined,envelope));
}
export async function fetchChainValuation(token:string,c:ChainCoverageDTO,req:ChainValuationRequestDTO,signal:AbortSignal):Promise<ChainValuationDTO>{
  return decodeChainValuation(await fetchJson(VALUATION,token,signal,req,envelope),c,req);
}

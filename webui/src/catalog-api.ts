import type { CatalogApiEnvelope, CatalogCoverageDTO, CatalogValuationDTO, CatalogValuationRequestDTO } from "./generated/catalog-types";
import { validateCatalogEnvelope, validateCatalogRequest } from "./generated/catalog-validators.mjs";
import { ApiError, buildRequestInputs, close, fetchJson, requireInput, requireResponse, sha, validateGrid } from "./api";

export type { CatalogCoverageDTO, CatalogValuationDTO, CatalogValuationRequestDTO } from "./generated/catalog-types";
const PIN="2c57d7df89e87db37749d4217c68117bd1d3ccc4c332bf1cfa41b4fb28264fea";
const DATES=["2022-09-07","2022-09-08"];
const EXPECTED_MONTHS:Record<string,number>={"SHFE.cu2210":70,"SHFE.cu2211":66,"SHFE.cu2212":66,"SHFE.cu2301":54,"SHFE.cu2302":44,"SHFE.cu2303":40};
const APPROVED:Record<string,Record<string,{pin:string,settlement:number,expiry:string}>>={
  "2022-09-07":{
    "SHFE.cu2211":{pin:"9216e5598c11ef2736c353dddd08bd7c2efedf742935a9a7ee9afd8383ee8846",settlement:60580,expiry:"2022-10-25"},
    "SHFE.cu2212":{pin:"366bac3d688ff6a3311354131d26149c3ea9f6cca652bc664ada8a8c5714b122",settlement:60240,expiry:"2022-11-24"},
  },
  "2022-09-08":{
    "SHFE.cu2211":{pin:"d44434e8f752fef05744f64028d60f046fb99df0ad8f1170117dd654dba94efa",settlement:60580,expiry:"2022-10-25"},
  },
};
const COVERAGE="/api/v3/research/coverage";
const VALUATION="/api/v3/research/valuations";
const RATE_DAY:Record<string,string>={"2022-09-07":"2022-09-06","2022-09-08":"2022-09-07"};
const RATE_VALUE:Record<string,number>={"2022-09-07":1.4442,"2022-09-08":1.4628};
export const expiryKey=(underlying:string,expiry:string)=>underlying.replace(".","|")+"|"+expiry;
const time=(day:string)=>Date.parse(day+"T15:00:00+08:00");
const years=(day:string,expiry:string)=>(time(expiry)-time(day))/(365*86400000);
const sameMap=(a:Record<string,number>,b:Record<string,number>)=>Object.keys(a).length===Object.keys(b).length&&Object.entries(a).every(([k,v])=>b[k]===v);
function envelope(value:unknown):CatalogApiEnvelope{
  requireResponse(validateCatalogEnvelope(value));
  if(value.status==="UNAVAILABLE"){
    requireResponse(value.reason_code!==null&&value.coverage===null&&value.valuation===null);
    throw new ApiError(value.reason_code);
  }
  requireResponse(value.reason_code===null&&Number(value.coverage!==null)+Number(value.valuation!==null)===1);
  return value;
}
function sourceValid(v:CatalogCoverageDTO["source_versions"]){
  return v.packet_sha256===PIN&&v.artifacts.length===18&&
    new Set(v.artifacts.map(a=>a.artifact)).size===18&&v.artifacts.every(a=>sha(a.sha256));
}
export function decodeCatalogCoverage(value:unknown,requestedDay?:string):CatalogCoverageDTO{
  const e=envelope(value),c=e.coverage;
  requireResponse(c!==null&&e.valuation===null&&c.production_valuation_ready===false&&
    c.price_basis==="SETTLEMENT"&&sourceValid(c.source_versions)&&
    DATES.includes(c.trade_date)&&c.trade_date===(requestedDay??DATES[0])&&
    JSON.stringify(c.available_dates)===JSON.stringify(DATES)&&c.approved_contract_dates===198&&
    Date.parse(c.data_observed_at)<=Date.parse(c.approved_at)&&Date.parse(c.approved_at)<=Date.parse(c.admitted_at));
  const scope=APPROVED[c.trade_date]!;
  const count=Object.keys(scope).length*66;
  requireResponse(c.requested_legs===count&&c.admitted_legs===count&&c.legs.length===count&&
    c.months.length===6&&new Set(c.months.map(m=>m.underlying_id)).size===6&&
    c.months.every(m=>{
      const a=scope[m.underlying_id];
      return m.discovered_contracts===EXPECTED_MONTHS[m.underlying_id]&&m.admitted_contracts===(a?66:0)&&
        (a?m.child_packet_sha256===a.pin&&m.underlying_settlement===a.settlement&&m.expiry_date===a.expiry&&
        m.observed_at!==null&&Date.parse(m.observed_at)<=Date.parse(c.data_observed_at):
        m.child_packet_sha256===null&&m.observed_at===null&&m.underlying_settlement===null);
    }));
  requireResponse(new Set(c.legs.map(x=>x.source_symbol)).size===count&&new Set(c.legs.map(x=>x.contract_id)).size===count&&
    new Set(c.legs.map(x=>x.official_record_id)).size===count&&
    Object.keys(scope).every(u=>{
      const legs=c.legs.filter(x=>x.underlying_id===u);
      return legs.length===66&&new Set(legs.map(x=>x.option_type+":"+x.strike)).size===66&&
        legs.filter(x=>x.option_type==="CALL").length===33;
    })&&c.legs.every((x,i)=>{
      const a=scope[x.underlying_id];
      return !!a&&(i===0||c.legs[i-1]!.source_symbol<x.source_symbol)&&
        x.source_namespace==="quantfeed.option/JQ"&&x.source_exchange==="XSGE"&&x.expiry_date===a.expiry&&
        x.contract_multiplier===5&&x.exercise_style==="AMERICAN"&&x.strike>0&&x.settlement>0&&x.close>0&&
        x.underlying_settlement===a.settlement&&x.volume>=0&&x.open_interest>=0&&
        /^SHFE-REF:[0-9a-f]{64}$/.test(x.effective_contract_version)&&
        x.at_intrinsic===(x.settlement<=Math.max(0,(x.option_type==="CALL"?1:-1)*(x.underlying_settlement-x.strike)));
    }));
  return c;
}
function parseOverrides(raw:Record<string,string>,known:Set<string>,label:string):Record<string,number>{
  return Object.fromEntries(Object.entries(raw).filter(([k,v])=>known.has(k)&&v.trim()!=="").map(([k,v])=>{
    requireInput(/^-?(?:0|[1-9]\d{0,2})(?:\.\d{1,2})?$/.test(v),label+"必须为有效数值");
    const n=Number(v);
    requireInput(n>=-100&&n<=100,label+"须在 -100 至 100 之间");
    return [k,n/100];
  }));
}
export function buildCatalogRequest(c:CatalogCoverageDTO,positions:Record<string,string>,scenario:string,
    priceRange:string,ivRange:string,priceOverrides:Record<string,string>={},ivOverrides:Record<string,string>={}):CatalogValuationRequestDTO{
  const legs=c.legs.filter(l=>Object.hasOwn(positions,l.source_symbol));
  requireInput(legs.length===Object.keys(positions).length,"存在未准入的合约");
  requireInput(legs.every(l=>!l.at_intrinsic),"所选合约无法可靠反解 IV");
  const req:CatalogValuationRequestDTO={
    schema_version:"3.0",catalog_id:c.catalog_id,trade_date:c.trade_date,
    ...buildRequestInputs({trade_date:c.trade_date,legs},legs.map(l=>positions[l.source_symbol]!),scenario,priceRange,ivRange),
    price_shocks_by_underlying:parseOverrides(priceOverrides,new Set(legs.map(l=>l.underlying_id)),"标的价格冲击"),
    iv_shocks_by_expiry:parseOverrides(ivOverrides,new Set(legs.map(l=>expiryKey(l.underlying_id,l.expiry_date))),"到期日 IV 冲击"),
  };
  requireInput(validateCatalogRequest(req),"请求超出 v3 接口限制");
  return req;
}
export function decodeCatalogValuation(value:unknown,c:CatalogCoverageDTO,req:CatalogValuationRequestDTO):CatalogValuationDTO{
  const e=envelope(value),v=e.valuation,legs=c.legs.filter(l=>Object.hasOwn(req.positions,l.source_symbol));
  const underlyings=[...new Set(legs.map(l=>l.underlying_id))].sort();
  const expiries=[...new Set(legs.map(l=>expiryKey(l.underlying_id,l.expiry_date)))];
  const months=c.months.filter(m=>underlyings.includes(m.underlying_id));
  requireResponse(v!==null&&e.coverage===null&&v.production_valuation_ready===false&&v.catalog_id===c.catalog_id&&
    v.trade_date===req.trade_date&&req.trade_date===c.trade_date&&v.price_basis===c.price_basis&&
    Date.parse(v.scenario_at)===Date.parse(req.scenario_at)&&Date.parse(v.reference_at)===time(c.trade_date)&&
    v.approved_at===c.approved_at&&Date.parse(v.admitted_at)>=Date.parse(v.approved_at)&&
    Date.parse(v.data_observed_at)===Math.max(...months.map(m=>Date.parse(m.observed_at!)))&&
    sourceValid(v.source_versions)&&sha(v.source_versions.registry_sha256)&&
    v.source_versions.artifacts.every(a=>c.source_versions.artifacts.some(b=>a.artifact===b.artifact&&a.sha256===b.sha256))&&
    JSON.stringify(v.child_packet_sha256)===JSON.stringify(months.map(m=>m.child_packet_sha256).sort())&&
    v.rate.quote_date===RATE_DAY[c.trade_date]&&v.rate.quoted_percent===RATE_VALUE[c.trade_date]&&
    v.legs.length===legs.length&&legs.length===Object.keys(req.positions).length&&
    JSON.stringify(v.underlying_exposures.map(x=>x.underlying_id))===JSON.stringify(underlyings)&&
    v.expiries.length===expiries.length&&new Set(v.expiries.map(x=>x.expiry_id)).size===expiries.length&&
    sameMap(v.price_shocks_by_underlying,req.price_shocks_by_underlying)&&sameMap(v.iv_shocks_by_expiry,req.iv_shocks_by_expiry));
  requireResponse(v.legs.every((x,i)=>{
    const p=legs[i];
    if(!p)return false;
    const id=expiryKey(p.underlying_id,p.expiry_date),expiry=v.expiries.find(a=>a.expiry_id===id);
    const maturity=years(c.trade_date,p.expiry_date);
    const scenarioMaturity=maturity-(Date.parse(req.scenario_at)-time(c.trade_date))/(365*86400000);
    return !p.at_intrinsic&&x.source_symbol===p.source_symbol&&x.contract_id===p.contract_id&&
      x.source_namespace===p.source_namespace&&x.source_exchange===p.source_exchange&&x.underlying_id===p.underlying_id&&
      x.expiry_date===p.expiry_date&&x.option_type===p.option_type&&x.exercise_style===p.exercise_style&&
      x.strike===p.strike&&x.contract_multiplier===p.contract_multiplier&&x.quantity===req.positions[x.source_symbol]&&
      x.effective_contract_version===p.effective_contract_version&&x.official_record_id===p.official_record_id&&
      x.market_option_price===p.settlement&&x.market_underlying_price===p.underlying_settlement&&
      x.implied_volatility>0&&close(x.scenario_iv,x.implied_volatility+(req.iv_shocks_by_expiry[id]??0))&&
      x.raw_contract_version===""&&x.raw_exercise_style==="UNKNOWN"&&
      x.expiry_id===id&&!!expiry&&expiry.underlying_id===x.underlying_id&&expiry.expiry_date===x.expiry_date&&
      Date.parse(expiry.nominal_expiry_at)===time(p.expiry_date)&&close(expiry.baseline_time_to_expiry,maturity)&&
      close(x.baseline_time_to_expiry,maturity)&&close(x.scenario_time_to_expiry,scenarioMaturity)&&scenarioMaturity>0&&
      close(x.baseline_value,x.quantity*x.contract_multiplier*x.market_option_price);
  }));
  for(const exposure of v.underlying_exposures){
    const selected=v.legs.filter(l=>l.underlying_id===exposure.underlying_id);
    for(const field of ["baseline_greeks","scenario_greeks"] as const)
      for(const key of ["delta","gamma","theta_annual","vega_per_unit"] as const)
        requireResponse(close(exposure[field][key],selected.reduce((sum,l)=>sum+l[field][key],0)));
  }
  validateGrid(v,req);
  return v;
}
export async function fetchCatalogCoverage(token:string,signal:AbortSignal,day?:string):Promise<CatalogCoverageDTO>{
  return decodeCatalogCoverage(await fetchJson(COVERAGE+(day?"?trade_date="+encodeURIComponent(day):""),token,signal,undefined,envelope),day);
}
export async function fetchCatalogValuation(token:string,c:CatalogCoverageDTO,req:CatalogValuationRequestDTO,signal:AbortSignal):Promise<CatalogValuationDTO>{
  return decodeCatalogValuation(await fetchJson(VALUATION,token,signal,req,envelope),c,req);
}

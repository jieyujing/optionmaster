import { ApiError, close, fetchJson, requireResponse } from "./api";
import type { StrategyCoverageDTO, StrategyChainDTO, StrategyEnvelopeDTO, StrategyPayoffDTO, StrategyValuationDTO, StrategyPayoffRequestDTO, StrategyValuationRequestDTO, StrategyCurveDTO } from "./generated/strategy-types";
import { validateStrategyEnvelope, validateStrategyPayoffRequest, validateStrategyValuationRequest } from "./generated/strategy-validators.mjs";

import { decodeQuoteDetails,type QuoteDetails } from "./quote-details";

export const STRATEGY_PATH="/api/v4/research/strategy";
export type ChainSelector={exchange:string;product:string;trade_date:string;underlying_id:string;snapshot_id?:string;cursor?:string};
export type LocalChainModel="AMERICAN_BINOMIAL"|"BLACK_76"|"BLACK_SCHOLES";
export type LocalModelInfo={chain_id:string;raw_styles:string[];default_model:LocalChainModel;effective_model:LocalChainModel;models:LocalChainModel[];rate:number;user_override:boolean;underlying_source:string;vendor_underlying:string;model_policy:"CFFEX_EUROPEAN_BLACK76"|"LOCAL_CHAIN_MODEL";pricing_basis:"CHAIN_ADJUSTED_DAILY_MARK";atm_strike:number|null;pricing_underlying:number|null;underlying_adjustment:number|null};
export interface StrategyDataClient{
  chainKey?(chain:StrategyChainDTO):string;
  modelInfo?(chainId:string,signal:AbortSignal):Promise<LocalModelInfo>;
  setModel?(chainId:string,model:LocalChainModel,signal:AbortSignal):Promise<LocalModelInfo>;
  coverage(signal:AbortSignal):Promise<StrategyCoverageDTO>;
  chain(selector:ChainSelector,signal:AbortSignal):Promise<StrategyChainDTO>;
  quoteDetails?(chain:StrategyChainDTO,signal:AbortSignal):Promise<QuoteDetails>;
}
export function strategyEnvelope(raw:unknown):StrategyEnvelopeDTO{
  requireResponse(validateStrategyEnvelope(raw));
  const payloads=[raw.chain,raw.coverage,raw.payoff,raw.valuation].filter(v=>v!==null);
  if(raw.status==="UNAVAILABLE"){
    requireResponse(payloads.length===0&&raw.reason!==null);
    throw new ApiError(raw.reason);
  }
  requireResponse(payloads.length===1);
  return raw;
}
export interface StrategyCalculationClient extends StrategyDataClient{
  payoff(req:StrategyPayoffRequestDTO,signal:AbortSignal):Promise<StrategyPayoffDTO>;
  valuation(req:StrategyValuationRequestDTO,signal:AbortSignal):Promise<StrategyValuationDTO>;
}
function pyFloat(v:number):string{
  // Match the validated Python float JSON representation, not the raw JS
  // integer token. Unrepresentable discrepancies fail the digest check closed.
  requireResponse(Number.isFinite(v));
  if(v===0)return "0.0";
  const s=Math.abs(v).toString().toLowerCase(),[mantissa,exponent]=s.split("e");
  const dot=mantissa!.indexOf("."),whole=dot<0?mantissa!.length:dot;
  const digits=mantissa!.replace(".","");
  const first=digits.search(/[1-9]/),e=Number(exponent??0)+whole-first-1;
  const d=digits.slice(first).replace(/0+$/,"");
  const sign=v<0?"-":"";
  if(e< -4||e>=16)return sign+d[0]+(d.length>1?"."+d.slice(1):"")+"e"+(e<0?"-":"+")+String(Math.abs(e)).padStart(2,"0");
  const index=e+1;
  if(index<=0)return sign+"0."+"0".repeat(-index)+d;
  if(index>=d.length)return sign+d+"0".repeat(index-d.length)+".0";
  return sign+d.slice(0,index)+"."+d.slice(index);
}
const floatFields=new Set(["entry_price","curve_price_returns","global_price_shock","price_shocks_by_underlying","global_iv_shock","iv_shocks_by_expiry"]);
const pyString=(s:string)=>JSON.stringify(s).replace(/[^\x00-\x7e]/g,c=>"\\u"+c.charCodeAt(0).toString(16).padStart(4,"0"));
function codepointOrder(a:string,b:string){
  const aa=Array.from(a),bb=Array.from(b);
  for(let i=0;i<Math.min(aa.length,bb.length);i++){const diff=aa[i]!.codePointAt(0)!-bb[i]!.codePointAt(0)!;if(diff)return diff;}
  return aa.length-bb.length;
}
export function canonicalStrategyRequest(req:StrategyPayoffRequestDTO|StrategyValuationRequestDTO):string{
  function encode(v:unknown,float=false):string{
    if(typeof v==="number")return float?pyFloat(v):String(v);
    if(typeof v==="string")return pyString(v);
    if(v===null||typeof v==="boolean")return String(v);
    if(Array.isArray(v))return "["+v.map(x=>encode(x,float)).join(",")+"]";
    requireResponse(typeof v==="object"&&v!==null);
    const map=v as Record<string,unknown>;
    return "{"+Object.keys(map).sort(codepointOrder).map(k=>pyString(k)+":"+encode(map[k],float||floatFields.has(k))).join(",")+"}";
  }
  return encode(req);
}
export async function strategyInputHash(req:StrategyPayoffRequestDTO|StrategyValuationRequestDTO){
  const bytes=new TextEncoder().encode(canonicalStrategyRequest(req));
  const digest=await crypto.subtle.digest("SHA-256",bytes);
  return Array.from(new Uint8Array(digest),n=>n.toString(16).padStart(2,"0")).join("");
}
function sameRecord(a:Record<string,number>,b:Record<string,number>){
  return Object.keys(a).length===Object.keys(b).length&&Object.entries(a).every(([k,v])=>b[k]===v);
}
function validateCurve(curve:StrategyCurveDTO,ids:string[],underlyingIds:string[],axis:number[]){
  const good=curve.points.filter(p=>p.status==="OK").length;
  requireResponse(curve.status==="OK"?good>0&&good===curve.points.length&&curve.reason===null:
    curve.status==="PARTIAL"?good>0&&good<curve.points.length&&curve.reason!==null:good===0&&curve.reason!==null);
  if(curve.points.length){
    requireResponse(axis.every(x=>curve.points.some(p=>p.price_return===x)));
    if(curve.kind!=="TERMINAL")requireResponse(curve.points.length===axis.length);
  }
  let previous=-Infinity;
  for(const p of curve.points){
    requireResponse(p.price_return>previous);previous=p.price_return;
    if(p.status==="OK")requireResponse(p.pnl!==null&&p.reason===null&&Object.keys(p.underlying_prices).length===underlyingIds.length&&underlyingIds.every(k=>Object.hasOwn(p.underlying_prices,k))&&p.contributions.length===ids.length&&
      p.contributions.every((c,i)=>c.instrument_id===ids[i])&&close(p.contributions.reduce((sum,c)=>sum+c.pnl,0),p.pnl));
    else requireResponse(p.pnl===null&&p.reason!==null&&p.contributions.length===0);
  }
}
export async function decodeCalculation(raw:unknown,req:StrategyPayoffRequestDTO|StrategyValuationRequestDTO,model:boolean){
  const e=strategyEnvelope(raw);
  const result=model?e.valuation:e.payoff;
  requireResponse(result!==null&&e.portfolio_revision===req.portfolio_revision&&e.snapshot_id===req.snapshot_id&&
    e.input_hash===await strategyInputHash(req)&&result.snapshot.snapshot_id===req.snapshot_id&&
    result.snapshot.provider!=="SYNTHETIC_FIXTURE"&&result.reference_underlying_id===req.reference_underlying_id&&
    result.global_price_shock===req.global_price_shock&&sameRecord(result.price_shocks_by_underlying,req.price_shocks_by_underlying));
  const ordered=[...req.options.map(o=>({...o,instrument_id:o.contract_id,kind:"OPTION"})),
                 ...req.underlyings.map(u=>({...u,instrument_id:u.underlying_id,kind:"UNDERLYING"}))];
  requireResponse(result.legs.length===ordered.length&&result.legs.every((l,i)=>{
    const q=ordered[i]!;return l.instrument_id===q.instrument_id&&l.kind===q.kind&&l.quantity===q.quantity&&
      l.entry_price===q.entry_price&&l.entry_trade_date===q.entry_trade_date&&l.entry_snapshot_id===q.entry_snapshot_id&&l.entry_origin===q.entry_origin;
  }));
  const ids=ordered.map(x=>x.instrument_id),underlyingIds=[...new Set(result.legs.map(l=>l.underlying_id))];
  if("terminal" in result){requireResponse(result.terminal.kind==="TERMINAL"||result.terminal.kind==="FIRST_EXPIRY_CONDITIONAL");validateCurve(result.terminal,ids,underlyingIds,req.curve_price_returns);}
  else{
    requireResponse("scenario_at" in req&&result.global_iv_shock===req.global_iv_shock&&sameRecord(result.iv_shocks_by_expiry,req.iv_shocks_by_expiry));
    requireResponse((result.current.evaluation_at===null||Date.parse(result.current.evaluation_at)===Date.parse(result.snapshot.reference_at))&&(result.scenario.evaluation_at===null||Date.parse(result.scenario.evaluation_at)===Date.parse(req.scenario_at)));
    requireResponse(result.current.kind==="CURRENT"&&result.scenario.kind==="SCENARIO"&&["TERMINAL","FIRST_EXPIRY_CONDITIONAL"].includes(result.horizon.kind));
    for(const curve of [result.current,result.scenario,result.horizon])validateCurve(curve,ids,underlyingIds,req.curve_price_returns);
  }
  return result;
}
export function createStrategyClient(token:string):StrategyCalculationClient{
  const get=async(path:string,signal:AbortSignal)=>strategyEnvelope(await fetchJson(path,token,signal,undefined,strategyEnvelope));
  return {
    async payoff(req,signal){
      requireResponse(validateStrategyPayoffRequest(req));
      const result=await decodeCalculation(await fetchJson(STRATEGY_PATH+"/payoffs",token,signal,req,strategyEnvelope),req,false);
      requireResponse("terminal" in result);return result;
    },
    async valuation(req,signal){
      requireResponse(validateStrategyValuationRequest(req));
      const result=await decodeCalculation(await fetchJson(STRATEGY_PATH+"/valuations",token,signal,req,strategyEnvelope),req,true);
      requireResponse("current" in result);return result;
    },
    async coverage(signal){
      const products:StrategyCoverageDTO["products"]=[];
      const keys=new Set<string>(),cursors=new Set<string>();
      let cursor:string|null=null;
      for(let page=0;page<8;page++){
        const e=await get(STRATEGY_PATH+"/coverage"+(cursor?"?"+new URLSearchParams({cursor}):""),signal);
        requireResponse(e.status==="OK"&&e.coverage!==null&&e.snapshot_id===null);
        for(const p of e.coverage.products){
          const key=JSON.stringify([p.exchange,p.product]);
          requireResponse(!keys.has(key));keys.add(key);products.push(p);
        }
        cursor=e.coverage.next_cursor;
        if(cursor===null)return {products,next_cursor:null};
        requireResponse(!cursors.has(cursor));cursors.add(cursor);
      }
      throw new ApiError("SOURCE_INCOMPLETE");
    },
    async quoteDetails(chain,signal){
      const s=chain.snapshot;
      const params=new URLSearchParams({exchange:s.exchange,product:s.product,trade_date:s.trade_date,underlying_id:chain.underlying.underlying_id,snapshot_id:s.snapshot_id});
      const raw=await fetchJson(STRATEGY_PATH+"/quote-details-v1?"+params,token,signal,undefined,strategyEnvelope);
      return decodeQuoteDetails(raw,chain);
    },
    async chain(selector,signal){
      const params=new URLSearchParams();
      for(const [k,v] of Object.entries(selector))if(v!==undefined)params.set(k,v);
      const e=await get(STRATEGY_PATH+"/chain?"+params,signal),c=e.chain;
      requireResponse(e.status==="OK"&&c!==null&&c.snapshot.provider!=="SYNTHETIC_FIXTURE"&&e.snapshot_id===c.snapshot.snapshot_id);
      requireResponse(c.snapshot.exchange===selector.exchange&&c.snapshot.product===selector.product&&
        c.snapshot.trade_date===selector.trade_date&&c.underlying.underlying_id===selector.underlying_id&&
        (!selector.snapshot_id||selector.snapshot_id===c.snapshot.snapshot_id));
      return c;
    }
  };
}
export async function loadStrategyChain(client:StrategyDataClient,selector:ChainSelector,signal:AbortSignal):Promise<StrategyChainDTO>{
  let base:StrategyChainDTO|null=null;
  const rows:StrategyChainDTO["rows"]=[],strikes=new Set<number>(),ids=new Set<string>(),cursors=new Set<string>();
  let cursor:string|undefined;
  for(let page=0;page<8;page++){
    const c=await client.chain({...selector,snapshot_id:base?.snapshot.snapshot_id??selector.snapshot_id,cursor},signal);
    requireResponse(c.snapshot.exchange===selector.exchange&&c.snapshot.product===selector.product&&
      c.snapshot.trade_date===selector.trade_date&&(client.chainKey?.(c)??c.underlying.underlying_id)===selector.underlying_id&&
      (!selector.snapshot_id||c.snapshot.snapshot_id===selector.snapshot_id));
    if(base)requireResponse(JSON.stringify(base.snapshot)===JSON.stringify(c.snapshot)&&JSON.stringify(base.underlying)===JSON.stringify(c.underlying));
    else base=c;
    for(const row of c.rows){
      requireResponse(!strikes.has(row.strike));strikes.add(row.strike);
      for(const [q,side] of [[row.call,"CALL"],[row.put,"PUT"]] as const){
        if(q){requireResponse(q.option_type===side&&q.strike===row.strike&&q.underlying_id===c.underlying.underlying_id&&!ids.has(q.contract_id));ids.add(q.contract_id);}
      }
      rows.push(row);
    }
    if(c.next_cursor===null)return {...base,rows:rows.sort((a,b)=>a.strike-b.strike),next_cursor:null};
    requireResponse(!cursors.has(c.next_cursor));cursors.add(c.next_cursor);cursor=c.next_cursor;
  }
  throw new ApiError("SOURCE_INCOMPLETE");
}

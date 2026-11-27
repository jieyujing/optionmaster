import type { ApiEnvelope, CoverageDTO, GridPointDTO, SourceVersionsDTO, ValuationDTO, ValuationRequestDTO } from "./generated/types";
import { validateEnvelope, validateRequest } from "./generated/validators.mjs";

export type { CoverageDTO, GridPointDTO, ValuationDTO, ValuationRequestDTO } from "./generated/types";
export const COVERAGE_PATH="/api/v1/research/coverage";
export const VALUATION_PATH="/api/v1/research/valuations";
const PIN="85a27c0d97b836cf51d2f8db7ac92c1e9681138ffe477d0f324f4518276ad826";
const MAX_RESPONSE=512*1024;
export class ApiError extends Error {
  constructor(public readonly code:string, message:string=code) {super(message);this.name="ApiError";}
}
export function requireResponse(condition:unknown): asserts condition {
  if(!condition) throw new ApiError("INVALID_RESPONSE");
}
export function requireInput(condition:unknown,message:string): asserts condition {
  if(!condition) throw new ApiError("INVALID_INPUT",message);
}
export const equalNumbers=(a:number[],b:number[])=>a.length===b.length&&a.every((x,i)=>x===b[i]);
export const close=(a:number,b:number)=>Math.abs(a-b)<=1e-7*Math.max(1,Math.abs(a),Math.abs(b));
export const sha=(v:string)=>/^[0-9a-f]{64}$/.test(v);

function versionsValid(v:SourceVersionsDTO) {
  return v.packet_sha256===PIN&&sha(v.registry_sha256)&&v.artifacts.length===8&&
    new Set(v.artifacts.map(x=>x.artifact)).size===8&&v.artifacts.every(x=>sha(x.sha256));
}
function sameVersions(a:SourceVersionsDTO,b:SourceVersionsDTO){
  return versionsValid(a)&&a.packet_sha256===b.packet_sha256&&a.registry_sha256===b.registry_sha256&&
    a.artifacts.every(x=>b.artifacts.some(y=>x.artifact===y.artifact&&x.sha256===y.sha256));
}
function envelope(value:unknown):ApiEnvelope{
  requireResponse(validateEnvelope(value));
  if(value.status==="UNAVAILABLE"){
    requireResponse(value.reason_code!==null&&value.coverage===null&&value.valuation===null);
    throw new ApiError(value.reason_code);
  }
  requireResponse(value.reason_code===null&&Number(value.coverage!==null)+Number(value.valuation!==null)===1);
  return value;
}
export function decodeCoverage(value:unknown):CoverageDTO {
  const e=envelope(value);
  const c=e.coverage;
  requireResponse(c!==null&&e.valuation===null&&c.scope==="ARCHIVED_HISTORICAL_INPUTS_ONLY"&&
    c.production_valuation_ready===false&&c.requested_legs===2&&c.admitted_legs===2&&c.legs.length===2&&
    c.trade_date==="2022-09-07"&&c.price_basis==="SETTLEMENT"&&versionsValid(c.source_versions));
  requireResponse(new Set(c.legs.map(x=>x.source_symbol)).size===2&&
    new Set(c.legs.map(x=>x.contract_id)).size===2&&
    c.legs.every(x=>x.underlying_id===c.legs[0]?.underlying_id&&x.expiry_date===c.legs[0]?.expiry_date&&x.strike>0&&x.contract_multiplier>0&&x.exercise_style==="AMERICAN")&&
    c.legs.some(x=>x.option_type==="CALL")&&c.legs.some(x=>x.option_type==="PUT"));
  return c;
}
export function validatePoint(p:GridPointDTO,price:number,iv:number,baseline:number,ids:string[]) {
  requireResponse(p.price_shock===price&&p.iv_shock===iv);
  if(p.status==="UNAVAILABLE"){
    requireResponse(p.reason_code!==null&&p.baseline_value===null&&p.scenario_value===null&&p.value_change===null&&p.contributions.length===0);
  }else{
    requireResponse(p.reason_code===null&&p.baseline_value!==null&&p.scenario_value!==null&&p.value_change!==null&&
      close(p.baseline_value,baseline)&&close(p.scenario_value-p.baseline_value,p.value_change)&&
      p.contributions.length===ids.length&&p.contributions.every((x,i)=>x.contract_id===ids[i])&&
      close(p.contributions.reduce((sum,x)=>sum+x.value_change,0),p.value_change)&&
      close(p.contributions.reduce((sum,x)=>sum+x.scenario_value,0),p.scenario_value));
  }
}
export function decodeValuation(value:unknown,c:CoverageDTO,req:ValuationRequestDTO):ValuationDTO {
  const e=envelope(value);
  const v=e.valuation;
  requireResponse(v!==null&&e.coverage===null&&v.production_valuation_ready===false&&
    v.scope==="ARCHIVED_HISTORICAL_RESEARCH_ONLY"&&v.slice_id===c.slice_id&&v.trade_date===c.trade_date&&v.price_basis===c.price_basis&&
    sameVersions(v.source_versions,c.source_versions)&&Date.parse(v.scenario_at)===Date.parse(req.scenario_at)&&
    v.legs.length===c.legs.length&&v.underlying_exposures.length===1&&v.underlying_exposures[0]?.underlying_id===c.legs[0]?.underlying_id);
  requireResponse(v.legs.every((x,i)=>{
    const prior=c.legs[i];
    return prior!==undefined&&Object.entries(prior).every(([key,val])=>x[key as keyof typeof prior]===val)&&
      x.quantity===req.positions[x.source_symbol]&&x.implied_volatility>0&&x.market_option_price>0&&x.market_underlying_price>0&&
      x.scenario_iv===x.implied_volatility&&x.raw_contract_version===""&&x.raw_exercise_style==="UNKNOWN"&&
      x.effective_contract_version.startsWith("SHFE-REF:")&&close(x.baseline_value,x.quantity*x.contract_multiplier*x.market_option_price);
  }));
  validateGrid(v,req);
  return v;
}
export function validateGrid(v:Pick<ValuationDTO,'legs'|'grid'>,req:Pick<ValuationRequestDTO,'curve_price_shocks'|'heatmap_price_shocks'|'heatmap_iv_shocks'>):void {
  const g=v.grid;
  requireResponse(equalNumbers(g.curve_price_axis,req.curve_price_shocks)&&equalNumbers(g.heatmap_price_axis,req.heatmap_price_shocks)&&
    equalNumbers(g.heatmap_iv_axis,req.heatmap_iv_shocks)&&g.price_curve.length===g.curve_price_axis.length&&
    g.heatmap_rows.length===g.heatmap_iv_axis.length&&
    close(g.baseline_value,v.legs.reduce((sum,x)=>sum+x.baseline_value,0))&&
    close(g.configured_scenario_value-g.baseline_value,g.configured_value_change)&&
    close(g.configured_value_change,v.legs.reduce((sum,x)=>sum+x.value_change,0)));
  const ids=v.legs.map(x=>x.contract_id);
  for(const [i,point] of g.price_curve.entries()) validatePoint(point,g.curve_price_axis[i]!,0,g.baseline_value,ids);
  for(const [r,row] of g.heatmap_rows.entries()){
    requireResponse(row.length===g.heatmap_price_axis.length);
    for(const [i,point] of row.entries()) validatePoint(point,g.heatmap_price_axis[i]!,g.heatmap_iv_axis[r]!,g.baseline_value,ids);
  }
  const origin=g.heatmap_rows[g.heatmap_iv_axis.indexOf(0)]?.[g.heatmap_price_axis.indexOf(0)];
  const curveOrigin=g.price_curve[g.curve_price_axis.indexOf(0)];
  requireResponse(origin?.status==="OK"&&curveOrigin?.status==="OK"&&origin.value_change!==null&&curveOrigin.value_change!==null&&
    close(origin.value_change,g.configured_value_change)&&close(curveOrigin.value_change,g.configured_value_change));
}
function parseQuantity(raw:string):number{
  requireInput(/^-?(?:0|[1-9]\d{0,3})$/.test(raw),"持仓必须为非零整数");
  const n=Number(raw);
  requireInput(n!==0&&Math.abs(n)<=1000,"每腿持仓限制为 -1000 至 +1000 手，不含 0");
  return n;
}
function parseRange(raw:string,label:string):number{
  requireInput(/^(?:0|[1-9]\d{0,2})(?:\.\d{1,2})?$/.test(raw),label+"必须为有效数值");
  const n=Number(raw);
  requireInput(n>=0.01&&n<=100,label+"须在 0.01 至 100 之间");
  return n;
}
function axis(width:number,count:number):number[]{
  const mid=(count-1)/2;
  return Array.from({length:count},(_,i)=>Number((((i-mid)/mid)*width/100).toFixed(8)));
}
export function buildRequestInputs(c:Pick<CoverageDTO,'trade_date'|'legs'>,quantities:string[],scenario:string,priceRange:string,ivRange:string):Omit<ValuationRequestDTO,'schema_version'|'slice_id'>{
  requireInput(c.legs.length>=2&&c.legs.length<=8,"请选择 2 至 8 张不同的已准入合约");
  requireInput(quantities.length===c.legs.length,"持仓与已准入合约不一致");
  const positions=Object.fromEntries(c.legs.map((x,i)=>[x.source_symbol,parseQuantity(quantities[i]??"")]));
  requireInput(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(scenario),"请输入完整的北京时间情景时点");
  const at=scenario+":00+08:00";
  const ms=Date.parse(at);
  requireInput(Number.isFinite(ms)&&new Date(ms+8*3600000).toISOString().slice(0,16)===scenario,"情景日期无效");
  requireInput(scenario>=c.trade_date+"T15:00","情景时间不得早于历史基准时点");
  requireInput(c.legs.every(x=>scenario.slice(0,10)<x.expiry_date),"到期日及之后的情景不支持");
  const price=parseRange(priceRange,"价格冲击半宽 (%)");
  const iv=parseRange(ivRange,"IV 冲击半宽 (百分点)");
  return {positions,scenario_at:at,curve_price_shocks:axis(price,9),heatmap_price_shocks:axis(price,5),heatmap_iv_shocks:axis(iv,5)};
}
export function buildRequest(c:CoverageDTO,quantities:string[],scenario:string,priceRange:string,ivRange:string):ValuationRequestDTO{
  const req:ValuationRequestDTO={schema_version:"1.0",slice_id:c.slice_id,...buildRequestInputs(c,quantities,scenario,priceRange,ivRange)};
  requireInput(validateRequest(req),"请求超出版本化接口限制");
  return req;
}
export async function fetchJson(path:string,token:string,signal:AbortSignal,body?:object,errorDecoder:(value:unknown)=>unknown=envelope):Promise<unknown>{
  let response:Response;
  try {
    response=await fetch(path,{
      method:body===undefined?"GET":"POST",headers:{Authorization:"Bearer "+token,Accept:"application/json",
        ...(body===undefined?{}:{"Content-Type":"application/json"})},
      ...(body===undefined?{}:{body:JSON.stringify(body)}),
      signal,cache:"no-store",credentials:"omit",redirect:"error",referrerPolicy:"no-referrer",
    });
  }catch(error){
    if(signal.aborted)throw error;
    throw new ApiError("NETWORK_ERROR");
  }
  if(response.status===401) throw new ApiError("UNAUTHORIZED");
  if(response.status===429) throw new ApiError("BUSY");
  if(response.status>=500) throw new ApiError("INTERNAL_ERROR");
  requireResponse(response.headers.get("content-type")?.toLowerCase().includes("application/json")&&response.body!==null);
  const reader=response.body.getReader();
  const decoder=new TextDecoder("utf-8",{fatal:true});
  let length=0,raw="";
  try {
    for(;;){
      const {done,value}=await reader.read();
      if(done)break;
      length+=value.byteLength;
      if(length>MAX_RESPONSE)throw new ApiError("INVALID_RESPONSE");
      raw+=decoder.decode(value,{stream:true});
    }
    raw+=decoder.decode();
    const value:unknown=JSON.parse(raw);
    if(!response.ok){
      errorDecoder(value); // Reject the failure DTO; non-2xx success is also invalid.
      throw new ApiError("INVALID_RESPONSE");
    }
    return value;
  }catch(error){
    if(error instanceof ApiError||signal.aborted)throw error;
    throw new ApiError("INVALID_RESPONSE");
  }finally{
    await reader.cancel().catch(()=>{});
    reader.releaseLock();
  }
}
export async function fetchCoverage(token:string,signal:AbortSignal):Promise<CoverageDTO>{
  return decodeCoverage(await fetchJson(COVERAGE_PATH,token,signal));
}
export async function fetchValuation(token:string,c:CoverageDTO,req:ValuationRequestDTO,signal:AbortSignal):Promise<ValuationDTO>{
  return decodeValuation(await fetchJson(VALUATION_PATH,token,signal,req),c,req);
}

const messages:Record<string,string>={
  UNAUTHORIZED:"令牌无效或已失效，请重新连接。",
  BUSY:"计算服务忙，当前任务未完成，请稍后手动重试。",
  INVALID_RESPONSE:"接口返回与数据契约不符，已拒绝展示。",
  NETWORK_ERROR:"无法连接本地研究服务，请检查服务是否运行。",
  INTERNAL_ERROR:"计算服务内部错误，未生成估值。",
  ARCHIVE_UNAVAILABLE:"历史归档证据未通过验证。",
  RATE_UNAVAILABLE:"历史利率证据不可用。",
  EXPIRED_SCENARIO:"到期日及之后的情景不可用。",
  INVALID_SCENARIO_TIME:"情景时点不符合历史估值口径。",
  INVALID_SCENARIO:"该情景无法定价。",
  INVALID_REQUEST:"请求不符合 API 契约。",
  SLICE_SCOPE_MISMATCH:"合约不在已准入历史范围内。",
  IV_UNAVAILABLE:"隐含波动率反解不可用。",
  NUMERICAL_FAILURE:"数值计算未通过验证。",
  VALUATION_UNAVAILABLE:"估值不可用。",
};
export function errorMessage(error:unknown):string{
  if(error instanceof ApiError) return error.code==="INVALID_INPUT"?error.message:(messages[error.code]??"请求未完成："+error.code);
  return "未能完成计算，请重试。";
}
export function reasonMessage(code:string|null):string {return code===null?"":messages[code]??code;}

import {useEffect,useRef,useState} from "react";
import {ApiError,close,requireInput,requireResponse} from "./api";
import {expiryKey,fetchCatalogCoverage,fetchCatalogValuation,type CatalogCoverageDTO,type CatalogValuationDTO,type CatalogValuationRequestDTO} from "./catalog-api";
import type {StrategyValuationDTO,StrategyValuationRequestDTO} from "./generated/strategy-types";
import {validateCatalogRequest} from "./generated/catalog-validators.mjs";
import {canonicalStrategyRequest} from "./strategy-api";
import type {PortfolioState} from "./strategy-state";

export function advancedBlock(state:PortfolioState,req:StrategyValuationRequestDTO|null,value:StrategyValuationDTO|undefined):string|null{
 const s=state.snapshot;
 if(!s||s.provider!=="APPROVED_ARCHIVE"||s.price_basis!=="SETTLEMENT")return "旧版仅支持已批准的 D09 归档结算价范围";
 if(state.positions.some(p=>p.kind!=="OPTION"))return "旧版不支持期货或现货腿；不会删除持仓后计算";
 if(state.positions.length<2||state.positions.length>8)return "旧版需要 2 至 8 个不同期权腿";
 if(state.positions.some(p=>p.unavailable!==null||p.current?.kind!=="OPTION"))return "持仓存在不可用合约";
 if(state.positions.some(p=>p.current?.kind==="OPTION"&&(p.entry.entry_snapshot_id!==s.snapshot_id||p.entry.entry_trade_date!==s.trade_date||p.entry.entry_origin!=="ADMITTED_MARK"||p.entry.entry_price!==p.current.option.market_price)))return "旧版不能无损表达自定义或跨日建仓成本";
 if(!req||!value)return "等待有效的当前版本模型估值";
 if(req.portfolio_revision!==state.revision||req.snapshot_id!==s.snapshot_id||value.snapshot.snapshot_id!==s.snapshot_id)return "当前估值版本不匹配";
 if(state.positions.some(p=>p.current?.kind==="OPTION"&&req.scenario_at.slice(0,10)>=p.current.option.expiry_date))return "旧版严格限制在最早到期日前";
 if(value.legs.length!==req.options.length||value.legs.some(l=>l.model_eligibility.status!=="ELIGIBLE"||l.implied_volatility===null||l.implied_volatility<=0))return "存在无法可靠反解 IV 的期权腿";
 if(value.scenario.points.find(p=>p.price_return===0)?.status!=="OK")return "当前情景中心点不可用";
 return null;
}
export function projectAdvancedRequest(state:PortfolioState,req:StrategyValuationRequestDTO,value:StrategyValuationDTO,c:CatalogCoverageDTO,ivWidth:number):CatalogValuationRequestDTO{
 const block=advancedBlock(state,req,value);requireInput(block===null,block??"");
 const s=state.snapshot!;
 requireInput(c.trade_date===s.trade_date&&c.source_versions.packet_sha256===s.source_pin&&c.source_versions.artifacts.every(a=>s.source_versions[a.artifact]===a.sha256),"旧版和新版归档来源不一致");
 requireInput(Number.isFinite(ivWidth)&&ivWidth>0&&ivWidth<=1,"IV 网格半宽须大于 0 且不超过 100 个百分点");
 const positions:Record<string,number>={},prices:Record<string,number>={},ivs:Record<string,number>={};
 for(const p of state.positions){
   requireInput(p.current?.kind==="OPTION","不支持非期权腿");
   const o=p.current.option,u=p.current.underlying;
   const legacy=c.legs.find(l=>l.source_symbol===o.contract_id),calibrated=value.legs.find(l=>l.instrument_id===o.contract_id);
   requireInput(legacy!==undefined&&calibrated!==undefined&&calibrated.quantity===p.entry.quantity&&calibrated.entry_price===p.entry.entry_price&&
     legacy.underlying_id===u.underlying_id&&legacy.expiry_date===o.expiry_date&&legacy.option_type===o.option_type&&legacy.strike===o.strike&&
     legacy.contract_multiplier===o.multiplier&&legacy.exercise_style===o.exercise_style&&legacy.settlement===o.market_price&&legacy.underlying_settlement===u.market_price&&!legacy.at_intrinsic,
     "旧版无一致合约、条款、行情或 IV 准入");
   positions[legacy.source_symbol]=p.entry.quantity;
   prices[u.underlying_id]=req.price_shocks_by_underlying[u.underlying_id]??req.global_price_shock;
   ivs[expiryKey(u.underlying_id,o.expiry_date)]=req.iv_shocks_by_expiry[o.expiry_id]??req.global_iv_shock;
 }
 const width=Math.max(...req.curve_price_returns.map(Math.abs));
 const request:CatalogValuationRequestDTO={schema_version:"3.0",catalog_id:c.catalog_id,trade_date:c.trade_date,positions,
   scenario_at:req.scenario_at,curve_price_shocks:req.curve_price_returns,
   heatmap_price_shocks:[-width,-width/2,0,width/2,width],heatmap_iv_shocks:[-ivWidth,-ivWidth/2,0,ivWidth/2,ivWidth],
   price_shocks_by_underlying:prices,iv_shocks_by_expiry:ivs};
 requireInput(validateCatalogRequest(request),"不满足旧版请求网格或持仓限制");
 return request;
}
export function AdvancedAnalysis({token,state,request,value,onUnauthorized}:{token:string;state:PortfolioState;request:StrategyValuationRequestDTO|null;value:StrategyValuationDTO|undefined;onUnauthorized:()=>void}){
 const [iv,setIv]=useState("6"),[result,setResult]=useState<{key:string;value:CatalogValuationDTO}|null>(null),[error,setError]=useState<{key:string;message:string}|null>(null),[busy,setBusy]=useState(false);
 const active=useRef<AbortController|null>(null),generation=useRef(0);
 const key=request?canonicalStrategyRequest(request)+"|"+iv:"",block=advancedBlock(state,request,value);
 useEffect(()=>{generation.current++;setResult(null);setError(null);active.current?.abort();return()=>{generation.current++;active.current?.abort();};},[key,token]);
 async function run(){
   if(active.current||!request||!value||block)return;
   const g=++generation.current,ctrl=new AbortController();active.current=ctrl;setBusy(true);setError(null);setResult(null);
   const timer=setTimeout(()=>ctrl.abort("DEADLINE_EXCEEDED"),30000);
   try{
     const c=await fetchCatalogCoverage(token,ctrl.signal,state.snapshot!.trade_date);
     const width=/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(iv)?Number(iv)/100:NaN;
     const projected=projectAdvancedRequest(state,request,value,c,width);
     const v=await fetchCatalogValuation(token,c,projected,ctrl.signal);
     const center=value.scenario.points.find(p=>p.price_return===0);
     requireResponse(center?.pnl!==null&&center?.pnl!==undefined&&close(center.pnl,v.grid.configured_value_change));
     if(g===generation.current&&!ctrl.signal.aborted)setResult({key,value:v});
   }catch(e){
     if(g===generation.current){const message=ctrl.signal.aborted?"已取消 / 超时":e instanceof Error?e.message:"不可用";setError({key,message});if(e instanceof ApiError&&e.code==="UNAUTHORIZED")onUnauthorized();}
   }finally{clearTimeout(timer);if(active.current===ctrl){active.current=null;setBusy(false);}}
 }
 const visible=result?.key===key&&!block?result.value:null;
 return <details className="sa-advanced"><summary>高级分析 · 原版 v3 压力测试</summary>
   <p>独立旧版理论变化（相对市场基准，非建仓盈亏）；只有无损兼容组合才允许投影。</p>
   <p><a href="/?api=v1">原版 v1</a> · <a href="/?api=v2">原版 v2</a> · <a href="/?api=v3">原版 v3</a></p>
   <label>IV 网格半宽 (百分点)<input aria-label="高级 IV 网格半宽" value={iv} onChange={e=>setIv(e.target.value)}/></label>
   <p data-testid="advanced-status">{block??(busy?"计算中":error?.key===key?error.message:visible?"旧版估值成功":"兼容，可手动运行")}</p>
   <button type="button" disabled={!!block||busy} onClick={()=>void run()}>运行原版压力测试</button>
   {busy&&<button type="button" onClick={()=>active.current?.abort()}>取消高级计算</button>}
   {visible&&<section data-testid="advanced-result"><h4>理论市值变化 · CNY</h4><p data-testid="advanced-center">{visible.grid.configured_value_change.toLocaleString("zh-CN",{maximumFractionDigits:2})}</p>
   <div className="sa-scroll"><table><thead><tr><th>IV / 价格冲击</th>{visible.grid.heatmap_price_axis.map(x=><th key={x}>{(x*100).toFixed(2)}%</th>)}</tr></thead><tbody>
   {visible.grid.heatmap_rows.map((row,i)=><tr key={i}><th>{(visible.grid.heatmap_iv_axis[i]!*100).toFixed(2)} pp</th>{row.map((p,j)=><td key={j} data-testid={"advanced-cell-"+i+"-"+j}>{p.value_change===null?p.reason_code:p.value_change.toLocaleString("zh-CN",{maximumFractionDigits:2})}</td>)}</tr>)}</tbody></table></div></section>}
 </details>;
}

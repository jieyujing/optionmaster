import {useEffect,useRef,useState} from "react";
import {canonicalStrategyRequest,type StrategyCalculationClient} from "./strategy-api";
import {LatestLane,type LaneResult} from "./strategy-lane";
import {buildPayoffRequest,type PortfolioState} from "./strategy-state";
import type {StrategyPayoffRequestDTO,StrategyValuationRequestDTO,StrategyPayoffDTO,StrategyValuationDTO,StrategyCurveDTO,StrategyRiskDTO,RiskMetricDTO,RootIntervalDTO,StrategyGreekGroupDTO} from "./generated/strategy-types";
import {validateStrategyValuationRequest} from "./generated/strategy-validators.mjs";
import {AdvancedAnalysis} from "./strategy-advanced";
import "./strategy-analysis.css";

function useLane<Q extends StrategyPayoffRequestDTO|StrategyValuationRequestDTO,T>(client:StrategyCalculationClient,kind:"payoff"|"valuation",req:Q|null,run:(request:Q,signal:AbortSignal)=>Promise<T>,onUnauthorized:()=>void){
  const key=req===null?null:canonicalStrategyRequest(req);
  const [result,setResult]=useState<LaneResult<T>|null>(null);
  const lane=useRef<LatestLane<Q,T>|null>(null),runRef=useRef(run),authRef=useRef(onUnauthorized),reqRef=useRef(req);
  runRef.current=run;authRef.current=onUnauthorized;reqRef.current=req;
  useEffect(()=>{
    const l=new LatestLane<Q,T>((r,s)=>runRef.current(r,s),v=>{setResult(v);if(v?.error==="UNAUTHORIZED")authRef.current();},kind==="payoff"?0:250,kind==="payoff"?3000:30000);
    lane.current=l;return()=>{l.dispose();lane.current=null;};
  },[client,kind]);
  useEffect(()=>{lane.current?.submit(key,reqRef.current);},[client,kind,key]);
  return key===null?null:result?.key===key?result:{key,status:"pending" as const};
}
const money=(x:number|null)=>x===null?"—":x.toLocaleString("zh-CN",{maximumFractionDigits:2});
const pct=(x:number)=>Number((x*100).toPrecision(7))+"%";
const riskText=(m:RiskMetricDTO)=>m.kind==="UNBOUNDED"?"无界":m.kind==="UNAVAILABLE"?"不可用 · "+(m.reason??""):money(m.value)+(m.kind==="CONDITIONAL_RANGE"?"（范围内）":"");
const interval=(r:RootIntervalDTO)=> (r.start_inclusive?"[":"(")+(r.start===null?"−∞":pct(r.start))+", "+(r.end===null?"+∞":pct(r.end))+(r.end_inclusive?"]":")");
const scopeText={EXACT_DOMAIN:"已声明价格域内 · 精确",CONDITIONAL_COMMON_RETURN:"共同收益率路径 · 条件",CONDITIONAL_RANGE:"采样范围内 · 条件",UNAVAILABLE:"风险指标不可用"};
const curveName={CURRENT:"当前估值",SCENARIO:"情景估值",TERMINAL:"同期限到期",FIRST_EXPIRY_CONDITIONAL:"最早到期条件估值"};
const colors={CURRENT:"#2067ae",SCENARIO:"#d48728",TERMINAL:"#11815a",FIRST_EXPIRY_CONDITIONAL:"#11815a"};
function Risk({risk}:{risk:StrategyRiskDTO}){
 return <section className="sa-risk" data-testid="strategy-risk"><strong>{scopeText[risk.scope]}</strong>
   <div className="sa-metrics">{[["最大盈利",risk.max_profit],["最大亏损",risk.max_loss],["收益风险比",risk.reward_risk_ratio]].map(([label,metric])=><div key={label as string}><small>{label as string}</small><b>{riskText(metric as RiskMetricDTO)}</b></div>)}</div>
   <p>盈亏平衡：{risk.breakevens.map(pct).join(" / ")||"—"}{risk.zero_intervals.length?"；零值区间 "+risk.zero_intervals.map(interval).join(" / "):""} · {risk.roots_exhaustive?"根已完整枚举":"根仅条件/非穷尽"}</p>
   <p>共同收益率域：{risk.domain?interval(risk.domain):"未建立"}；非交易所涨跌停或全市场价格保证。</p>
 </section>;
}
function Curves({curves}:{curves:StrategyCurveDTO[]}){
 const points=curves.flatMap(c=>c.points.filter(p=>p.pnl!==null));
 if(!points.length)return <section className="sa-legend" aria-label="曲线状态">{curves.map(c=><span key={c.kind}>{curveName[c.kind]} · {c.evaluation_at??c.evaluation_date??"—"} · {c.status} {c.reason??""}</span>)}</section>;
 const xs=points.map(p=>p.price_return),ys=points.map(p=>p.pnl!);
 const lo=Math.min(...xs),hi=Math.max(...xs),min=Math.min(0,...ys),max=Math.max(0,...ys);
 const x=(v:number)=>46+(v-lo)/(hi-lo||1)*520,y=(v:number)=>212-(v-min)/(max-min||1)*184;
 return <section className="sa-curves"><svg viewBox="0 0 600 250" role="img" aria-label="策略盈亏曲线，横轴共同收益率，纵轴人民币">
 <line x1="46" y1={y(0)} x2="566" y2={y(0)} stroke="#8495a4" strokeDasharray="4 3"/><text x="4" y="18" fontSize="10">CNY</text>
 <text x="46" y="238" fontSize="11">{pct(lo)}</text><text x="566" y="238" textAnchor="end" fontSize="11">{pct(hi)}</text>
 <text x="42" y="28" textAnchor="end" fontSize="9">{money(max)}</text><text x="42" y="212" textAnchor="end" fontSize="9">{money(min)}</text>
 {curves.map(c=>{
   let d="",open=false;for(const p of c.points){if(p.pnl===null){open=false;continue;}d+=(open?"L":"M")+x(p.price_return)+" "+y(p.pnl)+" ";open=true;}
   return <g key={c.kind} data-testid={"curve-"+c.kind}><path d={d} fill="none" stroke={colors[c.kind]} strokeWidth="2.2" strokeDasharray={c.kind==="SCENARIO"?"5 3":undefined}/>{c.points.filter(p=>p.pnl!==null).map(p=><circle key={p.price_return} cx={x(p.price_return)} cy={y(p.pnl!)} r="3" fill={colors[c.kind]}><title>{curveName[c.kind]} {pct(p.price_return)} · {money(p.pnl)} CNY · {Object.entries(p.underlying_prices).map(([k,v])=>k+"="+money(v)).join(" / ")} · {p.contributions.map(q=>q.instrument_id+"="+money(q.pnl)).join(" / ")}</title></circle>)}</g>;
 })}
 </svg><div className="sa-legend">{curves.map(c=><span key={c.kind}><i style={{background:colors[c.kind]}}/>{curveName[c.kind]} · {c.evaluation_at??c.evaluation_date??"—"} · {c.status}{c.reason?" "+c.reason:""}</span>)}</div>
 <details><summary>逐点实际标的价格与分腿盈亏</summary>{curves.map(c=><div key={c.kind}><h4>{curveName[c.kind]}</h4><div className="sa-scroll"><table><thead><tr><th>共同收益率</th><th>盈亏 CNY</th><th>标的价格</th><th>分腿贡献 CNY</th></tr></thead><tbody>{c.points.map(p=><tr key={p.price_return}><td>{pct(p.price_return)}</td><td>{p.pnl===null?p.reason:money(p.pnl)}</td><td>{Object.entries(p.underlying_prices).map(([k,v])=>k+" "+money(v)).join(" / ")}</td><td>{p.contributions.map(q=>q.instrument_id+" "+money(q.pnl)).join(" / ")}</td></tr>)}</tbody></table></div></div>)}</details></section>;
}
function Greeks({groups,title}:{groups:StrategyGreekGroupDTO[];title:string}){
 return <div><h4>{title}（按实际标的分组）</h4><div className="sa-scroll"><table><thead><tr><th>标的 / 单位</th><th>Delta</th><th>Gamma</th><th>Theta / 年</th><th>Vega / 1.0 IV</th></tr></thead><tbody>{groups.map(g=><tr key={g.underlying_id}><td>{g.underlying_id} / {g.quote_unit}{g.reason?" · "+g.reason:""}</td><td>{money(g.delta)}</td><td>{money(g.gamma)}</td><td>{money(g.theta_annual)}</td><td>{money(g.vega_per_unit)}</td></tr>)}</tbody></table></div></div>;
}
type Settings={range:string;price:string;iv:string;scenario:string;reference:string;prices:Record<string,string>;ivs:Record<string,string>};
const defaults=(state:PortfolioState):Settings=>({range:"10",price:"0",iv:"0",scenario:state.snapshot?.reference_at.slice(0,16)??"",reference:"",prices:{},ivs:{}});
function parse(s:string,lo=-100,hi=100){if(!/^-?(?:\d+(?:\.\d*)?|\.\d+)$/.test(s))return null;const v=Number(s);return Number.isFinite(v)&&v>=lo&&v<=hi?v/100:null;}
function parseMap(values:Record<string,string>,ids:string[]){const out:Record<string,number>={};for(const id of ids){const raw=values[id];if(raw===undefined||raw==="")continue;const value=parse(raw);if(value===null)return null;out[id]=value;}return out;}
export function StrategyAnalysis({state,active,client,bumpRevision,onUnauthorized,enableModel=true,token}:{state:PortfolioState;active:boolean;client:StrategyCalculationClient;bumpRevision:()=>void;onUnauthorized:()=>void;enableModel?:boolean;token?:string}){
 const snapshot=state.snapshot?.snapshot_id??"", [saved,setSaved]=useState<{snapshot:string;settings:Settings}>({snapshot,settings:defaults(state)});
 const s=saved.snapshot===snapshot?saved.settings:defaults(state);
 const options=state.positions.filter(p=>p.kind==="OPTION");
 const ids=[...new Set(options.map(p=>p.original.underlying.underlying_id))];
 const expiries=[...new Set(options.map(p=>p.original.kind==="OPTION"?p.original.option.expiry_id:"").filter(Boolean))];
 const ref=ids.includes(s.reference)?s.reference:ids[0];
 const change=(patch:Partial<Settings>)=>{setSaved({snapshot,settings:{...s,...patch}});bumpRevision();};
 const range=parse(s.range,1,100),price=parse(s.price),iv=parse(s.iv),prices=parseMap(s.prices,ids),ivs=parseMap(s.ivs,expiries);
 const axis=range===null?[]:[-1,-.75,-.5,-.25,0,.25,.5,.75,1].map(v=>Number((v*range).toPrecision(12)));
 const req=active&&price!==null&&prices!==null&&range!==null?buildPayoffRequest(state,axis,price,prices,ref):null;
 const at=s.scenario+":00+08:00";
 const candidate=req&&iv!==null&&ivs!==null&&Number.isFinite(Date.parse(at))?{...req,scenario_at:at,global_iv_shock:iv,iv_shocks_by_expiry:ivs}:null;
 const model=enableModel&&candidate&&validateStrategyValuationRequest(candidate)?candidate:null;
 const p=useLane<StrategyPayoffRequestDTO,StrategyPayoffDTO>(client,"payoff",req,(r,signal)=>client.payoff(r,signal),onUnauthorized);
 const v=useLane<StrategyValuationRequestDTO,StrategyValuationDTO>(client,"valuation",model,(r,signal)=>client.valuation(r,signal),onUnauthorized);
 const pv=p?.status==="ok"?p.value:undefined,mv=v?.status==="ok"?v.value:undefined;
 const horizon=pv&&pv.terminal.status!=="UNAVAILABLE"?pv.terminal:mv?.horizon??pv?.terminal;
 const risk=pv&&pv.risk.scope!=="UNAVAILABLE"?pv.risk:mv?.horizon_risk??pv?.risk;
 const curves=[mv?.current,mv?.scenario,horizon].filter((c):c is StrategyCurveDTO=>!!c);
 return <section className="sa-root" data-testid="strategy-analysis">
 <div className="sa-controls"><label>共同收益率半宽 (%)<input aria-label="收益率半宽" value={s.range} disabled={!active} onChange={e=>change({range:e.target.value})}/></label>
 <label>共同价格冲击 (%)<input aria-label="共同价格冲击" value={s.price} disabled={!active} onChange={e=>change({price:e.target.value})}/></label>
 <label>参考标的<select aria-label="参考标的" value={ref??""} disabled={!active} onChange={e=>change({reference:e.target.value})}>{ids.map(id=><option key={id}>{id}</option>)}</select></label>
 {enableModel&&<><label>情景时点（北京时间）<input type="datetime-local" aria-label="情景时点" value={s.scenario} disabled={!active} onChange={e=>change({scenario:e.target.value})}/></label>
 <label>共同 IV 冲击 (百分点)<input aria-label="共同 IV 冲击" value={s.iv} disabled={!active} onChange={e=>change({iv:e.target.value})}/></label></>}
 <details><summary>独立价格 / IV 冲击</summary>{ids.map(id=><label key={id}>{id} 价格 (%)<input aria-label={id+" 价格冲击"} value={s.prices[id]??""} placeholder="跟随共同" onChange={e=>change({prices:{...s.prices,[id]:e.target.value}})}/></label>)}{enableModel&&expiries.map(id=><label key={id}>{id} IV (百分点)<input aria-label={id+" IV 冲击"} value={s.ivs[id]??""} placeholder="跟随共同" onChange={e=>change({ivs:{...s.ivs,[id]:e.target.value}})}/></label>)}</details></div>
 <p className="sa-note">按人民币建仓成本计算研究盈亏；共同冲击是标的收益率路径，独立冲击覆盖共同值。归档名义时点非实际行权时刻。</p>
 <p data-testid="payoff-status" aria-live="polite">到期计算：{p?.status==="pending"?"计算中":p?.status==="error"?"不可用 · "+p.error:pv?"已计算 · "+pv.terminal.status+(pv.terminal.reason?" · "+pv.terminal.reason:""):"等待有效持仓"}</p>
 {enableModel&&<p data-testid="model-status" aria-live="polite">模型计算：{v?.status==="pending"?"计算中":v?.status==="error"?"不可用 · "+v.error:mv?"已计算":"等待有效持仓与情景参数"}</p>}
 {pv&&<section data-testid="payoff-result" data-revision={req?.portfolio_revision}><div className="sa-metrics"><div><small>净权利金（净支付为正）</small><b data-testid="net-premium">{money(pv.accounting.net_premium)}</b><small>CNY</small></div><div><small>现货建仓现金</small><b>{money(pv.accounting.spot_entry_cash)}</b><small>不包含期货名义价值 / 保证金</small></div></div></section>}
 {curves.length>0&&<Curves curves={curves}/>}
 {risk&&<Risk risk={risk}/>}
 {mv&&<section data-testid="model-result" data-revision={model?.portfolio_revision}><Greeks title="当前 Greeks" groups={mv.current_greeks}/><Greeks title="情景 Greeks" groups={mv.scenario_greeks}/>
 <p>已选腿 IV：{mv.legs.filter(l=>l.kind==="OPTION").map(l=>l.instrument_id+" "+(l.implied_volatility===null?l.model_eligibility.reason??"不可用":pct(l.implied_volatility))).join(" / ")}</p></section>}
 {token&&<AdvancedAnalysis token={token} state={state} request={model} value={mv} onUnauthorized={onUnauthorized}/>}
 </section>;
}

import { useEffect,useRef,useState,type ReactNode } from "react";
import type { CoverageProductDTO,CoverageDayDTO,StrategyChainDTO,StrategyCoverageDTO,OptionQuoteDTO } from "./generated/strategy-types";
import { loadStrategyChain,type StrategyDataClient,type LocalModelInfo,type LocalChainModel } from "./strategy-api";
import { buildPayoffRequest,canSelect,emptyPortfolio,entryText,instrumentForOption,instrumentForUnderlying,instrumentKey,
  optionCount,positionKey,quantityText,revaluePortfolio,setEntryPrice,setQuantity,setSnapshot,
  coverageMonthUsable,coverageDayUsable,preferredCoverageDay,coverageChainChoices,coverageChainKey,type PortfolioState,type StrategyInstrument } from "./strategy-state";
import { QuoteMatrix } from "./quote-matrix";
import type { QuoteDetails } from "./quote-details";
import "./strategy-builder.css";

const productKey=(p:CoverageProductDTO)=>JSON.stringify([p.exchange,p.product]);
const usable=coverageDayUsable;
const defaultDay=preferredCoverageDay;
const fmt=(n:number|null)=>n===null?"—":n.toLocaleString("zh-CN",{maximumFractionDigits:6});
const name=(i:StrategyInstrument)=>i.kind==="OPTION"?i.option.contract_id:i.underlying.underlying_id;
type Props={client:StrategyDataClient;fixture?:boolean;analysis?:(state:PortfolioState,active:boolean,bumpRevision:()=>void)=>ReactNode;onAdvanced?:(state:PortfolioState)=>void};
type Change={p:CoverageProductDTO;d:CoverageDayDTO;month:string;productChanged:boolean};

export function StrategyBuilder({client,fixture=false,analysis,onAdvanced}:Props){
  const [coverage,setCoverage]=useState<StrategyCoverageDTO|null>(null);
  const [selection,setSelection]=useState<{p:CoverageProductDTO;d:CoverageDayDTO;month:string}|null>(null);
  const [chain,setChain]=useState<StrategyChainDTO|null>(null);
  const [state,setState]=useState<PortfolioState>(emptyPortfolio);
  const [loading,setLoading]=useState(true),[error,setError]=useState("");
  const [pending,setPending]=useState<Change|null>(null),[split,setSplit]=useState(55);
  const [details,setDetails]=useState<QuoteDetails|null>(null),[detailStatus,setDetailStatus]=useState("未准入详情");
  const [wide,setWide]=useState(true),[precision,setPrecision]=useState(4);
  const [modelInfo,setModelInfo]=useState<LocalModelInfo|null>(null),[modelSaving,setModelSaving]=useState(false);
  const localCache=!!client.modelInfo;
  const request=useRef<AbortController|null>(null),generation=useRef(0),layout=useRef<HTMLDivElement>(null);
  const current=useRef(state);current.current=state;
  const selectionRef=useRef(selection);selectionRef.current=selection;
  function start(){
    request.current?.abort();const ctrl=new AbortController();request.current=ctrl;
    const id=++generation.current;setLoading(true);setError("");setChain(null);setDetails(null);
    return {ctrl,id,active:()=>!ctrl.signal.aborted&&generation.current===id};
  }
  async function load(p:CoverageProductDTO,d:CoverageDayDTO,month:string,mode:"rebuild"|"revalue"|"month"){
    const before=current.current,run=start();
    setSelection({p,d,month});setPending(null);
    try{
      const fetchMonth=(id:string,snapshot_id?:string)=>loadStrategyChain(client,{exchange:p.exchange,product:p.product,trade_date:d.trade_date,underlying_id:id,snapshot_id},run.ctrl.signal);
      const first=await fetchMonth(month,mode==="month"?before.snapshot?.snapshot_id:undefined);
      if(!run.active())return;
      const chains=[first];
      if(mode==="revalue"){
        const keys=client.chainKey?before.positions.flatMap(x=>x.kind==="OPTION"?[x.original.option.expiry_id]:[]):before.positions.map(x=>x.original.underlying.underlying_id);
        const ids=[...new Set(keys)].filter(id=>id!==month&&d.months.some(m=>m.underlying_id===id&&coverageMonthUsable(m)));
        for(const id of ids){chains.push(await fetchMonth(id,first.snapshot.snapshot_id));if(!run.active())return;}
      }
      if(!run.active())return;
      const next=mode==="revalue"?revaluePortfolio(before,first.snapshot,chains):mode==="month"?before:setSnapshot(before,first.snapshot);
      if(next.snapshot?.snapshot_id!==first.snapshot.snapshot_id)throw Error("SNAPSHOT_STALE");
      setState(next);setChain(first);
    }catch(e){if(run.active())setError(e instanceof Error?e.message:"数据不可用");}
    finally{if(run.active())setLoading(false);}
  }
  useEffect(()=>{
    const run=start();setCoverage(null);setSelection(null);setState(s=>emptyPortfolio(s.revision+1));
    void (async()=>{
      try{
        const result=await client.coverage(run.ctrl.signal);if(!run.active())return;
        setCoverage(result);const p=result.products.find(x=>defaultDay(x)!==undefined);const d=p&&defaultDay(p);
        if(!p||!d)throw Error("没有已准入交易日");
        const month=d.months.find(coverageMonthUsable)!;
        await load(p,d,month.underlying_id,"rebuild");
      }catch(e){if(run.active()){setError(e instanceof Error?e.message:"数据不可用");setLoading(false);}}
    })();
    return ()=>{request.current?.abort();generation.current++;};
  },[client]);
  useEffect(()=>{
    const controller=new AbortController();
    setDetails(null);setModelInfo(null);
    if(chain&&client.modelInfo&&client.chainKey)void client.modelInfo(client.chainKey(chain),controller.signal).then(info=>{
      if(!controller.signal.aborted)setModelInfo(info);
    }).catch(()=>{});
    if(!chain||!client.quoteDetails){setDetailStatus("未准入详情");return ()=>controller.abort();}
    setDetailStatus("读取报价详情…");
    void client.quoteDetails(chain,controller.signal).then(d=>{
      if(!controller.signal.aborted){setDetails(d);setDetailStatus("详情已核对 · 理论 Greeks 非盘口估值");}
    }).catch(()=>{if(!controller.signal.aborted)setDetailStatus("报价详情不可用");});
    return ()=>controller.abort();
  },[chain,client]);
  function chooseTarget(p:CoverageProductDTO,d:CoverageDayDTO,month:string){
    if(!usable(d)||!d.months.some(m=>m.underlying_id===month&&coverageMonthUsable(m)))return;
    const snapshot=state.snapshot;
    const productChanged=!!snapshot&&(snapshot.exchange!==p.exchange||snapshot.product!==p.product);
    if(snapshot&&!productChanged&&snapshot.trade_date===d.trade_date){
      // Keep the portfolio for another expiry on the same admitted snapshot.
      if(!chain||(client.chainKey?.(chain)??chain.underlying.underlying_id)!==month||error)
        void load(p,d,month,"month");
      return;
    }
    if(state.positions.length)setPending({p,d,month,productChanged});
    else void load(p,d,month,"rebuild");
  }
  function chooseProduct(key:string){
    const p=coverage?.products.find(x=>productKey(x)===key),d=p&&defaultDay(p);
    const month=d?.months.find(coverageMonthUsable);
    if(p&&d&&month)chooseTarget(p,d,month.underlying_id);
  }
  function chooseDay(day:string){
    const p=selection?.p,d=p?.days.find(x=>x.trade_date===day);
    const month=d?.months.find(m=>m.underlying_id===selection?.month&&coverageMonthUsable(m))??d?.months.find(coverageMonthUsable);
    if(p&&d&&month)chooseTarget(p,d,month.underlying_id);
  }
  const chainChoices=selection?coverageChainChoices(selection.p):[];
  function chooseChain(key:string){
    const choice=chainChoices.find(c=>c.key===key);
    if(selection&&choice)chooseTarget(selection.p,choice.day,choice.month.underlying_id);
  }
  async function changeModel(model:LocalChainModel){
    if(!selection||!chain||!client.setModel||!modelInfo||model===modelInfo.effective_model)return;
    // Model selection changes only the valuation assumption, not the original entry.
    const target=selection,ctrl=new AbortController();
    setModelSaving(true);
    try{
      await client.setModel(target.month,model,ctrl.signal);
      await load(target.p,target.d,target.month,state.positions.length?"revalue":"rebuild");
    }catch(e){setError(e instanceof Error?e.message:"模型保存失败");}
    finally{setModelSaving(false);}
  }
  function applyChange(mode:"rebuild"|"revalue"){
    if(!pending)return;
    const {p,d,month}=pending;
    void load(p,d,month,mode);
  }
  const active=!!chain&&!loading&&!modelSaving&&!error&&state.snapshot?.snapshot_id===chain.snapshot.snapshot_id;
  function quantity(instrument:StrategyInstrument){
    const key=instrumentKey(instrument),id=name(instrument),text=quantityText(state,key),selected=state.positions.some(p=>positionKey(p)===key);
    const enabled=active&&(selected||canSelect(instrument));
    function step(delta:number){setState(s=>{const v=Number(quantityText(s,key));return setQuantity(s,instrument,String((Number.isSafeInteger(v)?v:0)+delta));});}
    return <div className="sb-quantity">
      <button disabled={!enabled} aria-label={id+" 减少数量"} onClick={()=>step(-1)}>−</button>
      <input aria-label={id+" 持仓数量"} inputMode="numeric" disabled={!enabled} value={text} onChange={e=>setState(s=>setQuantity(s,instrument,e.target.value))}/>
      <button disabled={!enabled} aria-label={id+" 增加数量"} onClick={()=>step(1)}>+</button>
    </div>;
  }
  const atm=localCache?modelInfo?.atm_strike??null:chain?.rows.reduce<number|null>((best,r)=>chain.underlying.market_price===null?null:best===null||
    Math.abs(r.strike-chain.underlying.market_price)<Math.abs(best-chain.underlying.market_price)?r.strike:best,null);
  function renderQuantity(q:OptionQuoteDTO){
    if(!chain)return null;
    const i=instrumentForOption(chain,q);
    return q.payoff_eligibility.status!=="ELIGIBLE"?<small>{q.payoff_eligibility.reason??"未准入"}</small>:quantity(i);
  }
  const valid=active?buildPayoffRequest(state):null;
  return <main className="sb-root">
    <header className="sb-head"><div><h1>期权策略构建器</h1><p>{fixture?"合成测试数据 · 不代表真实行情":localCache?"真实 CH 日行情 · 本地模型假设 · 非实盘":"研究模式 · 仅使用已准入数据"}</p></div><span className="sb-tag">v4 · 非实盘</span></header>
    <div className="sb-filters">
      <label>品种<select aria-label="品种" disabled={loading||modelSaving} value={selection?productKey(selection.p):""} onChange={e=>chooseProduct(e.target.value)}>
        {!selection&&<option value="">请选择</option>}{coverage?.products.map(p=><option key={productKey(p)} value={productKey(p)} disabled={!defaultDay(p)}>{p.exchange} {p.product}</option>)}</select></label>
      <label>交易日<select aria-label="交易日" disabled={loading||modelSaving} value={selection?.d.trade_date??""} onChange={e=>chooseDay(e.target.value)}>
        {selection?.p.days.map(d=><option key={d.trade_date} value={d.trade_date} disabled={!usable(d)}>{d.trade_date} · {d.status==="ARCHIVE_ONLY"?"归档":d.status==="INCOMPLETE"?"部分覆盖":d.status}</option>)}</select></label>
      <label>期权链<select aria-label="期权链" disabled={loading||modelSaving||chainChoices.length===0} value={selection?coverageChainKey(selection.d.trade_date,selection.month):""} onChange={e=>chooseChain(e.target.value)}>
        {!chainChoices.length&&<option value="">无已准入期权链</option>}
        {chainChoices.map(({key,day,month})=><option key={key} value={key}>{localCache?month.underlying_id.split("|").slice(1).join(" · "):month.underlying_id} · {day.trade_date} · {month.admitted_contracts}/{month.discovered_contracts} 合约</option>)}</select></label>
      <span>{localCache?"本地缓存日 "+(selection?.d.trade_date??""):selection?.p.latest_complete_trade_date?"最新完整日 "+selection.p.latest_complete_trade_date:"最新完整交易日未验证"}</span>
      {localCache&&<><label>定价模型<select aria-label="定价模型" disabled={!modelInfo||loading||modelSaving||modelInfo.models.length===1} value={modelInfo?.effective_model??""} onChange={e=>void changeModel(e.target.value as LocalChainModel)}>
        {!modelInfo&&<option value="">读取模型…</option>}{modelInfo?.models.map(m=><option key={m} value={m}>{m==="AMERICAN_BINOMIAL"?"CRR 二叉树（美式）":m==="BLACK_76"?(modelInfo.model_policy==="CFFEX_EUROPEAN_BLACK76"?"Black-76（欧式股指）":"Black-76（欧式期货）"):"Black-Scholes（欧式现货）"}</option>)}</select></label>
        <span>年化无风险利率 2.00% · 连续复利</span>
        {modelInfo&&<p className="sb-coverage-note">原始行权方式 {modelInfo.raw_styles.join("/")} · {modelInfo.model_policy==="CFFEX_EUROPEAN_BLACK76"?"中金所规则：欧式 Black-76":modelInfo.user_override?"已记忆用户模型":modelInfo.raw_styles.includes("UNKNOWN")?"UNKNOWN 默认美式假设":"按已知条款默认模型"} · {modelInfo.underlying_source==="MISSING_UNDERLYING"?"缺少同日标的价格：仅查看期权链":modelInfo.pricing_underlying===null?"缺少有效认购认沽配对，IV/Greeks 不可计算":`原框架期权链 · ATM ${fmt(modelInfo.atm_strike)} · 定价标的 ${fmt(modelInfo.pricing_underlying)} · 调整量 ${fmt(modelInfo.underlying_adjustment)}（历史日行情，非实时盘口）`}。模型仅作本地研究，不更改原始条款。</p>}</>}
      {coverage&&<p className="sb-coverage-note">当前数据覆盖：{coverage.products.filter(p=>coverageChainChoices(p).length>0).length} 个品种 · {coverage.products.reduce((sum,p)=>sum+coverageChainChoices(p).length,0)} 条{localCache?"本地缓存期权链。缺少标的价格仍可查看真实合约；不生成虚假估值。":"已准入期权链。期权链下拉框包含当前品种所有已准入交易日及交割月；未覆盖品种不提供虚假报价。"}</p>}
    </div>
    {pending&&<section role="dialog" aria-label="切换持仓确认" className="sb-dialog">
      <strong>{pending.productChanged?"切换品种将清空当前持仓":"切换交易日"} · {pending.month} · {pending.d.trade_date}</strong>
      <button onClick={()=>applyChange("rebuild")}>重新构建</button>
      {!pending.productChanged&&<button onClick={()=>applyChange("revalue")}>保留建仓成本重估</button>}
      <button onClick={()=>setPending(null)}>取消</button>
    </section>}
    <div aria-live="polite" className="sb-status">{loading?"读取已准入快照…":error?"不可用："+error:chain?chain.snapshot.trade_date+" · "+(localCache?"LOCAL_SQLITE / 用户授权假设":chain.snapshot.provider)+" · "+chain.snapshot.price_basis:"无快照"}</div>
    <div className="sb-layout" data-split={split} data-wide={wide} ref={layout}>
      <section className="sb-panel sb-chain"><div className="sb-quote-toolbar"><h2>T 型报价</h2><label>Greeks 精度<select aria-label="Greeks 精度" value={precision} onChange={e=>setPrecision(Number(e.target.value))}>{Array.from({length:11},(_,n)=><option key={n}>{n}</option>)}</select></label><button onClick={()=>setWide(v=>!v)}>{wide?"左右分栏":"全宽报价"}</button></div>
        {chain&&active?<><div className="sb-underlying"><strong>{chain.underlying.underlying_id} · {fmt(chain.underlying.market_price)}</strong><span>对应标的仓位</span>{quantity(instrumentForUnderlying(chain))}</div>
          <p className="sb-quote-note">{detailStatus} · — 表示无准入字段 · 净持仓为研究数量，非账户持仓。{localCache?"收盘价、结算价为真实日行情；模型IV与Greeks共用原框架校准，不显示虚构盘口。":"标记价及结算隐波仅见代码提示。"}</p>
          <QuoteMatrix chain={chain} details={details?.snapshot.snapshot_id===chain.snapshot.snapshot_id&&details.underlying_id===chain.underlying.underlying_id?details:null} atm={atm??null} precision={precision} renderQuantity={renderQuantity}/></>:<p>无可用报价</p>}
      </section>
      <div className="sb-divider" role="separator" aria-label="调整分栏" aria-orientation="vertical" aria-valuemin={35} aria-valuemax={70} aria-valuenow={split} tabIndex={0}
        onKeyDown={e=>{if(e.key==="ArrowLeft"||e.key==="ArrowRight"){e.preventDefault();setSplit(s=>Math.min(70,Math.max(35,s+(e.key==="ArrowRight"?5:-5))));}}}
        onPointerDown={e=>e.currentTarget.setPointerCapture(e.pointerId)}
        onPointerMove={e=>{if(e.currentTarget.hasPointerCapture(e.pointerId)&&layout.current){const r=layout.current.getBoundingClientRect();setSplit(Math.min(70,Math.max(35,Math.round((e.clientX-r.left)/r.width*20)*5)));}}}/>
      <section className="sb-panel sb-holdings"><h2>策略持仓 <small>{optionCount(state)}/8 期权腿</small></h2>
        {!state.positions.length&&<p>在 T 型报价上输入正数买入、负数卖出，0 删除。</p>}
        <div className="sb-positions">{state.positions.map(p=>{
          const i=p.original,k=positionKey(p),mark=p.current?.kind==="OPTION"?p.current.option.market_price:p.current?.underlying.market_price??null;
          return <article className="sb-position" key={k} data-testid={"position-"+name(i)}>
            <div><strong>{name(i)}</strong><span className={p.entry.quantity>0?"sb-long":"sb-short"}>{p.entry.quantity>0?"多头":"空头"}</span></div>
            {quantity(i)}
            <label>建仓价<input aria-label={name(i)+" 建仓价"} inputMode="decimal" disabled={!active} value={entryText(state,k)} onChange={e=>setState(s=>setEntryPrice(s,k,e.target.value))}/></label>
            <small>建仓日 {p.entry.entry_trade_date} · 当前标记 {fmt(active?mark:null)}</small>
            {p.unavailable&&<span className="sb-error">不可用：{p.unavailable}</span>}
            <button disabled={loading} onClick={()=>setState(s=>setQuantity(s,i,"0"))}>移除 {name(i)}</button>
          </article>;
        })}</div>
        {Object.entries(state.errors).map(([k,v])=><p key={k} className="sb-error">{k}：{v}</p>)}
        <div className="sb-analysis"><h2>策略分析</h2><p data-testid="portfolio-ready">{valid?"持仓有效 · 等待计算":state.positions.length?"持仓尚不可计算":"尚未选择持仓"}</p>
          {analysis?.(state,active,()=>setState(s=>({...s,revision:s.revision+1})))}
          {onAdvanced&&<button disabled={!valid} onClick={()=>onAdvanced(state)}>高级压力测试</button>}
        </div>
      </section>
    </div>
  </main>;
}

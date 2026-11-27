import { useEffect, useRef, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { ApiError, buildRequest, errorMessage, fetchCoverage, fetchValuation, reasonMessage, type CoverageDTO as LegacyCoverageDTO, type GridPointDTO, type ValuationDTO as LegacyValuationDTO } from "./api";
import { buildChainRequest, fetchChainCoverage, fetchChainValuation, type ChainCoverageDTO, type ChainCoverageLegDTO, type ChainValuationDTO } from "./chain-api";
import { buildCatalogRequest, expiryKey, fetchCatalogCoverage, fetchCatalogValuation, type CatalogCoverageDTO, type CatalogValuationDTO } from "./catalog-api";
type CoverageDTO=LegacyCoverageDTO|ChainCoverageDTO|CatalogCoverageDTO;
type ValuationDTO=LegacyValuationDTO|ChainValuationDTO|CatalogValuationDTO;
const apiVersion=new URLSearchParams(window.location.search).get("api");
const legacyMode=apiVersion==="v1";
const catalogMode=apiVersion!=="v1"&&apiVersion!=="v2";
const isChain=(c:CoverageDTO):c is ChainCoverageDTO=>c.scope==="ARCHIVED_HISTORICAL_CHAIN_INPUTS_ONLY";
const isCatalog=(c:CoverageDTO):c is CatalogCoverageDTO=>c.scope==="ARCHIVED_HISTORICAL_CATALOG_INPUTS_ONLY";
const DEFAULT_PAIR=["CU2211C61000.XSGE","CU2211P61000.XSGE"];
import "./styles.css";
import "./chain.css";

const format=(n:number,digits=0)=>new Intl.NumberFormat("zh-CN",{maximumFractionDigits:digits,minimumFractionDigits:digits}).format(n);
const money=(n:number|null)=>n===null?"不可用":format(n,2);
const signed=(n:number|null,digits=0)=>n===null?"不可用":(n>0?"+":"")+format(n,digits);
const tone=(n:number|null)=>n===null?"muted":n>0?"positive":n<0?"negative":"neutral";
const percent=(x:number)=>signed(x*100,2).replace(/\.00$/,"").replace(/(\.\d)0$/,"$1")+"%";
const pp=(x:number)=>signed(x*100,2).replace(/\.00$/,"").replace(/(\.\d)0$/,"$1")+" pp";
const localTime=(value:string)=>new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date(value));
const shortHash=(x:string)=>x.slice(0,12)+"…";
const dateBefore=(date:string)=>new Date(Date.parse(date+"T00:00:00Z")-86400000).toISOString().slice(0,10);

function Icon({name}:{name:"grid"|"lock"|"chart"|"refresh"|"check"|"arrow"|"close"}){
  const paths:Record<typeof name,string>={
    grid:"M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    lock:"M6 11h12v10H6z M8 11V7a4 4 0 0 1 8 0v4",
    chart:"M3 3v18h18 M6 15l5-6 4 4 5-8",
    refresh:"M20 11a8 8 0 1 0-2 6 M20 4v7h-7",
    check:"M5 12l4 4L19 6",
    arrow:"M5 12h14 M13 6l6 6-6 6",
    close:"M6 6l12 12 M18 6L6 18",
  };
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;
}

function SectionTitle({title,sub}:{title:string,sub?:string}){
  return <div className="section-title"><h2>{title}</h2>{sub&&<span>{sub}</span>}</div>;
}
function Metric({label,value,sub,kind="neutral",testId}:{label:string,value:string,sub:string,kind?:string,testId?:string}){
  return <article className="metric"><span className="metric-label">{label}</span><strong className={kind} data-testid={testId}>{value}</strong><span className="metric-sub">{sub}</span></article>;
}

function Curve({points,select}:{points:GridPointDTO[],select:(p:GridPointDTO)=>void}){
  const valid=points.filter(p=>p.status==="OK"&&p.value_change!==null);
  const x0=points[0]?.price_shock??-0.1,x1=points.at(-1)?.price_shock??0.1;
  const values=valid.map(p=>p.value_change!);
  const min=Math.min(0,...values),max=Math.max(0,...values);
  const pad=Math.max((max-min)*0.12,1),lo=min-pad,hi=max+pad;
  const W=660,H=242,L=74,R=22,T=18,B=44;
  const sx=(v:number)=>L+(v-x0)/(x1-x0||1)*(W-L-R);
  const sy=(v:number)=>T+(hi-v)/(hi-lo)*(H-T-B);
  let drawing="",open=false;
  for(const p of points){
    if(p.value_change===null||p.status!=="OK"){open=false;continue;}
    drawing+=(open?"L":"M")+sx(p.price_shock).toFixed(2)+" "+sy(p.value_change).toFixed(2)+" ";
    open=true;
  }
  const yticks=[lo+(hi-lo)*0.15,lo+(hi-lo)*0.5,lo+(hi-lo)*0.85];
  return <section className="panel curve-panel" aria-labelledby="curve-heading">
    <div className="section-title"><h2 id="curve-heading">价格响应曲线</h2><span>IV 固定 · CNY</span></div>
    <svg className="curve" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="标的价格变化与组合理论价值变化曲线；无效点不连线">
      {yticks.map((y,i)=><g key={i}><line className="grid-line" x1={L} x2={W-R} y1={sy(y)} y2={sy(y)}/><text x={L-10} y={sy(y)+4} textAnchor="end">{signed(y)}</text></g>)}
      <line className="zero-line" x1={L} x2={W-R} y1={sy(0)} y2={sy(0)}/>
      <line className="origin-line" x1={sx(0)} x2={sx(0)} y1={T} y2={H-B}/>
      <path className="curve-path" d={drawing} fill="none" strokeWidth="2.8"/>
      {points.map((p,i)=><g key={i}>
        {(i%2===0||i===points.length-1)&&<text x={sx(p.price_shock)} y={H-20} textAnchor="middle">{percent(p.price_shock)}</text>}
        {p.value_change!==null&&p.status==="OK"&&<circle cx={sx(p.price_shock)} cy={sy(p.value_change)} r="4.1" className="curve-dot"><title>{percent(p.price_shock)} / {signed(p.value_change,2)} 元</title></circle>}
        {p.status==="UNAVAILABLE"&&<g className="invalid-point"><path d={`M${sx(p.price_shock)-4} ${H-B-4}l8 8m0-8l-8 8`} stroke="currentColor"><title>不可用 {percent(p.price_shock)}</title></path></g>}
      </g>)}
      <text className="axis-label" x={(L+W-R)/2} y={H-3} textAnchor="middle">标的价格冲击</text>
    </svg>
    <details className="curve-values"><summary>查看全部 {points.length} 个曲线点</summary><div className="curve-point-list">{points.map((p,i)=><button key={i} type="button" className="point-chip" onClick={()=>select(p)} aria-label={`曲线点 ${percent(p.price_shock)} ${p.status==="OK"?signed(p.value_change,2)+" 元":"不可用"}`}><span>{percent(p.price_shock)}</span><strong className={tone(p.value_change)}>{signed(p.value_change)}</strong></button>)}</div></details>
  </section>;
}

function Heatmap({value,selected,select}:{value:ValuationDTO,selected:GridPointDTO|null,select:(p:GridPointDTO)=>void}){
  const g=value.grid;
  const cells=g.heatmap_rows.flat();
  const valid=cells.filter(x=>x.status==="OK").length;
  const peak=Math.max(1,...cells.map(c=>Math.abs(c.value_change??0)));
  const rows=g.heatmap_iv_axis.map((iv,index)=>({iv,index})).reverse();
  function fill(c:GridPointDTO){
    if(c.status==="UNAVAILABLE"||c.value_change===null)return "cell-invalid";
    if(Math.abs(c.value_change)<1e-8)return "cell-zero";
    const strength=Math.max(1,Math.min(4,Math.ceil(Math.abs(c.value_change)/peak*4)));
    return (c.value_change>0?"cell-pos-":"cell-neg-")+strength;
  }
  return <section className="panel heatmap-panel" aria-labelledby="heatmap-heading">
    <div className="section-title"><h2 id="heatmap-heading">风险热力图</h2><span>{valid}/{cells.length} 有效 · CNY</span></div>
    <div className="matrix-caption"><span>行：IV 绝对变化（百分点）</span><span>列：标的价格变化</span></div>
    <div className="table-scroll">
      <table className="matrix" aria-label="风险热力图">
        <thead><tr><th scope="col" className="corner">IV / 价格</th>{g.heatmap_price_axis.map(x=><th scope="col" key={x}>{percent(x)}</th>)}</tr></thead>
        <tbody>{rows.map(({iv,index})=><tr key={iv}><th scope="row">{pp(iv)}</th>{g.heatmap_rows[index]!.map((cell,i)=>{
          const active=selected?.price_shock===cell.price_shock&&selected.iv_shock===cell.iv_shock;
          return <td key={i}><button type="button" data-testid={`heatmap-${index}-${i}`} aria-pressed={active} aria-label={`IV ${pp(iv)}，价格 ${percent(cell.price_shock)}，${cell.value_change===null?"不可用":signed(cell.value_change,2)+" 元"}`} className={`matrix-cell ${fill(cell)} ${active?"selected":""} ${cell.price_shock===0&&cell.iv_shock===0?"origin":""}`} onClick={()=>select(cell)}>{cell.value_change===null?<span>—<small>不可用</small></span>:<span>{signed(cell.value_change)}</span>}</button></td>;
        })}</tr>)}</tbody>
      </table>
    </div>
    <div className="legend"><span><i className="cell-neg-3"/>理论损失</span><span><i className="cell-zero"/>零附近</span><span><i className="cell-pos-3"/>理论收益</span><span><i className="cell-invalid"/>不可用</span></div>
    <div className="hint">点击任意格查看各腿贡献；矩阵为完整重新定价，不是 Greeks 近似。</div>
  </section>;
}

function CellDetail({point}:{point:GridPointDTO|null}){
  if(!point)return null;
  return <section className="panel cell-detail" aria-live="polite" data-testid="cell-detail">
    <SectionTitle title="情景明细" sub={`价格 ${percent(point.price_shock)} · IV ${pp(point.iv_shock)}`}/>
    {point.status==="UNAVAILABLE"?<div className="unavailable"><strong>此情景不可用</strong><p>{reasonMessage(point.reason_code)} · 不提供数值，不视作零。</p></div>:<>
      <div className="detail-values">
        <div><span>基准价值</span><strong>{money(point.baseline_value)}</strong></div>
        <div><span>情景价值</span><strong>{money(point.scenario_value)}</strong></div>
        <div><span>理论价值变化</span><strong className={tone(point.value_change)} data-testid="selected-value">{signed(point.value_change,2)}</strong></div>
      </div>
      <div className="table-scroll"><table className="data-table"><thead><tr><th>合约</th><th>情景 IV</th><th>剩余天数</th><th>情景价值</th><th>变化 (CNY)</th></tr></thead><tbody>
        {point.contributions.map(c=><tr key={c.contract_id}><td className="mono">{c.contract_id}</td><td>{format(c.scenario_iv*100,2)}%</td><td>{format(c.scenario_time_to_expiry*365,2)}</td><td>{money(c.scenario_value)}</td><td className={tone(c.value_change)}>{signed(c.value_change,2)}</td></tr>)}
      </tbody></table></div>
    </>}
  </section>;
}

function LegTable({value}:{value:ValuationDTO}){
  return <section className="panel">
    <SectionTitle title="合约估值与 Greeks" sub="已按实际合约乘数及有符号持仓换算"/>
    <div className="table-scroll"><table className="data-table leg-table" aria-label="每腿估值及风险">
      <thead><tr><th>合约</th><th>持仓</th><th>结算价</th><th>IV</th><th>基准价值</th><th>Delta</th><th>Gamma</th><th>日 Theta</th><th>Vega / pp</th></tr></thead>
      <tbody>{value.legs.map(l=><tr key={l.contract_id}>
        <td><strong className="mono">{l.contract_id}</strong><small className="subline">{l.option_type} · K {format(l.strike)} · {l.contract_multiplier} 吨/手{"expiry_id" in l?" · "+l.expiry_date:""}</small></td>
        <td className={l.quantity>0?"positive":"negative"}>{signed(l.quantity)} 手</td>
        <td>{format(l.market_option_price,2)}</td><td>{format(l.implied_volatility*100,4)}%</td><td>{money(l.baseline_value)}</td>
        <td>{format(l.baseline_greeks.delta,4)}</td><td>{format(l.baseline_greeks.gamma,7)}</td>
        <td className={tone(l.baseline_greeks.theta_annual)}>{signed(l.baseline_greeks.theta_annual/365,2)}</td><td>{signed(l.baseline_greeks.vega_per_unit/100,2)}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="hint">Delta：元 / 标的价格单位；Gamma：元 / 价格单位²；Theta：年化÷365；Vega：每 1 个绝对波动率百分点。不同交割月不混合聚合。</p>
  </section>;
}

function Evidence({value}:{value:ValuationDTO}){
  return <details className="panel evidence"><summary><span>数据证据、定价口径与限制</span><span className="muted">展开核对</span></summary>
    <div className="evidence-grid">
      <dl><dt>历史基准</dt><dd>{localTime(value.reference_at)} 北京时间（名义）</dd><dt>情景时点</dt><dd>{localTime(value.scenario_at)} 北京时间</dd><dt>实际观测时间</dt><dd>{value.data_observed_at}</dd><dt>重新准入时间</dt><dd>{value.admitted_at}</dd><dt>审批时间</dt><dd>{value.approved_at}</dd><dt>到期日名义时点</dt><dd>{"expiries" in value?value.expiries.map(e=><span className="subline" key={e.expiry_id}>{e.underlying_id} · {localTime(e.nominal_expiry_at)}</span>):localTime(value.nominal_expiry_at)} · 到期日不支持</dd></dl>
      <dl><dt>报价基准</dt><dd>{value.price_basis} · 无回退</dd><dt>利率代理</dt><dd>中债 3M {format(value.rate.quoted_percent,4)}% · {value.rate.quote_date}</dd><dt>连续利率</dt><dd>{format(value.rate.continuous_rate*100,6)}% · ACT/365F</dd><dt>模型</dt><dd>{value.model} · {value.tree_steps} 步</dd><dt>模型版本</dt><dd className="mono">{value.model_version}</dd><dt>来源</dt><dd className="wrap-anywhere">{value.rate.source_uri}</dd></dl>
    </div>
    <div className="evidence-grid hash-grid"><dl><dt>已审批数据包 SHA-256</dt><dd className="mono wrap-anywhere">{value.source_versions.packet_sha256}</dd><dt>条款登记 SHA-256</dt><dd className="mono wrap-anywhere">{value.source_versions.registry_sha256}</dd></dl><dl><dt>利率档案 SHA-256</dt><dd className="mono wrap-anywhere">{value.rate.artifact_sha256}</dd><dt>源数据档案</dt><dd>{value.source_versions.artifacts.length} 份已验证文件</dd></dl></div>
    <div className="table-scroll"><table className="data-table"><thead><tr><th>原始合约键</th><th>原始版本 / 行权方式</th><th>有效条款版本</th><th>官方记录 ID</th></tr></thead><tbody>{value.legs.map(l=><tr key={l.source_symbol}><td className="mono">{l.source_symbol}</td><td>空版本 / {l.raw_exercise_style}</td><td className="mono" title={l.effective_contract_version}>{shortHash(l.effective_contract_version)}</td><td>{l.official_record_id} · {l.exercise_style}</td></tr>)}</tbody></table></div>
    {"child_packet_sha256" in value&&<div className="hash-grid"><strong>本组合交割月证据</strong>{value.child_packet_sha256.map(p=><p key={p} className="mono wrap-anywhere">{p}</p>)}</div>}
    <p className="hint">历史结算价并非成交价。3M 国债收益率仅为代理，名义日终时点不代表真实发布时点；二叉树离散化容差未获生产批准。当前非真实 PIT 回测、非实盘定价、非交易授权。</p>
  </details>;
}

type State="disconnected"|"connecting"|"ready"|"calculating"|"complete"|"dirty"|"error";
type Operation={controller:AbortController,timer:ReturnType<typeof setTimeout>};
function App(){
  const [state,setState]=useState<State>("disconnected");
  const [credential,setCredential]=useState("");
  const [coverage,setCoverage]=useState<CoverageDTO|null>(null);
  const [result,setResult]=useState<ValuationDTO|null>(null);
  const [positions,setPositions]=useState<Record<string,string>>({});
  const [pick,setPick]=useState("");
  const [month,setMonth]=useState("SHFE.cu2211");
  const [priceOverrides,setPriceOverrides]=useState<Record<string,string>>({});
  const [ivOverrides,setIvOverrides]=useState<Record<string,string>>({});
  const [rightFilter,setRightFilter]=useState<"ALL"|"CALL"|"PUT">("ALL");
  const [strikeFilter,setStrikeFilter]=useState("");
  const [scenario,setScenario]=useState("");
  const [priceRange,setPriceRange]=useState("10");
  const [ivRange,setIvRange]=useState("6");
  const [selected,setSelected]=useState<GridPointDTO|null>(null);
  const [message,setMessage]=useState("");
  const [error,setError]=useState(false);
  const token=useRef("");
  const current=useRef<Operation|null>(null);
  const busy=state==="connecting"||state==="calculating";
  const selectedLegs=coverage?.legs.filter(l=>Object.hasOwn(positions,l.source_symbol))??[];
  const catalog=coverage&&isCatalog(coverage)?coverage:null;
  const chain=coverage&&(isChain(coverage)||isCatalog(coverage))?coverage:null;
  const available=chain?.legs.filter(l=>l.underlying_id===month&&(rightFilter==="ALL"||l.option_type===rightFilter)&&
    (!strikeFilter||String(l.strike).includes(strikeFilter)))??[];
  const selectedMonths=[...new Map(selectedLegs.map(l=>[l.underlying_id,l])).values()];
  const earliestExpiry=selectedLegs.map(l=>l.expiry_date).sort()[0]??coverage?.legs[0]?.expiry_date??"";
  const picked=available.find(l=>l.source_symbol===pick);
  const canAdd=Boolean(picked&&!picked.at_intrinsic&&!Object.hasOwn(positions,pick)&&selectedLegs.length<8);


  function end(op:Operation){
    clearTimeout(op.timer);
    if(current.current===op)current.current=null;
  }
  function cancel(){
    const op=current.current;
    if(op){current.current=null;clearTimeout(op.timer);op.controller.abort();}
  }
  useEffect(()=>()=>{cancel();token.current="";},[]);
  function begin():Operation{
    const controller=new AbortController();
    const op:Operation={controller,timer:setTimeout(()=>{
      if(current.current!==op)return;
      current.current=null;controller.abort();
      setResult(null);setSelected(null);setError(true);setMessage("请求超时，未展示估值。服务器可能仍在计算，请稍后手动重试。");setState("error");
    },30000)};
    current.current=op;
    return op;
  }
  function fail(err:unknown){
    setError(true);setMessage(errorMessage(err));setResult(null);setSelected(null);
    if(err instanceof ApiError&&err.code==="UNAUTHORIZED"){
      token.current="";setCoverage(null);setState("disconnected");
    }else setState("error");
  }
  async function connect(e:FormEvent){
    e.preventDefault();
    if(current.current)return;
    setError(false);setMessage("");
    if(!/^[!-~]{32,256}$/.test(credential)){setError(true);setMessage("请输入 32–256 字符的本地研究令牌（不含空格）。");return;}
    const supplied=credential;
    setCredential("");setState("connecting");
    const op=begin();
    try{
      const c=await (legacyMode?fetchCoverage(supplied,op.controller.signal):catalogMode?fetchCatalogCoverage(supplied,op.controller.signal):fetchChainCoverage(supplied,op.controller.signal));
      if(current.current!==op)return;
      token.current=supplied;installCoverage(c);
      setPriceRange("10");setIvRange("6");
    }catch(err){if(current.current===op)fail(err);}
    finally{end(op);}
  }
  function installCoverage(c:CoverageDTO){
    setCoverage(c);setResult(null);setSelected(null);
    setPositions(Object.fromEntries(c.legs.filter(l=>DEFAULT_PAIR.includes(l.source_symbol)).map(l=>[l.source_symbol,"1"])));
    setMonth(c.legs[0]?.underlying_id??"SHFE.cu2211");
    setPriceOverrides({});setIvOverrides({});setPick("");setRightFilter("ALL");setStrikeFilter("");
    setScenario(c.trade_date+"T15:00");setState("ready");setError(false);
    setMessage("已核验历史切片。请确认假设持仓后执行估值。");
  }
  async function changeTradeDate(day:string){
    if(!catalog||day===catalog.trade_date||current.current||!token.current)return;
    setResult(null);setSelected(null);setState("connecting");setError(false);setMessage("正在切换历史交易日…");
    const op=begin();
    try{
      const c=await fetchCatalogCoverage(token.current,op.controller.signal,day);
      if(current.current!==op)return;
      installCoverage(c);
    }catch(err){if(current.current===op)fail(err);}
    finally{end(op);}
  }
  function disconnect(){
    cancel();token.current="";setCredential("");setCoverage(null);setResult(null);setSelected(null);
    setState("disconnected");setMessage("");setError(false);setPositions({});setPick("");setRightFilter("ALL");setStrikeFilter("");setScenario("");setMonth("SHFE.cu2211");setPriceOverrides({});setIvOverrides({});
  }
  function edit(update:()=>void){
    if(current.current)return;
    update();setResult(null);setSelected(null);setState("dirty");setError(false);
    setMessage("参数已修改 · 请重新计算，旧结果已清除。");
  }
  async function calculate(e:FormEvent){
    e.preventDefault();
    if(!coverage||!token.current||current.current)return;
    setResult(null);setSelected(null);setError(false);setMessage("");
    let requestValuation:(signal:AbortSignal)=>Promise<ValuationDTO>;
    try{
      if(isCatalog(coverage)){
        const req=buildCatalogRequest(coverage,positions,scenario,priceRange,ivRange,priceOverrides,ivOverrides);
        requestValuation=signal=>fetchCatalogValuation(token.current,coverage,req,signal);
      }else if(isChain(coverage)){
        const req=buildChainRequest(coverage,positions,scenario,priceRange,ivRange);
        requestValuation=signal=>fetchChainValuation(token.current,coverage,req,signal);
      }else{
        const req=buildRequest(coverage,coverage.legs.map(l=>positions[l.source_symbol]??""),scenario,priceRange,ivRange);
        requestValuation=signal=>fetchValuation(token.current,coverage,req,signal);
      }
    }
    catch(err){fail(err);return;}
    setState("calculating");
    setMessage("正在重新准入历史证据、反解 IV 并完整重估风险矩阵…");
    const op=begin();
    try{
      const v=await requestValuation(op.controller.signal);
      if(current.current!==op)return;
      setResult(v);
      const g=v.grid;setSelected(g.heatmap_rows[g.heatmap_iv_axis.indexOf(0)]?.[g.heatmap_price_axis.indexOf(0)]??null);
      setState("complete");setMessage("计算完成 · 本次参数与数据版本已核对。");
    }catch(err){if(current.current===op)fail(err);}
    finally{end(op);}
  }
  const g=result?.grid;
  const exposure=result?.underlying_exposures[0];
  const resultMonths=result?[...new Map(result.legs.map(l=>[l.underlying_id,l])).values()]:[];
  const maturityDays=result?("expiries" in result?result.expiries.map(e=>e.baseline_time_to_expiry*365):[result.time_to_expiry_years*365]):[];
  const maturityLabel=maturityDays.length?format(Math.min(...maturityDays),0)+(Math.min(...maturityDays)!==Math.max(...maturityDays)?"–"+format(Math.max(...maturityDays),0):"")+" 天":"—";
  return <div className="shell">
    <header className="topbar">
      <div className="brand"><span className="brandmark"><Icon name="grid"/></span><div><strong>OptionMaster</strong><span>RESEARCH WORKBENCH</span></div></div>
      <div className="top-center"><span className="nav-active">期权组合研究</span><span className="top-separator"/><span className="muted">API {legacyMode?"v1.0":catalogMode?"v3.0":"v2.0"}</span></div>
      <div className="top-right"><span className="scope-pill"><span className="status-dot"/>历史归档 · 非生产</span>{coverage&&<button className="ghost" type="button" onClick={disconnect}>断开连接</button>}</div>
    </header>
    <main className="workspace">
      <div className="page-heading"><div><div className="eyebrow">DERIVATIVES / SCENARIO LAB</div><h1>期权风险研究工作台</h1><p>基于已审核历史条款与结算价的组合完整重估</p></div><div className="scope-note"><Icon name="lock"/><div><strong>只读研究环境</strong><span>无订单权限 · 不连接 CTP</span></div></div></div>
      {!coverage?<section className="panel login-panel">
        <div className="login-icon"><Icon name="lock"/></div><h2>连接本地研究服务</h2><p>令牌仅在当前页面内存中使用。刷新或断开后需重新输入；不会保存到浏览器存储。</p>
        <form onSubmit={connect} noValidate><label htmlFor="bearer">本地研究令牌</label><input id="bearer" type="password" value={credential} onChange={e=>setCredential(e.target.value)} autoComplete="off" spellCheck={false} disabled={busy} maxLength={256}/><button className="primary" disabled={busy} type="submit">{busy?"正在核验…":"连接并验证历史数据"} <Icon name="arrow"/></button>{busy&&<button className="ghost" type="button" onClick={disconnect}>取消连接</button>}</form>
        <div role={error?"alert":"status"} aria-live="polite" className={error?"feedback error":"feedback"}>{message}</div>
        <div className="login-foot"><span>数据未连接，暂无估值。</span><span>服务端限定已批准切片。</span></div>
      </section>:<>
        <div className="data-strip"><div className="data-identity"><span className="market-tag">SHFE</span><strong>{catalog?"沪铜多期限":coverage.legs[0]?.underlying_id}</strong><span className="muted">沪铜期权</span></div><div><span className="strip-label">交易日</span><strong>{coverage.trade_date}</strong></div><div><span className="strip-label">报价口径</span><strong>结算价 SETTLEMENT</strong></div><div><span className="strip-label">数据准入</span><strong className="positive"><Icon name="check"/> {coverage.admitted_legs}/{coverage.requested_legs}</strong></div><div><span className="strip-label">{catalog?"最早选中到期日":"到期日"}</span><strong>{earliestExpiry||"—"}</strong></div></div>
        <div className="layout">
          <aside className="sidebar">
            <section className="panel builder">
              <SectionTitle title="组合构建器" sub="假设持仓"/>
              <form onSubmit={calculate} noValidate>
                <fieldset disabled={busy}><legend>合约及数量</legend>
                  {chain&&<div className="chain-picker" data-testid="chain-picker">
                    <div className="picker-title"><strong>历史期权链</strong><span className="positive" data-testid="chain-count">{chain.admitted_legs} 张已准入</span></div>
                    {catalog&&<><label htmlFor="trade-date">历史交易日</label><select id="trade-date" value={catalog.trade_date} onChange={e=>void changeTradeDate(e.target.value)}>{catalog.available_dates.map(d=><option key={d} value={d}>{d}</option>)}</select></>}
                    <label htmlFor="chain-month">交割月</label>
                    <select id="chain-month" value={month} onChange={e=>{setMonth(e.target.value);setPick("");}} aria-label="交割月">
                      {chain.months.map(m=><option key={m.underlying_id} value={m.underlying_id} disabled={!m.admitted_contracts}>{m.underlying_id.replace("SHFE.","").toUpperCase()} · {m.admitted_contracts?m.admitted_contracts+" 张已准入":m.discovered_contracts+" 张仅发现"}</option>)}
                    </select>
                    <div className="picker-filters"><div><label htmlFor="right-filter">方向</label><select id="right-filter" value={rightFilter} onChange={e=>{setRightFilter(e.target.value as "ALL"|"CALL"|"PUT");setPick("");}}><option value="ALL">全部</option><option value="CALL">Call</option><option value="PUT">Put</option></select></div><div><label htmlFor="strike-filter">筛选行权价</label><input id="strike-filter" inputMode="numeric" value={strikeFilter} onChange={e=>{setStrikeFilter(e.target.value);setPick("");}} placeholder="如 61000"/></div></div>
                    <label htmlFor="contract-pick">选择新增合约</label>
                    <select id="contract-pick" value={pick} onChange={e=>setPick(e.target.value)}>
                      <option value="">选择合约（{available.length} 张）</option>
                      {["CALL","PUT"].map(right=><optgroup key={right} label={right}>{available.filter(l=>l.option_type===right).map(l=><option key={l.source_symbol} value={l.source_symbol} disabled={l.at_intrinsic||Object.hasOwn(positions,l.source_symbol)}>{format(l.strike)} · {right} · 结 {format(l.settlement)}{l.at_intrinsic?" · IV不可反解":Object.hasOwn(positions,l.source_symbol)?" · 已选":""}</option>)}</optgroup>)}
                    </select>
                    <div className="small-actions"><button className="secondary" type="button" disabled={!canAdd} data-testid="add-contract" onClick={()=>edit(()=>{setPositions(p=>({...p,[pick]:"1"}));setPick("");})}>＋ 添加合约</button><button className="secondary" type="button" onClick={()=>edit(()=>{setPositions(Object.fromEntries(coverage.legs.filter(l=>DEFAULT_PAIR.includes(l.source_symbol)).map(l=>[l.source_symbol,"1"])));setPick("");setPriceOverrides({});setIvOverrides({});})}>重置跨式</button></div>
                    <p className="hint"><span data-testid="selected-count">已选 {selectedLegs.length}/8 腿</span> · {catalog?"2–8 腿同日跨月组合。切换日期会重置持仓。":"2–8 腿同月组合。"} 未准入月份不可选择；结算价无时间价值的合约不用于 IV 重估。</p>
                  </div>}
                  {selectedLegs.map((l,i)=><div className="leg-control" key={l.source_symbol} data-testid="selected-leg">
                    <div className="leg-name"><span className={l.option_type==="CALL"?"call-tag":"put-tag"}>{l.option_type}</span><strong className="mono">{l.contract_id}</strong>{chain&&<button className="remove-leg" type="button" aria-label={"移除 "+l.contract_id} onClick={()=>edit(()=>setPositions(p=>Object.fromEntries(Object.entries(p).filter(([s])=>s!==l.source_symbol))))}><Icon name="close"/></button>}</div>
                    <span className="hint">行权价 {format(l.strike)} · {l.contract_multiplier} 吨/手 · 美式{catalog?" · "+l.expiry_date:""}{chain&&"settlement" in l? " · 结算 "+format((l as ChainCoverageLegDTO).settlement):""}</span>
                    <label htmlFor={`qty-${i}`}>有符号持仓（手）</label><input id={`qty-${i}`} inputMode="text" aria-label={`${l.contract_id} 有符号持仓`} value={positions[l.source_symbol]??""} data-symbol={l.source_symbol} onChange={e=>edit(()=>setPositions(p=>({...p,[l.source_symbol]:e.target.value})))}/>
                  </div>)}
                  <p className="hint">正数为买入，负数为卖出。每腿非零整数，最多 1000 手。</p>
                </fieldset>
                <fieldset disabled={busy}><legend>情景控制</legend>
                  <label htmlFor="scenario">情景时间（北京时间）</label><input id="scenario" type="datetime-local" min={coverage.trade_date+"T15:00"} max={earliestExpiry?dateBefore(earliestExpiry)+"T23:59":undefined} value={scenario} onChange={e=>edit(()=>setScenario(e.target.value))}/>
                  <div className="small-actions"><button type="button" className="secondary" onClick={()=>edit(()=>setScenario(coverage.trade_date+"T15:00"))}>基准时点</button><button type="button" className="secondary" onClick={()=>edit(()=>setScenario(new Date(Date.parse(coverage.trade_date+"T00:00:00Z")+86400000).toISOString().slice(0,10)+"T15:00"))}>+1 自然日</button></div>
                  <label htmlFor="price-range">价格冲击半宽 (%)</label><input id="price-range" inputMode="decimal" value={priceRange} onChange={e=>edit(()=>setPriceRange(e.target.value))}/>
                  <label htmlFor="iv-range">IV 冲击半宽 (百分点)</label><input id="iv-range" inputMode="decimal" value={ivRange} onChange={e=>edit(()=>setIvRange(e.target.value))}/>
                  {catalog&&selectedMonths.length>0&&<details className="scenario-greeks"><summary>按交割月独立冲击（可选）</summary>{selectedMonths.map(l=><div className="month-shocks" key={l.underlying_id}><strong>{l.underlying_id} · {l.expiry_date}</strong><div className="picker-filters"><div><label htmlFor={"price-"+l.underlying_id}>价格 (%)</label><input id={"price-"+l.underlying_id} inputMode="decimal" placeholder="0" value={priceOverrides[l.underlying_id]??""} onChange={e=>edit(()=>setPriceOverrides(p=>({...p,[l.underlying_id]:e.target.value})))}/></div><div><label htmlFor={"iv-"+l.underlying_id}>IV (pp)</label><input id={"iv-"+l.underlying_id} inputMode="decimal" placeholder="0" value={ivOverrides[expiryKey(l.underlying_id,l.expiry_date)]??""} onChange={e=>edit(()=>setIvOverrides(p=>({...p,[expiryKey(l.underlying_id,l.expiry_date)]:e.target.value})))}/></div></div></div>)}<p className="hint">矩阵冲击叠加在独立情景上。零格代表已配置情景。</p></details>}
                  <p className="hint">9 点价格曲线 · 5 × 5 风险矩阵。零冲击始终包含在轴中。</p>
                </fieldset>
                <button className="primary calculate" type="submit" disabled={busy||selectedLegs.length<2}>{state==="calculating"?<><span className="spinner"/>计算中</>:<><Icon name="refresh"/>重新估值</>}</button>
                {state==="calculating"&&<button className="secondary cancel" type="button" onClick={()=>{cancel();setState("dirty");setMessage("已取消等待。服务端可能仍在计算，请稍后手动重试。");}}>取消等待</button>}
              </form>
            </section>
            <section className="panel guard-card"><SectionTitle title="准入边界"/><div className="guard-row"><span>数据切片</span><strong>{catalog?catalog.available_dates.length+" 日 / "+catalog.approved_contract_dates+" 条":"1 日 / "+coverage.admitted_legs+" 合约"}</strong></div><div className="guard-row"><span>官方档案</span><strong>{coverage.source_versions.artifacts.length} 份</strong></div><div className="guard-row"><span>数据包</span><span className="mono" title={coverage.source_versions.packet_sha256}>{shortHash(coverage.source_versions.packet_sha256)}</span></div><div className="guard-row"><span>生产估值</span><strong className="amber">NO-GO</strong></div><p className="hint">历史归档数据不代表当前行情。仅供研究，不产生真实持仓或可执行交易信号。</p></section>
          </aside>
          <section className="results">
            <div className={`status-line ${error?"status-error":""}`} role={error?"alert":"status"} aria-live="polite"><span className={`status-icon ${state==="complete"?"done":""}`}>{state==="calculating"?<span className="spinner"/>:state==="complete"?<Icon name="check"/>:<span className="status-dot"/>}</span><span>{message}</span>{state==="complete"&&<span className="status-end">已计算</span>}</div>
            {!result||!g||!exposure?<div className="panel empty"><div className="empty-icon"><Icon name="chart"/></div><h2>{state==="calculating"?"正在计算真实历史样本":"等待组合估值"}</h2><p>{state==="calculating"?"当前结果尚不可用，计算完成后自动显示。":"确认左侧假设持仓和情景参数，执行估值后显示 Greeks、曲线和风险矩阵。不会展示过期或模拟结果。"}</p></div>:<>
              <div className="metrics">
                <Metric label="组合基准价值" value={"¥"+money(g.baseline_value)} sub="有符号净理论价值" testId="baseline-value"/>
                <Metric label="情景价值变化" value={"¥"+signed(g.configured_value_change,2)} sub={catalog?"已配置冲击 · 指定时点":"固定 IV · 指定情景时点"} kind={tone(g.configured_value_change)} testId="configured-change"/>
                <Metric label={resultMonths.length>1?"标的交割月":"标的结算价"} value={resultMonths.length>1?resultMonths.length+" 个":format(result.legs[0]!.market_underlying_price,0)} sub={resultMonths.length>1?resultMonths.map(l=>l.underlying_id.replace("SHFE.","").toUpperCase()+" "+format(l.market_underlying_price)).join(" / ")+" 元/吨":"元/吨 · 同一交割月"}/>
                <Metric label="基准剩余期限" value={maturityLabel} sub="ACT/365F · 名义时钟"/>
              </div>
              {result.underlying_exposures.map((exposure,index)=><section key={exposure.underlying_id} className="panel exposures" data-testid="underlying-exposure"><SectionTitle title="组合风险暴露" sub={exposure.underlying_id+" · 基准 Greeks"}/>
                <div className="greek-grid">
                  <div><span>Delta</span><strong data-testid={index===0?"delta":"delta-"+exposure.underlying_id}>{signed(exposure.baseline_greeks.delta,4)}</strong><small>元 / 价格单位</small></div>
                  <div><span>Gamma</span><strong>{format(exposure.baseline_greeks.gamma,7)}</strong><small>元 / 价格单位²</small></div>
                  <div><span>每日 Theta</span><strong className={tone(exposure.baseline_greeks.theta_annual)}>{signed(exposure.baseline_greeks.theta_annual/365,2)}</strong><small>元 / 自然日（年化÷365）</small></div>
                  <div><span>Vega / 1 pp</span><strong className={tone(exposure.baseline_greeks.vega_per_unit)}>{signed(exposure.baseline_greeks.vega_per_unit/100,2)}</strong><small>元 / 波动率百分点</small></div>
                </div>
                <details className="scenario-greeks"><summary>指定情景时点 Greeks</summary><div className="greek-grid small"><div><span>Delta</span><strong>{signed(exposure.scenario_greeks.delta,4)}</strong></div><div><span>Gamma</span><strong>{format(exposure.scenario_greeks.gamma,7)}</strong></div><div><span>每日 Theta</span><strong>{signed(exposure.scenario_greeks.theta_annual/365,2)}</strong></div><div><span>Vega / pp</span><strong>{signed(exposure.scenario_greeks.vega_per_unit/100,2)}</strong></div></div></details>
              </section>)}
              <LegTable value={result}/>
              <div className="chart-grid"><Curve points={g.price_curve} select={setSelected}/><Heatmap value={result} selected={selected} select={setSelected}/></div>
              <CellDetail point={selected}/>
              <Evidence value={result}/>
            </>}
          </section>
        </div>
      </>}
      <footer>OptionMaster · {legacyMode?"M1-D07":catalogMode?"M1-D09":"M1-D08"} <span>已批准历史数据研究 · 非生产 · 所有估值为理论值</span></footer>
    </main>
  </div>;
}
createRoot(document.getElementById("root")!).render(<App/>);
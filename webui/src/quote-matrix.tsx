import {useLayoutEffect,useRef,type ReactNode} from "react";
import type { OptionQuoteDTO,StrategyChainDTO } from "./generated/strategy-types";
import { quoteColumns,putColumns,historicalColumns,historicalPutColumns,formatQuoteField,type QuoteColumn,type QuoteDetails } from "./quote-details";
import { alignedTop,extent,mirrorLeft,outwardFraction,clampUnit,type QuoteSide } from "./quote-scroll";

type Props={chain:StrategyChainDTO;details:QuoteDetails|null;atm:number|null;precision:number;renderQuantity:(q:OptionQuoteDTO)=>ReactNode};
const TRACK=1000;
const colWidth=([key,,style]:QuoteColumn,precision:number)=>key==="symbol"?156:key==="net_pos"?108:style==="greeks"?Math.max(82,54+precision*7):key.endsWith("_price")?82:66;
const xMax=(el:HTMLElement)=>extent(el.scrollWidth,el.clientWidth);
const yMax=(el:HTMLElement)=>extent(el.scrollHeight,el.clientHeight);
export function QuoteMatrix({chain,details,atm,precision,renderQuantity}:Props){
  const left=useRef<HTMLDivElement>(null),right=useRef<HTMLDivElement>(null),center=useRef<HTMLDivElement>(null);
  const leftBar=useRef<HTMLInputElement>(null),rightBar=useRef<HTMLInputElement>(null);
  const scroll=useRef({outward:0,top:0});
  // Native horizontal scroll, trackpad and persistent sliders share one
  // outward coordinate. Ignore programmatic feedback at its target.
  function sync(){
    const l=left.current,r=right.current,c=center.current;
    if(!l||!r||!c)return;
    const p=scroll.current;
    const height=Math.min(l.clientHeight,r.clientHeight);
    if(height>0&&c.style.height!==height+"px")c.style.height=height+"px";
    const lx=xMax(l),rx=xMax(r);
    if(lx===0&&rx===0)p.outward=0;
    const a=mirrorLeft("CALL",p.outward,lx),b=mirrorLeft("PUT",p.outward,rx);
    p.top=alignedTop(p.top,yMax(l),yMax(r),yMax(c));
    if(Math.abs(l.scrollLeft-a)>.5)l.scrollLeft=a;
    if(Math.abs(r.scrollLeft-b)>.5)r.scrollLeft=b;
    for(const pane of [l,c,r])if(Math.abs(pane.scrollTop-p.top)>.5)pane.scrollTop=p.top;
    if(leftBar.current){leftBar.current.value=String(Math.round((1-p.outward)*TRACK));leftBar.current.disabled=lx===0&&rx===0;}
    if(rightBar.current){rightBar.current.value=String(Math.round(p.outward*TRACK));rightBar.current.disabled=lx===0&&rx===0;}
  }
  function onScroll(side:QuoteSide|"CENTER",el:HTMLDivElement){
    const p=scroll.current;
    if(side!=="CENTER"){
      const max=xMax(el);
      if(max>0&&Math.abs(el.scrollLeft-mirrorLeft(side,p.outward,max))>1)p.outward=outwardFraction(side,el.scrollLeft,max);
    }
    if(Math.abs(el.scrollTop-p.top)>1)p.top=el.scrollTop;
    sync();
  }
  function move(side:QuoteSide,value:number){
    scroll.current.outward=clampUnit(side==="CALL"?1-value/TRACK:value/TRACK);sync();
  }
  useLayoutEffect(()=>{
    // Default to the inner columns, and keep equal row alignment on resize.
    sync();
    const observer=new ResizeObserver(sync);
    for(const pane of [left.current,center.current,right.current]){
      if(pane){observer.observe(pane);const table=pane.querySelector("table");if(table)observer.observe(table);}
    }
    return ()=>observer.disconnect();
  },[]);
  const historical=chain.snapshot.source_versions.LOCAL_USER_ASSUMPTION!==undefined;
  const byId=new Map(details?.quotes.map(q=>[q.contract_id,q]));
  const side=(q:OptionQuoteDTO|null,columns:readonly QuoteColumn[],right:string)=>columns.map(([key,label,style])=>{
    const cell=right+":"+key;
    if(!q)return <td key={cell} data-field={cell} className={"qt-cell qt-"+style+" sb-missing"} title="合约缺失">{key==="symbol"?"缺失":"—"}</td>;
    if(key==="symbol")return <td key={cell} data-field={cell} className="qt-cell qt-symbol" title={q.contract_id+" · "+chain.snapshot.price_basis+" 标记价 "+(q.market_price??"—")+" · IV "+(q.implied_volatility===null?"不可用":(q.implied_volatility*100).toFixed(2)+"%")}>{q.contract_id}</td>;
    if(key==="net_pos")return <td key={cell} data-field={cell} className="qt-cell qt-pos" title="研究持仓，非实盘账户">{renderQuantity(q)}</td>;
    const field=byId.get(q.contract_id)?.[key],value=formatQuoteField(key,field,precision);
    return <td key={cell} data-field={cell} className={"qt-cell qt-"+style+(field?.value==null?" qt-unavailable":"")} title={label+" "+value+" · "+(field?.value!=null?field.basis:field?.reason??"报价详情未准入")}>{value}</td>;
  });
  const table=(columns:readonly QuoteColumn[],right:QuoteSide)=><table className="qt-table qt-side-table" aria-label={right+" 报价"} style={{width:Math.max(0,columns.reduce((sum,col)=>sum+colWidth(col,precision),0))}}>
    <colgroup>{columns.map(col=><col key={col[0]} style={{width:colWidth(col,precision)}}/>)}</colgroup>
    <thead><tr>{columns.map(([key,label,style])=><th key={right+":"+key} scope="col" data-field={right+":"+key} className={"qt-"+style}>{label}</th>)}</tr></thead>
    <tbody>{chain.rows.map(r=><tr key={r.strike} data-strike={r.strike} className={r.strike===atm?"sb-atm":""}>{side(right==="CALL"?r.call:r.put,columns,right)}</tr>)}</tbody>
  </table>;
  return <div className="qt-mirror" role="region" aria-label={historical?"23列历史T型报价，左右镜像滚动":"29列T型报价，左右镜像滚动"}>
    <div className="qt-mirror-grid qt-group-labels"><strong>CALL 看涨</strong><strong>行权价</strong><strong>PUT 看跌</strong></div>
    <div className="qt-mirror-grid qt-mirror-panes">
      <div className="qt-pane" data-pane="CALL" ref={left} tabIndex={0} role="region" aria-label="CALL 报价滚动区" onScroll={e=>onScroll("CALL",e.currentTarget)}>{table(historical?historicalColumns:quoteColumns,"CALL")}</div>
      <div className="qt-pane qt-center" data-pane="CENTER" ref={center} tabIndex={0} role="region" aria-label="固定行权价" onScroll={e=>onScroll("CENTER",e.currentTarget)}>
        <table className="qt-table qt-center-table" aria-label="行权价"><thead><tr><th scope="col" data-field="CENTER:strike" className="qt-strike">行权价</th></tr></thead>
        <tbody>{chain.rows.map(r=><tr key={r.strike} data-strike={r.strike} className={r.strike===atm?"sb-atm":""}><th scope="row" data-field="CENTER:strike" className="qt-strike">{r.strike}{r.strike===atm&&<small>ATM</small>}</th></tr>)}</tbody></table>
      </div>
      <div className="qt-pane" data-pane="PUT" ref={right} tabIndex={0} role="region" aria-label="PUT 报价滚动区" onScroll={e=>onScroll("PUT",e.currentTarget)}>{table(historical?historicalPutColumns:putColumns,"PUT")}</div>
    </div>
    <div className="qt-mirror-grid qt-scroll-controls">
      <input ref={leftBar} type="range" min={0} max={TRACK} step={1} defaultValue={TRACK} aria-label="CALL 水平滚动" onChange={e=>move("CALL",Number(e.target.value))}/>
      <button type="button" onClick={()=>move("PUT",0)} title="回到靠近行权价的列">居中</button>
      <input ref={rightBar} type="range" min={0} max={TRACK} step={1} defaultValue={0} aria-label="PUT 水平滚动" onChange={e=>move("PUT",Number(e.target.value))}/>
    </div>
  </div>;
}

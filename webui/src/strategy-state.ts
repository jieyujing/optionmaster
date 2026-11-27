// D12 portfolio draft: exact positions, entry lineage and snapshot revisions.
import { STRATEGY_LIMITS, type OptionQuoteDTO, type UnderlyingQuoteDTO, type SnapshotDTO,
  type StrategyChainDTO, type StrategyOptionDTO, type StrategyUnderlyingDTO, type StrategyPayoffRequestDTO,
  type CoverageMonthDTO, type CoverageDayDTO, type CoverageProductDTO } from "./generated/strategy-types";
import { validateStrategyPayoffRequest } from "./generated/strategy-validators.mjs";

type OptionInstrument={kind:"OPTION";snapshot:SnapshotDTO;option:OptionQuoteDTO;underlying:UnderlyingQuoteDTO};
type UnderlyingInstrument={kind:"UNDERLYING";snapshot:SnapshotDTO;underlying:UnderlyingQuoteDTO};
export type StrategyInstrument=OptionInstrument|UnderlyingInstrument;
type OptionPosition={kind:"OPTION";entry:StrategyOptionDTO;original:OptionInstrument;current:OptionInstrument|null;unavailable:string|null};
type UnderlyingPosition={kind:"UNDERLYING";entry:StrategyUnderlyingDTO;original:UnderlyingInstrument;current:UnderlyingInstrument|null;unavailable:string|null};
export type StrategyPosition=OptionPosition|UnderlyingPosition;
export type PortfolioState={snapshot:SnapshotDTO|null;positions:StrategyPosition[];drafts:Record<string,string>;errors:Record<string,string>;revision:number};
export const emptyPortfolio=(revision=0):PortfolioState=>({snapshot:null,positions:[],drafts:{},errors:{},revision});
export const setSnapshot=(state:PortfolioState,snapshot:SnapshotDTO):PortfolioState=>({...emptyPortfolio(state.revision+1),snapshot});
export const instrumentForOption=(chain:StrategyChainDTO,option:OptionQuoteDTO):OptionInstrument=>
  ({kind:"OPTION",snapshot:chain.snapshot,option,underlying:chain.underlying});
export const instrumentForUnderlying=(chain:StrategyChainDTO):UnderlyingInstrument=>
  ({kind:"UNDERLYING",snapshot:chain.snapshot,underlying:chain.underlying});
export const instrumentKey=(instrument:StrategyInstrument)=>instrument.kind==="OPTION"?"O:"+instrument.option.contract_id:"U:"+instrument.underlying.underlying_id;
export const positionKey=(position:StrategyPosition)=>instrumentKey(position.original);
export const optionCount=(state:PortfolioState)=>state.positions.filter(p=>p.kind==="OPTION").length;
export const positionFor=(state:PortfolioState,key:string)=>state.positions.find(p=>positionKey(p)===key);
export const quantityText=(state:PortfolioState,key:string)=>state.drafts["qty:"+key]??String(positionFor(state,key)?.entry.quantity??0);
export const entryText=(state:PortfolioState,key:string)=>state.drafts["entry:"+key]??String(positionFor(state,key)?.entry.entry_price??"");
// Coverage is not a complete-day certificate. Independently eligible legs in
// a partially observed month remain selectable; discovery-only rows do not.
export function coverageMonthUsable(m:CoverageMonthDTO):boolean {
  return m.admitted_contracts>0 && (
    (m.status==="ADMITTED" && m.admitted_contracts===m.discovered_contracts) ||
    (m.status==="INCOMPLETE" && m.admitted_contracts<m.discovered_contracts));
}
export const coverageDayUsable=(d:CoverageDayDTO)=>d.status!=="UNAVAILABLE"&&d.status!=="NO_SESSION"&&d.months.some(coverageMonthUsable);
export const coverageChainKey=(day:string,underlyingId:string)=>JSON.stringify([day,underlyingId]);
/** A chain is identified by both date and actual underlying contract. */
export function coverageChainChoices(product:CoverageProductDTO){
  return product.days.filter(coverageDayUsable)
    .sort((a,b)=>b.trade_date.localeCompare(a.trade_date))
    .flatMap(day=>day.months.filter(coverageMonthUsable)
      .map(month=>({day,month,key:coverageChainKey(day.trade_date,month.underlying_id)})));
}
export function preferredCoverageDay(p:CoverageProductDTO):CoverageDayDTO|undefined {
  return p.days.find(d=>d.trade_date===p.latest_complete_trade_date&&coverageDayUsable(d))??
    p.days.filter(coverageDayUsable).sort((a,b)=>b.trade_date.localeCompare(a.trade_date))[0];
}
export function canSelect(instrument:StrategyInstrument):boolean{
  const u=instrument.underlying;
  return u.eligibility.status==="ELIGIBLE"&&u.market_price!==null&&(
    instrument.kind==="OPTION"
      ?instrument.option.underlying_id===u.underlying_id&&instrument.option.payoff_eligibility.status==="ELIGIBLE"&&instrument.option.market_price!==null
      :u.domain.status==="VERIFIED"&&inDomain(u,u.market_price));
}
function inDomain(u:UnderlyingQuoteDTO,value:number):boolean{
  const d=u.domain;
  return Number.isFinite(value)&&d.status==="VERIFIED"&&
    (d.min_price===null||value>d.min_price||(value===d.min_price&&d.min_inclusive))&&
    (d.max_price===null||value<d.max_price||(value===d.max_price&&d.max_inclusive));
}
function changedField(state:PortfolioState,field:string,raw:string,error:string|null,positions=state.positions):PortfolioState{
  const drafts={...state.drafts,[field]:raw};
  const errors={...state.errors};
  if(error)errors[field]=error;else delete errors[field];
  return {...state,drafts,errors,positions,revision:state.revision+1};
}
function replacePosition(state:PortfolioState,key:string,position:StrategyPosition):StrategyPosition[]{
  const found=state.positions.some(p=>positionKey(p)===key);
  return found?state.positions.map(p=>positionKey(p)===key?position:p):[...state.positions,position];
}
export function setQuantity(state:PortfolioState,instrument:StrategyInstrument,raw:string):PortfolioState{
  const key=instrumentKey(instrument),field="qty:"+key,existing=positionFor(state,key);
  const qty=/^[+-]?\d+$/.test(raw)?Number(raw):NaN;
  if(!Number.isSafeInteger(qty)||Math.abs(qty)>STRATEGY_LIMITS.maxQuantity)
    return changedField(state,field,raw,"请输入 ±1–1000 的整数；0 移除");
  if(qty===0){
    const drafts={...state.drafts},errors={...state.errors};
    for(const f of ["qty:"+key,"entry:"+key]){delete drafts[f];delete errors[f];}
    return {...state,drafts,errors,positions:state.positions.filter(p=>positionKey(p)!==key),revision:state.revision+1};
  }
  if(existing){
    const position:StrategyPosition=existing.kind==="OPTION"
      ?{...existing,entry:{...existing.entry,quantity:qty}}
      :{...existing,entry:{...existing.entry,quantity:qty}};
    return changedField(state,field,raw,null,replacePosition(state,key,position));
  }
  if(!state.snapshot||instrument.snapshot.snapshot_id!==state.snapshot.snapshot_id||!canSelect(instrument))
    return changedField(state,field,raw,"合约未准入或快照已变化");
  if(instrument.kind==="OPTION"&&optionCount(state)>=STRATEGY_LIMITS.maxOptions)
    return changedField(state,field,raw,"最多 8 个期权腿");
  if(instrument.kind==="UNDERLYING"&&!state.positions.some(p=>p.kind==="OPTION"&&p.original.underlying.underlying_id===instrument.underlying.underlying_id))
    return changedField(state,field,raw,"必须先选择对应期权");
  if(instrument.kind==="UNDERLYING"&&state.positions.filter(p=>p.kind==="UNDERLYING").length>=STRATEGY_LIMITS.maxUnderlyings)
    return changedField(state,field,raw,"标的腿数量超限");
  const lineage={quantity:qty,entry_trade_date:state.snapshot.trade_date,entry_snapshot_id:state.snapshot.snapshot_id,entry_origin:"ADMITTED_MARK" as const};
  const position:StrategyPosition=instrument.kind==="OPTION"
    ?{kind:"OPTION",original:instrument,current:instrument,unavailable:null,
      entry:{...lineage,contract_id:instrument.option.contract_id,entry_price:instrument.option.market_price!}}
    :{kind:"UNDERLYING",original:instrument,current:instrument,unavailable:null,
      entry:{...lineage,underlying_id:instrument.underlying.underlying_id,entry_price:instrument.underlying.market_price!,quantity_unit:instrument.underlying.quantity_unit}};
  return changedField(state,field,raw,null,[...state.positions,position]);
}
export function setEntryPrice(state:PortfolioState,key:string,raw:string):PortfolioState{
  const field="entry:"+key,position=positionFor(state,key);
  if(!position)return state;
  const price=/^-?(?:\d+(?:\.\d*)?|\.\d+)$/.test(raw)?Number(raw):NaN;
  if(!Number.isFinite(price)||(position.kind==="OPTION"?price<0:!inDomain(position.original.underlying,price)))
    return changedField(state,field,raw,"建仓价必须是有效的已验证价格域数值");
  const next:StrategyPosition=position.kind==="OPTION"
    ?{...position,entry:{...position.entry,entry_price:price,entry_origin:"USER_EDITED"}}
    :{...position,entry:{...position.entry,entry_price:price,entry_origin:"USER_EDITED"}};
  return changedField(state,field,raw,null,replacePosition(state,key,next));
}
function optionTerms(q:OptionQuoteDTO,localModelOverride=false):string{
  return JSON.stringify([q.contract_id,q.underlying_id,q.option_type,q.strike,q.expiry_id,q.expiry_date,q.expiry_at,
    q.expiry_clock,localModelOverride?null:q.exercise_style,localModelOverride?null:q.model,q.multiplier,q.quote_unit,q.terms_version]);
}
function underlyingTerms(q:UnderlyingQuoteDTO):string{
  return JSON.stringify([q.underlying_id,q.kind,q.delivery_month,q.multiplier,q.quantity_unit,q.quote_unit,q.terms_version]);
}
export function revaluePortfolio(state:PortfolioState,snapshot:SnapshotDTO,chains:StrategyChainDTO[]):PortfolioState{
  if(!state.snapshot||state.snapshot.exchange!==snapshot.exchange||state.snapshot.product!==snapshot.product||state.snapshot.provider!==snapshot.provider)
    throw Error("SCOPE_UNAVAILABLE");
  if(chains.some(c=>c.snapshot.snapshot_id!==snapshot.snapshot_id||c.snapshot.trade_date!==snapshot.trade_date))
    throw Error("SNAPSHOT_STALE");
  const localModelOverride=state.snapshot.source_pin===snapshot.source_pin&&!!state.snapshot.source_versions.LOCAL_USER_ASSUMPTION&&!!snapshot.source_versions.LOCAL_USER_ASSUMPTION;
  const positions=state.positions.map((position):StrategyPosition=>{
    const chain=position.kind==="OPTION"
      ?chains.find(c=>c.underlying.underlying_id===position.original.underlying.underlying_id&&c.rows.some(r=>r.call?.contract_id===position.entry.contract_id||r.put?.contract_id===position.entry.contract_id))
      :chains.find(c=>c.underlying.underlying_id===position.original.underlying.underlying_id);
    const entryReason=position.entry.entry_trade_date>snapshot.trade_date?"ENTRY_DATE_INVALID":null;
    const underlyingChanged=chain&&underlyingTerms(chain.underlying)!==underlyingTerms(position.original.underlying);
    if(position.kind==="OPTION"){
      const quote=chain?.rows.flatMap(r=>[r.call,r.put]).find(q=>q?.contract_id===position.entry.contract_id);
      const instrument=chain&&quote?instrumentForOption(chain,quote):null;
      const reason=entryReason??(!instrument?"INSTRUMENT_UNAVAILABLE":
        underlyingChanged||optionTerms(instrument.option,localModelOverride&&instrument.option.terms_version.startsWith("LOCAL_CACHE:"))!==optionTerms(position.original.option,localModelOverride&&position.original.option.terms_version.startsWith("LOCAL_CACHE:"))?"TERMS_UNAVAILABLE":
        snapshot.trade_date>=instrument.option.expiry_date?"EXPIRED_SCENARIO":!canSelect(instrument)?"MARK_UNAVAILABLE":null);
      return {...position,current:reason?null:instrument,unavailable:reason};
    }
    const instrument=chain?instrumentForUnderlying(chain):null;
    const reason=entryReason??(!instrument?"INSTRUMENT_UNAVAILABLE":underlyingChanged?"TERMS_UNAVAILABLE":
      !canSelect(instrument)?"MARK_UNAVAILABLE":null);
    return {...position,current:reason?null:instrument,unavailable:reason};
  });
  return {...state,snapshot,positions,revision:state.revision+1};
}
export function buildPayoffRequest(state:PortfolioState,axis:number[]=[-.1,-.05,0,.05,.1],global=0,
  overrides:Record<string,number>={},reference?:string):StrategyPayoffRequestDTO|null{
  if(!state.snapshot||!optionCount(state)||Object.keys(state.errors).length||
    state.positions.some(p=>p.unavailable!==null||!p.current||p.current.snapshot.snapshot_id!==state.snapshot!.snapshot_id))return null;
  const options=state.positions.filter((p):p is OptionPosition=>p.kind==="OPTION");
  const underlyingIds=new Set(options.map(p=>p.original.underlying.underlying_id));
  const underlyings=state.positions.filter((p):p is UnderlyingPosition=>p.kind==="UNDERLYING");
  if(underlyings.some(p=>!underlyingIds.has(p.entry.underlying_id)))return null;
  const referenceId=reference??options[0]?.original.underlying.underlying_id;
  if(!referenceId||!underlyingIds.has(referenceId)||Object.keys(overrides).some(k=>!underlyingIds.has(k)))return null;
  const req:StrategyPayoffRequestDTO={schema_version:"4.0",snapshot_id:state.snapshot.snapshot_id,
    portfolio_revision:state.revision,options:options.map(p=>p.entry),underlyings:underlyings.map(p=>p.entry),
    reference_underlying_id:referenceId,curve_price_returns:axis,global_price_shock:global,price_shocks_by_underlying:overrides};
  return validateStrategyPayoffRequest(req)?req:null;
}

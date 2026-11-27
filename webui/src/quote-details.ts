import type { StrategyChainDTO } from "./generated/strategy-types";
import { requireResponse } from "./api";

export const quoteColumns = [
  ["symbol","代码","symbol"],["theo_vega","Vega","greeks"],["theo_theta","Theta","greeks"],
  ["theo_gamma","Gamma","greeks"],["theo_delta","Delta","greeks"],["open_interest","持仓量","normal"],
  ["volume","成交量","normal"],["bid_impv","买隐波","bid"],["bid_volume","买量","bid"],
  ["bid_price","买价","bid"],["ask_price","卖价","ask"],["ask_volume","卖量","ask"],
  ["ask_impv","卖隐波","ask"],["net_pos","净持仓","pos"],
] as const;
export const putColumns=[...quoteColumns].reverse();
export const historicalColumns=[
  ...quoteColumns.slice(0,7),
  ["model_iv","模型IV","greeks"],["settlement_price","结算价","normal"],["close_price","收盘价","normal"],["net_pos","净持仓","pos"],
] as const;
export const historicalPutColumns=[...historicalColumns].reverse();
export type HistoricalKey="model_iv"|"settlement_price"|"close_price";
export type QuoteColumn=typeof quoteColumns[number]|typeof historicalColumns[number];
export type DetailKey=Exclude<typeof quoteColumns[number][0],"symbol"|"net_pos">;
export type QuoteField={value:number|null;basis:"UNAVAILABLE"|"SETTLEMENT_MODEL"|"APPROVED_CLOSE_MODEL"|"ADMITTED_DAILY"|"ADMITTED_BOOK";reason:string|null};
export type QuoteDetail={contract_id:string}&Record<DetailKey,QuoteField>&Partial<Record<HistoricalKey,QuoteField>>;
export type QuoteDetails={schema_version:"1.0"|"1.1";snapshot:StrategyChainDTO["snapshot"];underlying_id:string;quotes:QuoteDetail[]};
const keys=quoteColumns.map(c=>c[0]).filter((k):k is DetailKey=>k!=="symbol"&&k!=="net_pos");
function object(v:unknown):Record<string,unknown>{
  requireResponse(v!==null&&typeof v==="object"&&!Array.isArray(v));return v as Record<string,unknown>;
}
function exactKeys(v:Record<string,unknown>,ks:readonly string[]){requireResponse(Object.keys(v).length===ks.length&&ks.every(k=>Object.hasOwn(v,k)));}
export function decodeQuoteDetails(raw:unknown,chain:StrategyChainDTO,historical=false):QuoteDetails{
  const d=object(raw);exactKeys(d,["schema_version","snapshot","underlying_id","quotes"]);
  requireResponse(d.schema_version===(historical?"1.1":"1.0")&&JSON.stringify(d.snapshot)===JSON.stringify(chain.snapshot)&&d.underlying_id===chain.underlying.underlying_id&&Array.isArray(d.quotes)&&d.quotes.length<=128);
  const expected=new Set(chain.rows.flatMap(r=>[r.call,r.put].flatMap(q=>q?[q.contract_id]:[])));
  requireResponse(d.quotes.length===expected.size);
  const fieldKeys:(DetailKey|HistoricalKey)[]=historical?[...keys,"close_price","settlement_price","model_iv"]:keys;
  for(const rawQuote of d.quotes){
    const q=object(rawQuote);exactKeys(q,["contract_id",...fieldKeys]);
    requireResponse(typeof q.contract_id==="string"&&expected.delete(q.contract_id));
    for(const key of fieldKeys){
      const f=object(q[key]);exactKeys(f,["value","basis","reason"]);
      if(f.value===null){requireResponse(f.basis==="UNAVAILABLE"&&typeof f.reason==="string"&&f.reason.length>0&&f.reason.length<=64);continue;}
      requireResponse(typeof f.value==="number"&&Number.isFinite(f.value)&&f.reason===null);
      const greek=key.startsWith("theo_"),model=greek||key==="model_iv";
      const daily=key==="open_interest"||key==="volume"||key==="close_price"||key==="settlement_price";
      requireResponse(f.basis===(model?(chain.snapshot.price_basis==="SETTLEMENT"?"SETTLEMENT_MODEL":"APPROVED_CLOSE_MODEL"):daily?"ADMITTED_DAILY":"ADMITTED_BOOK")&&(greek||f.value>=0)&&(key!=="model_iv"||f.value>0));
    }
  }
  requireResponse(expected.size===0);
  return raw as QuoteDetails;
}
export function formatQuoteField(key:DetailKey|HistoricalKey,field:QuoteField|undefined,precision:number):string{
  if(!field||field.value===null||!Number.isFinite(field.value))return "—";
  if(key==="bid_impv"||key==="ask_impv"||key==="model_iv")return (field.value*100).toFixed(2)+"%";
  if(key==="bid_price"||key==="ask_price"||key==="close_price"||key==="settlement_price")return field.value.toFixed(4);
  if(key.startsWith("theo_"))return field.value.toFixed(Math.min(10,Math.max(0,Math.trunc(precision))));
  return String(field.value);
}

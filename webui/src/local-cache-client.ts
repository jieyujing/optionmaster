import {fetchJson,requireResponse} from "./api";
import {createStrategyClient,strategyEnvelope,type LocalModelInfo,type StrategyCalculationClient} from "./strategy-api";
import type {StrategyChainDTO} from "./generated/strategy-types";
import {decodeQuoteDetails,type QuoteDetails} from "./quote-details";

const path="/api/local-cache/";
const chainKey=(c:StrategyChainDTO)=>c.rows.flatMap(r=>[r.call,r.put]).find(q=>q)?.expiry_id??"";
function modelInfo(raw:unknown,chainId:string):LocalModelInfo{
  requireResponse(raw!==null&&typeof raw==="object");
  const v=raw as LocalModelInfo;
  const allowed=new Set(["AMERICAN_BINOMIAL","BLACK_76","BLACK_SCHOLES"]);
  requireResponse(v.chain_id===chainId&&Array.isArray(v.raw_styles)&&v.raw_styles.every(s=>["UNKNOWN","AMERICAN","EUROPEAN"].includes(s))&&Array.isArray(v.models)&&v.models.length>=1&&v.models.length<=2&&new Set(v.models).size===v.models.length&&v.models.every(m=>allowed.has(m))&&v.models.includes(v.effective_model)&&v.models.includes(v.default_model)&&v.rate===0.02&&typeof v.user_override==="boolean");
  requireResponse(v.model_policy==="CFFEX_EUROPEAN_BLACK76"
    ?v.models.length===1&&v.effective_model==="BLACK_76"&&v.default_model==="BLACK_76"&&!v.user_override
    :v.model_policy==="LOCAL_CHAIN_MODEL"&&v.models.length===2);
  requireResponse(v.pricing_basis==="CHAIN_ADJUSTED_DAILY_MARK"&&
    [v.atm_strike,v.pricing_underlying,v.underlying_adjustment].every(x=>x===null||(typeof x==="number"&&Number.isFinite(x)))&&
    (v.pricing_underlying===null)===(v.underlying_adjustment===null)&&
    (v.pricing_underlying===null||(v.pricing_underlying>0&&v.atm_strike!==null)));
  return v;
}
export function createLocalCacheClient():StrategyCalculationClient{
  const token="local-preview";
  const base=createStrategyClient(token);
  const request=(route:string,signal:AbortSignal,body?:object)=>fetchJson(path+route,token,signal,body,strategyEnvelope);
  return {...base,chainKey,
    async chain(selector,signal){
      const params=new URLSearchParams();
      for(const [k,v] of Object.entries(selector))if(v!==undefined)params.set(k,v);
      const e=strategyEnvelope(await request("chain?"+params,signal)),c=e.chain;
      requireResponse(e.status==="OK"&&c!==null&&e.snapshot_id===c.snapshot.snapshot_id&&c.snapshot.source_versions.LOCAL_USER_ASSUMPTION!==undefined&&chainKey(c)===selector.underlying_id);
      requireResponse(c.rows.every(r=>[r.call,r.put].every(q=>q===null||q.expiry_id===selector.underlying_id)));
      return c;
    },
    async quoteDetails(chain,signal){
      const s=chain.snapshot,cid=chainKey(chain),quotes:QuoteDetails["quotes"]=[];
      requireResponse(cid.length>0);
      for(let offset=0;offset<chain.rows.length;offset+=32){
        const params=new URLSearchParams({exchange:s.exchange,product:s.product,trade_date:s.trade_date,chain_id:cid,snapshot_id:s.snapshot_id,offset:String(offset)});
        const page=decodeQuoteDetails(await request("details?"+params,signal),{...chain,rows:chain.rows.slice(offset,offset+32)},true);
        quotes.push(...page.quotes);
      }
      return {schema_version:"1.1",snapshot:s,underlying_id:chain.underlying.underlying_id,quotes};
    },
    async modelInfo(cid,signal){return modelInfo(await request("model?"+new URLSearchParams({chain_id:cid}),signal),cid);},
    async setModel(cid,model,signal){return modelInfo(await request("model",signal,{chain_id:cid,model}),cid);}
  };
}

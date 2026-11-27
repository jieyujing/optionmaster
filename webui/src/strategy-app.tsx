import {useMemo,useState} from "react";
import {createStrategyClient} from "./strategy-api";
import {createLocalCacheClient} from "./local-cache-client";
import {StrategyBuilder} from "./strategy-builder";
import {StrategyAnalysis} from "./strategy-analysis";
export function StrategyResearchApp({enableModel=true,localPreview=false,localCache=false}:{enableModel?:boolean;localPreview?:boolean;localCache?:boolean}){
 const [token,setToken]=useState(""),[draft,setDraft]=useState("");
 // This public marker is not a credential. Only the isolated loopback
 // preview adapter supplies the actual server-side credential.
 const requestToken=localPreview?"local-preview":token;
 const client=useMemo(()=>localPreview&&localCache?createLocalCacheClient():createStrategyClient(requestToken),[requestToken,localPreview,localCache]);
 if(!localPreview&&!token)return <form className="sa-auth" onSubmit={e=>{e.preventDefault();if(draft.trim()){setToken(draft.trim());setDraft("");}}}><h1>OptionMaster 研究 · v4</h1><p>仅已准入研究数据，不连接实盘。</p><label>研究访问令牌<input type="password" aria-label="研究访问令牌" autoComplete="off" value={draft} onChange={e=>setDraft(e.target.value)} required/></label><button type="submit">进入研究</button></form>;
 return <><div className="sa-toolbar"><span>{localCache?"本地行情缓存 / 用户授权模型假设 · 非生产估值":"归档 / 研究策略分析 · 非生产估值"}{localPreview?" · 本机预览":""}</span>{!localPreview&&<button onClick={()=>{setToken("");setDraft("");}}>断开会话</button>}</div>
 <StrategyBuilder client={client} analysis={(state,active,bumpRevision)=><StrategyAnalysis token={requestToken} state={state} active={active} bumpRevision={bumpRevision} client={client} enableModel={enableModel} onUnauthorized={()=>{setToken("");setDraft("");}}/>}/></>;
}

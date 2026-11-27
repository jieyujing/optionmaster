import assert from "node:assert/strict";
import {chromium} from "playwright-core";
const origin=process.env.OPTIONMASTER_TEST_ORIGIN,token=process.env.OPTIONMASTER_TEST_TOKEN;
assert.ok(origin?.startsWith("http://127.0.0.1:")&&token);
const browser=await chromium.launch({executablePath:process.env.OPTIONMASTER_BROWSER_EXECUTABLE,headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],checks=[];
page.on("pageerror",e=>errors.push(e.message));
const payPath="/api/v4/research/strategy/payoffs",valPath="/api/v4/research/strategy/valuations";
const chainPath="/api/v4/research/strategy/chain";
const responses=[],chains=[];
let delay=null;
await page.route("**/*",async route=>{
  const u=new URL(route.request().url());
  if(u.origin!==origin){await route.abort();return;}
  if(u.pathname===chainPath){
    try{const resp=await route.fetch({timeout:15000}),body=await resp.json();chains.push(body.chain);await route.fulfill({response:resp,body:JSON.stringify(body)});}catch{await route.abort().catch(()=>{});}
  }else if(u.pathname===payPath||u.pathname===valPath){
    try{
      const req=route.request().postDataJSON(),resp=await route.fetch({timeout:30000}),body=await resp.json();
      responses.push({path:u.pathname,req,body});
      if(delay&&delay.path===u.pathname&&!delay.hit){delay.hit=true;delay.mark();await delay.wait;}
      await route.fulfill({response:resp,body:JSON.stringify(body)});
    }catch(e){await route.abort().catch(()=>{});}
  }else await route.continue();
});
const wait=async(test,ms=20000)=>{const until=Date.now()+ms;while(Date.now()<until){if(await test())return;await new Promise(r=>setTimeout(r,30));}throw Error("Timed out: "+await page.locator("body").innerText().then(s=>s.slice(-1800)));};
const last=(path)=>responses.filter(r=>r.path===path).at(-1);
const result=()=>page.getByTestId("payoff-result");
const model=()=>page.getByTestId("model-result");
const row=(id)=>page.locator(".sb-chain").getByLabel(id+" 持仓数量",{exact:true});
const pos=(id)=>page.getByTestId("position-"+id);
const ready=()=>wait(async()=>await result().count()===1&&Number(await result().getAttribute("data-revision"))===last(payPath)?.req.portfolio_revision);
try{
 await page.goto(origin+"/strategy-preview");
 await page.getByLabel("研究访问令牌").fill(token);
 await page.getByRole("button",{name:"进入研究"}).click();
 await page.getByLabel("交易日").locator("option").filter({hasText:"2022-09-07"}).waitFor({state:"attached"});
 await page.getByLabel("交易日").selectOption("2022-09-07");await wait(()=>chains.some(c=>c?.snapshot.trade_date==="2022-09-07"));const c=chains.find(c=>c?.snapshot.trade_date==="2022-09-07");
 const put=c.rows.find(r=>r.strike===79000).put.contract_id,call=c.rows.find(r=>r.strike===61000)?.call?.contract_id??c.rows.find(r=>r.call?.model_eligibility.status==="NOT_EVALUATED").call.contract_id;
 await row(put).fill("1");await ready();
 assert.equal(last(payPath).body.payoff.accounting.net_premium,92100);
 assert.equal(await page.getByTestId("net-premium").innerText(),"92,100");
 assert.equal(last(payPath).body.payoff.terminal.points.find(p=>p.price_return===-.1).pnl,30290);
 assert.equal(last(payPath).body.payoff.terminal.points.find(p=>p.price_return===.1).pnl,-30290);
 assert.equal(await page.getByTestId("curve-TERMINAL").count(),1);
 checks.push("real D09 intrinsic-only option: exact payoff, cost and curve");
 await row(c.underlying.underlying_id).fill("1");await ready();
 assert.equal(last(payPath).body.payoff.terminal.points.find(p=>p.price_return===.1).pnl,0);
 await pos(put).getByLabel(put+" 建仓价").fill("0");await ready();
 assert.equal(last(payPath).body.payoff.accounting.net_premium,0);
 await pos(c.underlying.underlying_id).getByRole("button",{name:"移除 "+c.underlying.underlying_id}).click();await ready();
 await pos(put).getByRole("button",{name:"移除 "+put}).click();
 assert.equal(await result().count(),0);
 checks.push("mixed futures and editable zero cost; removal invalidates result");
 await row(call).fill("1");await ready();
 await wait(async()=>await model().count()===1);
 assert.equal(last(valPath).body.valuation.current.status,"OK");
 assert.equal(await page.getByTestId("curve-CURRENT").count(),1);
 assert.equal(await page.getByTestId("curve-SCENARIO").count(),1);
 assert.equal(await page.getByTestId("curve-TERMINAL").count(),1);
 await page.getByLabel("共同 IV 冲击").fill("3");
 await wait(async()=>last(valPath)?.req.global_iv_shock===.03&&await model().count()===1&&Number(await model().getAttribute("data-revision"))===last(valPath).req.portfolio_revision);
 checks.push("three curves and sticky-strike IV shock from real valuation");
 // Hold one actual response, then make A-B-A edits while it is in flight.
 let release,mark;const held=new Promise(r=>release=r),hit=new Promise(r=>mark=r);
 delay={path:payPath,wait:held,mark,hit:false};
 await row(call).fill("2");await hit;
 const oldRevision=last(payPath).req.portfolio_revision;
 await row(call).fill("3");await row(call).fill("2");
 assert.equal(await result().count(),0);
 release();await ready();
 assert.ok(last(payPath).req.portfolio_revision>oldRevision);
 assert.equal(last(payPath).req.options[0].quantity,2);
 delay=null;
 checks.push("delayed real response discarded on A-B-A revision");
 // Add a real far-month leg, keep independent underlying marks and expiry IV controls.
 await page.getByLabel("交割月").selectOption("SHFE.cu2212");
 await wait(()=>chains.some(c=>c?.underlying.underlying_id==="SHFE.cu2212"));const far=chains.find(c=>c?.underlying.underlying_id==="SHFE.cu2212");
 const farCall=far.rows.find(r=>r.strike===61000).call;
 await row(farCall.contract_id).fill("-1");await ready();
 await wait(async()=>await model().count()===1&&last(valPath).req.options.length===2);
 assert.equal(last(valPath).body.valuation.horizon.kind,"FIRST_EXPIRY_CONDITIONAL");
 assert.equal(last(valPath).body.valuation.horizon.status,"OK");
 assert.equal(last(valPath).body.valuation.horizon_risk.scope,"CONDITIONAL_RANGE");
 assert.equal(last(valPath).body.valuation.horizon_risk.roots_exhaustive,false);
 assert.equal(await page.getByTestId("curve-FIRST_EXPIRY_CONDITIONAL").count(),1);
 await page.getByText("独立价格 / IV 冲击",{exact:true}).click();
 await page.getByLabel("SHFE.cu2212 价格冲击").fill("2");
 await page.getByLabel(farCall.expiry_id+" IV 冲击").fill("4");
 await wait(async()=>last(valPath)?.req.iv_shocks_by_expiry[farCall.expiry_id]===.04&&await model().count()===1&&Number(await model().getAttribute("data-revision"))===last(valPath).req.portfolio_revision);
 const center=last(valPath).body.valuation.scenario.points.find(p=>p.price_return===0);
 assert.equal(center.underlying_prices["SHFE.cu2211"],60580);
 assert.ok(Math.abs(center.underlying_prices["SHFE.cu2212"]-60240*1.02)<1e-7);
 checks.push("actual cross-expiry conditional horizon and independent shocks");
 await page.getByText("高级分析 · 原版 v3 压力测试",{exact:true}).click();
 await page.getByRole("button",{name:"运行原版压力测试"}).waitFor();
 await wait(async()=>!(await page.getByRole("button",{name:"运行原版压力测试"}).isDisabled()));
 let legacyPost=null;
 page.on("request",r=>{if(r.url()===origin+"/api/v3/research/valuations"&&r.method()==="POST")legacyPost=r.postDataJSON();});
 await page.getByRole("button",{name:"运行原版压力测试"}).click();
 await wait(async()=>await page.getByTestId("advanced-result").count()===1,30000);
 assert.equal(Object.keys(legacyPost.positions).length,2);assert.equal(legacyPost.positions[call],2);assert.equal(legacyPost.positions[farCall.contract_id],-1);
 assert.equal(legacyPost.price_shocks_by_underlying["SHFE.cu2212"],.02);
 assert.equal(legacyPost.iv_shocks_by_expiry["SHFE|cu2212|"+farCall.expiry_date],.04);
 assert.equal(legacyPost.heatmap_price_shocks.length,5);assert.equal(legacyPost.heatmap_iv_shocks.length,5);
 assert.equal(await page.locator("[data-testid^=advanced-cell-]").count(),25);
 const before=legacyPost;
 await pos(farCall.contract_id).getByLabel(farCall.contract_id+" 建仓价").fill("0");
 assert.match(await page.getByTestId("advanced-status").innerText(),/自定义或跨日建仓成本/);
 assert.equal(await page.getByTestId("advanced-result").count(),0);
 assert.equal(await page.getByRole("button",{name:"运行原版压力测试"}).isDisabled(),true);
 assert.equal(legacyPost,before);
 checks.push("actual v3 advanced projection, 5x5 grid and custom cost refusal");
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.getByRole("button",{name:"断开会话"}).click();
 await page.getByLabel("研究访问令牌").waitFor();
 assert.equal(await result().count(),0);assert.deepEqual(errors,[]);
 checks.push("mobile containment and disconnect clears research state");
 console.log(JSON.stringify({status:"PASS",checks,responses:responses.length},null,2));
}finally{if(delay?.hit){}await browser.close();}

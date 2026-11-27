import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { chromium } from "playwright-core";

const origin=process.env.OPTIONMASTER_TEST_ORIGIN;
const token=process.env.OPTIONMASTER_TEST_TOKEN;
const executablePath=process.env.OPTIONMASTER_BROWSER_EXECUTABLE;
const evidence=process.env.OPTIONMASTER_BROWSER_EVIDENCE;
assert.match(origin??"",/^http:\/\/127\.0\.0\.1:\d+$/);
assert.ok(token&&executablePath&&evidence);
const root="../docs/evidence/";
const old=JSON.parse(await readFile(root+"m1-d06/actual_api_result.json","utf8")).valuation_response.valuation;
const candidate=JSON.parse(await readFile(root+"m1-d08/cu2211-20220907/chain_candidate.json","utf8"));
const expectedMarks=Object.fromEntries(candidate.bindings.map(b=>[b.raw_terms.symbol,b.official.settlement]));
const endpoint="/api/v2/research/valuations", coveragePath="/api/v2/research/coverage";
const browser=await chromium.launch({executablePath,headless:true,args:["--disable-background-networking","--disable-sync","--no-first-run"]});
const context=await browser.newContext({viewport:{width:1440,height:1100},deviceScaleFactor:1,locale:"zh-CN",timezoneId:"Asia/Shanghai"});
const checks=[],errors=[],external=[],cspErrors=[];
await context.route("**/*",route=>{
  if(new URL(route.request().url()).origin===origin)return route.continue();
  external.push(new URL(route.request().url()).origin);
  return route.abort();
});
const page=await context.newPage();
// Chromium's CDP response-body cache can evict a response before
// Playwright reads it. Capture a clone of the *actual browser fetch*
// response in the temporary test context, without changing the app's
// response, resending the request, persisting credentials or using a mock.
await page.addInitScript(()=>{
  window.__omTestResponses=[];
  const original=window.fetch.bind(window);
  window.fetch=async (...args)=>{
    const response=await original(...args);
    const path=new URL(response.url).pathname;
    if(path==="/api/v1/research/valuations"||path==="/api/v2/research/valuations"){
      response.clone().json().then(doc=>window.__omTestResponses.push({path,status:response.status,doc}))
        .catch(()=>window.__omTestResponses.push({path,status:response.status,failed:true}));
    }
    return response;
  };
});

page.setDefaultTimeout(32000);
page.on("pageerror",e=>errors.push(String(e)));
page.on("console",m=>{if(/Content Security Policy|Refused to execute|Refused to apply/.test(m.text()))cspErrors.push(m.text());});
let posts=0;
page.on("request",r=>{if(r.url()===origin+endpoint&&r.method()==="POST")posts++;});
const mark=name=>{checks.push(name);console.log("PASS",name);};
const waitText=(selector,text)=>page.locator(selector).filter({hasText:text}).waitFor({state:"visible"});
const render=(v,digits=2)=>new Intl.NumberFormat("zh-CN",{maximumFractionDigits:digits,minimumFractionDigits:digits}).format(v);
const fmtSigned=(v,digits=0)=>(v>0?"+":"")+render(v,digits);
async function connect(value,path=coveragePath){
  const pending=page.waitForResponse(r=>r.url()===origin+path&&r.request().method()==="GET");
  await page.locator("#bearer").fill(value);
  await page.getByRole("button",{name:"连接并验证历史数据"}).click();
  return await pending;
}
async function calculate(path=endpoint){
  const started=performance.now();
  const prior=await page.evaluate(()=>window.__omTestResponses.length);
  const pending=page.waitForResponse(r=>r.url()===origin+path&&r.request().method()==="POST");
  await page.getByRole("button",{name:"重新估值"}).click();
  const response=await pending;
  const handle=await page.waitForFunction(({prior,path})=>window.__omTestResponses.slice(prior).find(x=>x.path===path),{prior,path});
  const captured=await handle.jsonValue();
  await handle.dispose();
  assert.equal(captured.status,response.status());assert.ok(!captured.failed,"actual browser fetch body not available");
  const doc=captured.doc;
  if(doc.status==="OK"){
    await waitText('[data-testid="baseline-value"]',render(doc.valuation.grid.baseline_value));
  }
  return {status:response.status(),doc,request:response.request().postDataJSON(),elapsed_ms:performance.now()-started};
}
async function add(symbol){
  await page.locator("#contract-pick").selectOption(symbol);
  await page.getByTestId("add-contract").click();
  await page.locator(`input[data-symbol="${symbol}"]`).waitFor();
}
async function qty(symbol,value){
  await page.locator(`input[data-symbol="${symbol}"]`).fill(String(value));
}
async function remove(contract){
  await page.getByRole("button",{name:"移除 "+contract,exact:true}).click();
}
function verifyValuation(result,positions){
  assert.equal(result.status,200);assert.equal(result.doc.schema_version,"2.0");assert.equal(result.doc.status,"OK",JSON.stringify(result.doc));
  const v=result.doc.valuation;
  assert.deepEqual(result.request.positions,positions);
  assert.deepEqual(v.legs.map(l=>l.source_symbol),Object.keys(positions).sort());
  assert.equal(v.production_valuation_ready,false);
  assert.equal(v.source_versions.packet_sha256,candidate.candidate_packet_sha256);
  const expected=Object.entries(positions).reduce((sum,[s,q])=>sum+q*expectedMarks[s]*5,0);
  assert.ok(Math.abs(v.grid.baseline_value-expected)<0.001,`baseline ${v.grid.baseline_value} != ${expected}`);
  assert.equal(v.grid.heatmap_rows.flat().length,25);
  assert.equal(v.grid.price_curve.length,9);
  assert.ok(v.grid.heatmap_rows.flat().every(c=>c.status==="OK"&&c.contributions.length===v.legs.length));
  assert.ok(v.grid.price_curve.every(c=>c.status==="OK"&&c.contributions.length===v.legs.length));
  return v;
}
async function verifyGridDOM(v){
  assert.equal(await page.locator(".matrix-cell").count(),25);
  for(const [r,row] of v.grid.heatmap_rows.entries())for(const [c,cell] of row.entries()){
    assert.equal((await page.getByTestId(`heatmap-${r}-${c}`).innerText()).trim(),fmtSigned(cell.value_change));
  }
  assert.equal(await page.locator(".leg-table tbody tr").count(),v.legs.length);
}
async function noCredentials(){
  const stored=await page.evaluate(()=>({local:Object.entries(localStorage),session:Object.entries(sessionStorage),url:location.href,html:document.documentElement.outerHTML}));
  assert.equal(stored.local.length+stored.session.length,0);
  assert.ok(!stored.url.includes(token)&&!stored.html.includes(token));
}
const failure=(status,code)=>({status,contentType:"application/json",body:JSON.stringify({
  schema_version:"2.0",calculation_id:"a".repeat(32),status:"UNAVAILABLE",reason_code:code,coverage:null,valuation:null
})});
try{
  const landing=await page.goto(origin+"/?api=v2");
  assert.equal(landing.status(),200);
  assert.ok(landing.headers()["content-security-policy"].includes("script-src 'self'"));
  assert.ok(!landing.headers()["content-security-policy"].includes("unsafe-eval"));
  const denied=await connect("wrong-browser-credential-"+"x".repeat(20));
  assert.equal(denied.status(),401);
  await waitText('[role="alert"]',"令牌无效");mark("actual_auth_rejection");
  const cResponse=await connect(token),coverageDoc=await cResponse.json(),c=coverageDoc.coverage;
  assert.equal(cResponse.status(),200);assert.equal(coverageDoc.status,"OK");
  await page.getByTestId("chain-picker").waitFor();
  assert.equal(c.legs.length,66);assert.equal(c.admitted_legs,66);
  assert.equal(c.source_versions.packet_sha256,candidate.candidate_packet_sha256);
  assert.equal(await page.locator("#contract-pick option").count(),67);
  assert.equal(await page.locator("#chain-month option").count(),6);
  assert.equal(await page.locator("#chain-month option:disabled").count(),5);
  assert.equal(await page.locator('#contract-pick option[value="CU2211P79000.XSGE"]').isDisabled(),true);
  assert.equal(await page.locator('#contract-pick option[value="CU2211P80000.XSGE"]').isDisabled(),true);
  assert.equal(await page.getByTestId("selected-leg").count(),2);
  await noCredentials();mark("actual_66_contract_catalog_and_disabled_discovery_months");

  const base=await calculate();
  const pair={"CU2211C61000.XSGE":1,"CU2211P61000.XSGE":1};
  const baseV=verifyValuation(base,pair);
  assert.deepEqual(baseV.grid,old.grid);
  assert.deepEqual(baseV.legs.map(l=>l.implied_volatility),old.legs.map(l=>l.implied_volatility));
  await verifyGridDOM(baseV);mark("actual_v2_pair_exact_v1_grid_parity");

  await remove("cu2211C61000");
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  assert.equal(await page.getByRole("button",{name:"重新估值"}).isDisabled(),true);
  await remove("cu2211P61000");
  await page.locator("#right-filter").selectOption("CALL");
  await page.locator("#strike-filter").fill("60000");
  assert.equal(await page.locator("#contract-pick option").count(),2);
  await add("CU2211C60000.XSGE");
  assert.equal(await page.locator('#contract-pick option[value="CU2211C60000.XSGE"]').isDisabled(),true);
  await page.locator("#strike-filter").fill("");
  await add("CU2211C62000.XSGE");
  await page.locator("#right-filter").selectOption("PUT");
  await add("CU2211P60000.XSGE");
  await add("CU2211P62000.XSGE");
  await page.locator("#right-filter").selectOption("ALL");
  await qty("CU2211C62000.XSGE",-1);await qty("CU2211P60000.XSGE",-1);
  assert.equal(await page.getByTestId("selected-leg").count(),4);
  mark("selector_filters_distinct_keys_minimum_and_signed_positions");

  const fourPositions={"CU2211C60000.XSGE":1,"CU2211C62000.XSGE":-1,"CU2211P60000.XSGE":-1,"CU2211P62000.XSGE":1};
  const four=await calculate(),fourV=verifyValuation(four,fourPositions);
  assert.ok(Math.abs(fourV.grid.baseline_value-9980)<0.001);
  await verifyGridDOM(fourV);
  await page.getByTestId("heatmap-4-4").focus();await page.keyboard.press("Enter");
  assert.equal(await page.getByTestId("cell-detail").locator("tbody tr").count(),4);
  assert.equal(await page.getByTestId("selected-value").innerText(),fmtSigned(fourV.grid.heatmap_rows[4][4].value_change,2));
  await page.locator(".curve-values summary").click();
  assert.equal(await page.locator(".point-chip").count(),9);
  await page.locator(".point-chip").first().click();
  assert.equal(await page.getByTestId("cell-detail").locator("tbody tr").count(),4);
  await page.locator(".curve-values summary").click();
  await page.getByTestId("heatmap-2-2").click();
  await page.locator(".evidence summary").click();
  assert.ok((await page.locator(".evidence").innerText()).includes(candidate.candidate_packet_sha256));
  await page.locator(".evidence summary").click();
  mark("actual_four_leg_9980_value_full_grid_curve_and_provenance");
  await page.evaluate(()=>window.scrollTo(0,0));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:join(evidence,"desktop.png"),fullPage:true,animations:"disabled"});
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>window.scrollTo(0,0));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.ok(await page.locator("#contract-pick").isVisible());
  await page.screenshot({path:join(evidence,"mobile.png"),fullPage:true,animations:"disabled"});
  await page.setViewportSize({width:1440,height:1100});
  mark("desktop_mobile_selector_and_results_no_page_overflow");

  for(const [symbol,quantity] of Object.entries({"CU2211C59000.XSGE":1,"CU2211C63000.XSGE":-1,"CU2211P59000.XSGE":1,"CU2211P63000.XSGE":-1})){
    await add(symbol);await qty(symbol,quantity);
  }
  assert.equal(await page.getByTestId("selected-leg").count(),8);
  await page.locator("#contract-pick").selectOption("CU2211C64000.XSGE");
  assert.equal(await page.getByTestId("add-contract").isDisabled(),true);
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  const eightPositions={...fourPositions,"CU2211C59000.XSGE":1,"CU2211C63000.XSGE":-1,"CU2211P59000.XSGE":1,"CU2211P63000.XSGE":-1};
  await page.getByRole("button",{name:"+1 自然日"}).click();
  const eight=await calculate(),eightV=verifyValuation(eight,eightPositions);
  assert.ok(Math.abs(eightV.grid.baseline_value-8130)<0.001);
  assert.equal(eight.request.scenario_at,"2022-09-08T15:00:00+08:00");
  assert.notEqual(eightV.grid.configured_value_change,0);
  await verifyGridDOM(eightV);
  assert.equal(await page.getByTestId("cell-detail").locator("tbody tr").count(),8);
  mark("actual_eight_leg_8130_value_time_scenario_and_maximum_guard");

  const before=posts;
  await qty("CU2211C60000.XSGE",0);
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  await page.getByRole("button",{name:"重新估值"}).click();
  await waitText('[role="alert"]',"不含 0");
  assert.equal(posts,before);
  await qty("CU2211C60000.XSGE",1);
  mark("invalid_quantity_clears_stale_result_without_request");
  const intrinsic=await page.request.post(origin+endpoint,{headers:{Authorization:"Bearer "+token},data:{
    ...four.request,positions:{"CU2211P79000.XSGE":1,"CU2211P80000.XSGE":1}
  }});
  assert.equal(intrinsic.status(),200);
  const invalid=await intrinsic.json();
  assert.equal(invalid.reason_code,"IV_UNAVAILABLE");assert.equal(invalid.valuation,null);
  mark("actual_intrinsic_iv_unavailable_not_zero");
  const crossMonth=await page.request.post(origin+endpoint,{headers:{Authorization:"Bearer "+token},data:{
    ...four.request,positions:{"CU2211C60000.XSGE":1,"CU2212C60000.XSGE":1}
  }});
  assert.equal(crossMonth.status(),422);
  assert.equal((await crossMonth.json()).reason_code,"SLICE_SCOPE_MISMATCH");
  mark("actual_unapproved_month_rejected");

  for(const [status,code,message] of [[429,"BUSY","计算服务忙"],[200,"IV_UNAVAILABLE","隐含波动率反解不可用"]]){
    await page.route("**"+endpoint,r=>r.fulfill(failure(status,code)));
    await page.getByRole("button",{name:"重新估值"}).click();
    await waitText('[role="alert"]',message);
    assert.equal(await page.getByTestId("baseline-value").count(),0);
    await page.unroute("**"+endpoint);
  }
  const bad=structuredClone(eight.doc);
  bad.valuation.legs[0].market_option_price+=1;
  await page.route("**"+endpoint,r=>r.fulfill({status:200,contentType:"application/json",body:JSON.stringify(bad)}));
  await page.getByRole("button",{name:"重新估值"}).click();
  await waitText('[role="alert"]',"契约不符");
  await page.unroute("**"+endpoint);
  mark("fault_injection_busy_unavailable_and_mark_drift");
  let resolveIntercept,release;
  const intercepted=new Promise(r=>{resolveIntercept=r;}), delayed=new Promise(r=>{release=r;});
  await page.route("**"+endpoint,async r=>{resolveIntercept();await delayed;await r.fulfill({status:200,contentType:"application/json",body:JSON.stringify(eight.doc)}).catch(()=>{});});
  await page.getByRole("button",{name:"重新估值"}).click();
  await intercepted;
  await page.getByRole("button",{name:"取消等待"}).click();release();
  await page.getByText("已取消等待。服务端可能仍在计算，请稍后手动重试。").waitFor();
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  await page.unroute("**"+endpoint);
  mark("cancel_drops_late_response");
  await page.getByRole("button",{name:"断开连接"}).click();
  await noCredentials();await page.reload();
  await page.getByRole("button",{name:"连接并验证历史数据"}).waitFor();
  assert.equal(await page.locator("#bearer").inputValue(),"");
  mark("disconnect_reload_no_token_persistence");

  await page.goto(origin+"/?api=v1");
  const legacy=await connect(token,"/api/v1/research/coverage");
  assert.equal(legacy.status(),200);
  await page.getByText("已核验历史切片。请确认假设持仓后执行估值。").waitFor();
  assert.equal(await page.getByTestId("chain-picker").count(),0);
  const legacyVal=await calculate("/api/v1/research/valuations");
  assert.equal(legacyVal.doc.schema_version,"1.0");
  assert.deepEqual(legacyVal.doc.valuation.grid,baseV.grid);
  await noCredentials();mark("legacy_v1_real_browser_compatibility");

  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.deepEqual(cspErrors,[]);
  const result={
    status:"PASS",scope:"D08_APPROVED_ARCHIVED_CU2211_ONLY",chromium:browser.version(),checks,
    actual_api:{approved_contracts:66,official_discovered_contracts:340,available_selection_max:8,
      v1_v2_max_grid_difference_cny:0,v1_v2_max_curve_difference_cny:0,
      two_leg_value_cny:baseV.grid.baseline_value,four_leg_value_cny:fourV.grid.baseline_value,
      eight_leg_value_cny:eightV.grid.baseline_value,eight_leg_next_day_change_cny:eightV.grid.configured_value_change,
      two_leg_ms:base.elapsed_ms,four_leg_ms:four.elapsed_ms,eight_leg_ms:eight.elapsed_ms,
      actual_intrinsic_failure_reason:invalid.reason_code},
    no_page_errors:true,no_csp_errors:true,no_external_requests:true,no_persistent_credential:true,
    production_valuation_ready:false,
  };
  const actual={coverage_response:coverageDoc,two_leg:base,four_leg:four,eight_leg:eight,legacy:legacyVal};
  await writeFile(join(evidence,"actual_chain_api_result.json"),JSON.stringify(actual,null,2)+"\n");
  await writeFile(join(evidence,"browser_result.json"),JSON.stringify(result,null,2)+"\n");
  console.log(JSON.stringify(result,null,2));
}finally{
  await context.close();await browser.close();
}

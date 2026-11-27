import assert from "node:assert/strict";
import { writeFile,readFile } from "node:fs/promises";
import { join } from "node:path";
import { chromium } from "playwright-core";
const origin=process.env.OPTIONMASTER_TEST_ORIGIN;
const token=process.env.OPTIONMASTER_TEST_TOKEN;
const executablePath=process.env.OPTIONMASTER_BROWSER_EXECUTABLE;
const evidence=process.env.OPTIONMASTER_BROWSER_EVIDENCE;
assert.match(origin??"",/^http:\/\/127\.0\.0\.1:\d+$/);
assert.ok(token&&executablePath&&evidence);
const pin="2c57d7df89e87db37749d4217c68117bd1d3ccc4c332bf1cfa41b4fb28264fea";
const browser=await chromium.launch({executablePath,headless:true,args:["--disable-background-networking","--disable-sync","--no-first-run"]});
const context=await browser.newContext({viewport:{width:1440,height:1100},deviceScaleFactor:1,locale:"zh-CN",timezoneId:"Asia/Shanghai"});
const checks=[],errors=[],external=[],cspErrors=[];
await context.route("**/*",route=>{
  if(new URL(route.request().url()).origin===origin)return route.continue();
  external.push(new URL(route.request().url()).origin);return route.abort();
});
const page=await context.newPage();
page.setDefaultTimeout(32000);
await page.addInitScript(()=>{
  window.__omCatalogResponses=[];
  const fetchOriginal=window.fetch.bind(window);
  window.fetch=async (...args)=>{
    const response=await fetchOriginal(...args);
    const url=new URL(response.url);
    if(url.pathname.startsWith("/api/")){
      response.clone().json().then(doc=>window.__omCatalogResponses.push({path:url.pathname+url.search,status:response.status,doc}))
        .catch(()=>window.__omCatalogResponses.push({path:url.pathname+url.search,status:response.status,failed:true}));
    }
    return response;
  };
});
page.on("pageerror",e=>errors.push(String(e)));
page.on("console",m=>{if(/Content Security Policy|Refused to execute|Refused to apply/.test(m.text()))cspErrors.push(m.text());});
const coverage="/api/v3/research/coverage",endpoint="/api/v3/research/valuations";
let posts=0;page.on("request",r=>{if(r.method()==="POST"&&r.url()===origin+endpoint)posts++;});
const mark=name=>{checks.push(name);console.log("PASS",name);};
const render=(v,digits=2)=>new Intl.NumberFormat("zh-CN",{maximumFractionDigits:digits,minimumFractionDigits:digits}).format(v);
const signed=(v,digits=0)=>(v>0?"+":"")+render(v,digits);
const waitText=(selector,text)=>page.locator(selector).filter({hasText:text}).waitFor({state:"visible"});
async function action(path,method,click){
  const started=performance.now(),prior=await page.evaluate(()=>window.__omCatalogResponses.length);
  const pending=page.waitForResponse(r=>r.url()===origin+path&&r.request().method()===method);
  await click();const response=await pending;
  const handle=await page.waitForFunction(({prior,path})=>window.__omCatalogResponses.slice(prior).find(x=>x.path===path),{prior,path});
  const captured=await handle.jsonValue();await handle.dispose();
  assert.ok(!captured.failed);assert.equal(captured.status,response.status());
  return {status:response.status(),doc:captured.doc,request:method==="POST"?response.request().postDataJSON():null,elapsed_ms:performance.now()-started};
}
async function connect(value,path=coverage){
  await page.locator("#bearer").fill(value);
  return action(path,"GET",()=>page.getByRole("button",{name:"连接并验证历史数据"}).click());
}
async function calculate(path=endpoint){
  const result=await action(path,"POST",()=>page.getByRole("button",{name:"重新估值"}).click());
  assert.equal(result.status,200);assert.equal(result.doc.status,"OK",JSON.stringify(result.doc).slice(0,300));
  await waitText('[data-testid="baseline-value"]',render(result.doc.valuation.grid.baseline_value));
  return result;
}
async function add(symbol,month){
  await page.locator("#chain-month").selectOption(month);
  await page.locator("#contract-pick").selectOption(symbol);
  await page.getByTestId("add-contract").click();
  await page.locator('input[data-symbol="'+symbol+'"]').waitFor();
}
async function qty(symbol,value){await page.locator('input[data-symbol="'+symbol+'"]').fill(String(value));}
async function remove(contract){await page.getByRole("button",{name:"移除 "+contract,exact:true}).click();}
async function noCredentials(){
  const stored=await page.evaluate(()=>({local:Object.entries(localStorage),session:Object.entries(sessionStorage),url:location.href,html:document.documentElement.outerHTML}));
  assert.equal(stored.local.length+stored.session.length,0);assert.ok(!stored.url.includes(token)&&!stored.html.includes(token));
}
function verify(result,day,expected,legCount,monthCount){
  const v=result.doc.valuation;
  assert.equal(result.doc.schema_version,"3.0");assert.equal(v.trade_date,day);
  assert.equal(v.source_versions.packet_sha256,pin);assert.equal(v.production_valuation_ready,false);
  assert.ok(Math.abs(v.grid.baseline_value-expected)<0.001);
  assert.equal(v.legs.length,legCount);assert.equal(v.underlying_exposures.length,monthCount);
  assert.equal(v.expiries.length,monthCount);
  assert.equal(v.grid.price_curve.length,9);assert.equal(v.grid.heatmap_rows.flat().length,25);
  assert.ok(v.grid.heatmap_rows.flat().every(c=>c.status==="OK"&&c.contributions.length===legCount));
  assert.equal(v.grid.heatmap_rows[2][2].value_change,v.grid.configured_value_change);
  return v;
}
async function checkDOM(v){
  assert.equal(await page.getByTestId("underlying-exposure").count(),v.underlying_exposures.length);
  assert.equal(await page.locator(".leg-table tbody tr").count(),v.legs.length);
  assert.equal(await page.locator(".matrix-cell").count(),25);
  for(const [r,row] of v.grid.heatmap_rows.entries())for(const [c,cell] of row.entries())
    assert.equal((await page.getByTestId("heatmap-"+r+"-"+c).innerText()).trim(),signed(cell.value_change));
}
const C1="CU2211C61000.XSGE",C2="CU2212C61000.XSGE",P1="CU2211P61000.XSGE",P2="CU2212P61000.XSGE";
const old=JSON.parse(await readFile("../docs/evidence/m1-d08/cu2211-20220907/actual_chain_api_result.json","utf8")).two_leg.doc.valuation;
try{
  const landing=await page.goto(origin+"/");
  assert.equal(landing.status(),200);assert.ok(landing.headers()["content-security-policy"].includes("script-src 'self'"));
  assert.ok(!landing.headers()["content-security-policy"].includes("unsafe-eval"));
  const denied=await connect("wrong-local-research-token-"+"x".repeat(20));assert.equal(denied.status,401);
  await waitText('[role="alert"]',"令牌无效");mark("actual_authentication");
  const first=await connect(token),c=first.doc.coverage;
  assert.equal(first.status,200);assert.equal(c.approved_contract_dates,198);assert.equal(c.admitted_legs,132);
  assert.deepEqual(c.available_dates,["2022-09-07","2022-09-08"]);
  await page.getByTestId("chain-picker").waitFor();
  assert.equal(await page.locator("#trade-date").inputValue(),"2022-09-07");
  assert.equal(await page.locator("#chain-month option:disabled").count(),4);
  assert.equal(await page.locator("#contract-pick option").count(),67);
  assert.equal(await page.getByTestId("selected-leg").count(),2);
  await noCredentials();mark("approved_two_dates_and_132_current_day_contracts");
  // Original pair has exact numerical parity with prior actual v2 fixture.
  const pair=await calculate(),pairV=verify(pair,"2022-09-07",24400,2,1);
  assert.deepEqual(pairV.grid,old.grid);await checkDOM(pairV);mark("actual_v3_v2_numerical_parity");
  await remove("cu2211P61000");await qty(C1,-1);
  await add(C2,"SHFE.cu2212");
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  const cal=await calculate(),calV=verify(cal,"2022-09-07",1850,2,2);
  assert.deepEqual(calV.legs.map(l=>l.market_underlying_price),[60580,60240]);
  assert.deepEqual(calV.legs.map(l=>Math.round(l.baseline_time_to_expiry*365)),[48,78]);
  assert.equal(await page.locator("#scenario").getAttribute("max"),"2022-10-24T23:59");
  await checkDOM(calV);
  await page.getByTestId("heatmap-4-4").click();
  assert.equal(await page.getByTestId("cell-detail").locator("tbody tr").count(),2);
  await page.locator(".evidence summary").click();
  const details=await page.locator(".evidence").innerText();
  assert.ok(details.includes(pin)&&details.includes("2022-11-24")&&details.includes("2022-10-25"));
  await page.locator(".evidence summary").click();
  mark("actual_calendar_spread_1850_distinct_underlyings_expiries_greeks");
  await page.evaluate(()=>window.scrollTo(0,0));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:join(evidence,"desktop.png"),fullPage:true,animations:"disabled"});
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>window.scrollTo(0,0));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.ok(await page.locator("#trade-date").isVisible());
  await page.screenshot({path:join(evidence,"mobile.png"),fullPage:true,animations:"disabled"});
  await page.setViewportSize({width:1440,height:1100});mark("desktop_mobile_no_horizontal_overflow");
  await add(P1,"SHFE.cu2211");await qty(P1,-1);await add(P2,"SHFE.cu2212");
  await page.getByText("按交割月独立冲击（可选）",{exact:true}).click();
  await page.locator('[id="price-SHFE.cu2211"]').fill("2");
  await page.locator('[id="iv-SHFE.cu2212"]').fill("3");
  await page.getByRole("button",{name:"+1 自然日",exact:true}).click();
  const four=await calculate(),fourV=verify(four,"2022-09-07",5390,4,2);
  assert.deepEqual(four.request.price_shocks_by_underlying,{"SHFE.cu2211":0.02});
  assert.deepEqual(four.request.iv_shocks_by_expiry,{"SHFE|cu2212|2022-11-24":0.03});
  for(const l of fourV.legs){
    assert.ok(Math.abs(l.scenario_iv-l.implied_volatility-(l.underlying_id==="SHFE.cu2212"?0.03:0))<1e-8);
    assert.ok(Math.abs((l.baseline_time_to_expiry-l.scenario_time_to_expiry)*365-1)<1e-7);
  }
  assert.notEqual(fourV.grid.configured_value_change,0);await checkDOM(fourV);
  mark("actual_four_leg_independent_shocks_and_one_day_decay");
  await page.locator('[id="price-SHFE.cu2211"]').fill("");await page.locator('[id="iv-SHFE.cu2212"]').fill("");
  await page.getByRole("button",{name:"基准时点",exact:true}).click();
  await add("CU2211C60000.XSGE","SHFE.cu2211");await qty("CU2211C60000.XSGE",-1);
  await add("CU2212C60000.XSGE","SHFE.cu2212");
  await add("CU2211P60000.XSGE","SHFE.cu2211");
  await add("CU2212P60000.XSGE","SHFE.cu2212");await qty("CU2212P60000.XSGE",-1);
  assert.equal(await page.getByTestId("selected-leg").count(),8);
  assert.equal(await page.getByTestId("add-contract").isDisabled(),true);
  const eight=await calculate(),eightV=verify(eight,"2022-09-07",3700,8,2);await checkDOM(eightV);
  mark("actual_eight_leg_full_grid");
  const before=posts;await page.locator("#scenario").fill("2022-10-25T00:00");
  await page.getByRole("button",{name:"重新估值"}).click();await waitText('[role="alert"]',"到期日");
  assert.equal(posts,before);assert.equal(await page.getByTestId("baseline-value").count(),0);
  mark("earliest_expiry_guard_no_stale_result");
  const next=await action(coverage+"?trade_date=2022-09-08","GET",()=>page.locator("#trade-date").selectOption("2022-09-08"));
  assert.equal(next.status,200);assert.equal(next.doc.coverage.admitted_legs,66);
  await waitText('[data-testid="chain-count"]',"66 张已准入");
  assert.equal(await page.locator("#scenario").inputValue(),"2022-09-08T15:00");
  assert.equal(await page.getByTestId("selected-leg").count(),2);
  assert.equal(await page.locator('#chain-month option[value="SHFE.cu2212"]').isDisabled(),true);
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  const nextVal=await calculate(),nv=verify(nextVal,"2022-09-08",23280,2,1);
  assert.equal(nv.rate.quote_date,"2022-09-07");assert.equal(nv.rate.quoted_percent,1.4628);
  assert.deepEqual(nv.legs.map(l=>l.market_option_price),[2118,2538]);await checkDOM(nv);
  mark("actual_date_switch_new_quotes_rate_and_reset_positions");
  const bad=await page.request.post(origin+endpoint,{headers:{Authorization:"Bearer "+token},data:{...cal.request,trade_date:"2022-09-08",scenario_at:"2022-09-08T15:00:00+08:00"}});
  assert.equal(bad.status(),422);const invalid=await bad.json();
  assert.equal(invalid.reason_code,"SLICE_SCOPE_MISMATCH");assert.equal(invalid.valuation,null);
  mark("actual_server_rejects_cross_date_selection");
  // Fault injection is separate from genuine valuation evidence.
  const altered=structuredClone(nextVal.doc);altered.valuation.legs[0].market_underlying_price+=1;
  await page.route("**"+endpoint,r=>r.fulfill({status:200,contentType:"application/json",body:JSON.stringify(altered)}));
  await page.getByRole("button",{name:"重新估值"}).click();
  await waitText('[role="alert"]',"契约不符");assert.equal(await page.getByTestId("baseline-value").count(),0);
  await page.unroute("**"+endpoint);mark("fault_injection_rejects_inconsistent_underlying");
  await page.getByRole("button",{name:"断开连接"}).click();await noCredentials();await page.reload();
  await page.locator("#bearer").waitFor();assert.equal(await page.locator("#bearer").inputValue(),"");
  mark("no_persistent_token");
  const legacy={};
  for(const version of ["v1","v2"]){
    await page.goto(origin+"/?api="+version);
    const cp=await connect(token,"/api/"+version+"/research/coverage");assert.equal(cp.status,200);
    await page.getByRole("button",{name:"重新估值"}).waitFor();
    const val=await calculate("/api/"+version+"/research/valuations");
    assert.deepEqual(val.doc.valuation.grid,pairV.grid);
    assert.equal(val.doc.schema_version,version==="v1"?"1.0":"2.0");
    legacy[version]=val;await noCredentials();
  }
  mark("actual_v1_v2_browser_compatibility");
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.deepEqual(cspErrors,[]);
  const result={status:"PASS",scope:"D09_OPERATOR_APPROVED_ARCHIVED_ONLY",chromium:browser.version(),checks,
    actual_api:{approved_contract_dates:198,day_1_contracts:132,day_2_contracts:66,
      calendar_value_cny:calV.grid.baseline_value,four_leg_value_cny:fourV.grid.baseline_value,
      eight_leg_value_cny:eightV.grid.baseline_value,next_day_value_cny:nv.grid.baseline_value,
      configured_four_leg_change_cny:fourV.grid.configured_value_change,
      calendar_ms:cal.elapsed_ms,four_leg_ms:four.elapsed_ms,eight_leg_ms:eight.elapsed_ms,next_day_ms:nextVal.elapsed_ms,
      legacy_grid_exact_parity:true},no_page_errors:true,no_csp_errors:true,no_external_requests:true,
    no_persistent_credential:true,production_valuation_ready:false};
  await writeFile(join(evidence,"actual_catalog_api_result.json"),JSON.stringify({coverage_day1:first.doc,coverage_day2:next.doc,pair,calendar:cal,four_leg:four,eight_leg:eight,next_day:nextVal,legacy},null,2)+"\n",{flag:"wx"});
  await writeFile(join(evidence,"browser_result.json"),JSON.stringify(result,null,2)+"\n",{flag:"wx"});
  console.log(JSON.stringify(result,null,2));
}finally{await context.close();await browser.close();}

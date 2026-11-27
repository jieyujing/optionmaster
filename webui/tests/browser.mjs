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
const saved=JSON.parse(await readFile("../docs/evidence/m1-d06/actual_api_result.json","utf8")).valuation_response.valuation;
const endpoint="/api/v1/research/valuations";
const isValuation=r=>r.url()===origin+endpoint&&r.request().method()==="POST";
const browser=await chromium.launch({executablePath,headless:true,args:["--disable-background-networking","--disable-sync","--no-first-run"]});
const context=await browser.newContext({viewport:{width:1440,height:1100},deviceScaleFactor:1,locale:"zh-CN",timezoneId:"Asia/Shanghai"});
const external=[], errors=[], cspErrors=[], checks=[];
await context.route("**/*",route=>{
  if(new URL(route.request().url()).origin===origin)return route.continue();
  external.push(new URL(route.request().url()).origin);
  return route.abort();
});
const page=await context.newPage();
page.setDefaultTimeout(15000);
page.on("pageerror",e=>errors.push(String(e)));
page.on("console",msg=>{if(/Content Security Policy|Refused to execute|Refused to apply/.test(msg.text()))cspErrors.push(msg.text());});
let posts=0;
page.on("request",r=>{if(r.url()===origin+endpoint&&r.method()==="POST")posts++;});
const mark=(name)=>checks.push(name);
const waitText=(selector,text)=>page.locator(selector).filter({hasText:text}).waitFor({state:"visible"});
const failure=(status,code)=>({status,contentType:"application/json",body:JSON.stringify({
  schema_version:"1.0",calculation_id:"a".repeat(32),status:"UNAVAILABLE",reason_code:code,coverage:null,valuation:null
})});
async function connect(value) {
  await page.locator("#bearer").fill(value);
  await page.getByRole("button",{name:"连接并验证历史数据"}).click();
}
async function calculate(){
  const pending=page.waitForResponse(isValuation);
  await page.getByRole("button",{name:"重新估值"}).click();
  const response=await pending;
  return {status:response.status(),doc:await response.json(),request:response.request().postDataJSON()};
}
async function noCredentials(){
  const contents=await page.evaluate(()=>({storage:[...Object.entries(localStorage),...Object.entries(sessionStorage)],url:location.href,text:document.documentElement.outerHTML}));
  assert.equal(contents.storage.length,0);
  assert.ok(!contents.url.includes(token)&&!contents.text.includes(token));
}
try{
  const landing=await page.goto(origin+"/?api=v1");
  assert.equal(landing.status(),200);
  assert.ok(landing.headers()["content-security-policy"].includes("script-src 'self'"));
  assert.ok(!landing.headers()["content-security-policy"].includes("unsafe-eval"));
  assert.equal(landing.headers()["cache-control"],"no-store");
  const wrong="invalid-local-token-"+"x".repeat(24);
  await connect(wrong);
  await waitText('[role="alert"]',"令牌无效");
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  mark("auth_reject_real_401");
  await connect(token);
  await page.getByText("已核验历史切片。请确认假设持仓后执行估值。").waitFor();
  assert.equal(await page.locator("#qty-0").inputValue(),"1");
  assert.equal(await page.locator("#scenario").inputValue(),"2022-09-07T15:00");
  await noCredentials();mark("real_coverage_memory_only_token");

  const base=await calculate();
  assert.equal(base.status,200);assert.equal(base.doc.status,"OK");
  const v=base.doc.valuation;
  assert.deepEqual(base.request,JSON.parse(await readFile("../docs/evidence/m1-d06/actual_api_result.json","utf8")).request);
  assert.equal(v.production_valuation_ready,false);
  assert.equal(v.source_versions.packet_sha256,saved.source_versions.packet_sha256);
  assert.equal(v.source_versions.registry_sha256,saved.source_versions.registry_sha256);
  assert.equal(v.grid.baseline_value,saved.grid.baseline_value);
  assert.deepEqual(v.grid.heatmap_rows,saved.grid.heatmap_rows);
  assert.deepEqual(v.grid.price_curve,saved.grid.price_curve);
  await waitText('[data-testid="baseline-value"]',"24,400.00");
  assert.equal(await page.locator(".matrix-cell").count(),25);
  assert.equal(await page.getByTestId("heatmap-2-2").getAttribute("aria-pressed"),"true");
  const renderInt=n=>new Intl.NumberFormat("zh-CN",{maximumFractionDigits:0,minimumFractionDigits:0}).format(n);
  for(const [r,row] of v.grid.heatmap_rows.entries())for(const [c,cell] of row.entries()){
    const el=page.getByTestId(`heatmap-${r}-${c}`);
    assert.equal((await el.innerText()).trim(),(cell.value_change>0?"+":"")+renderInt(cell.value_change));
  }
  mark("real_browser_25_grid_9_curve_exact_D06");
  const selected=page.getByTestId("heatmap-4-4");
  await selected.focus();await page.keyboard.press("Enter");
  assert.equal(await selected.getAttribute("aria-pressed"),"true");
  const point=v.grid.heatmap_rows[4][4];
  const detail=page.getByTestId("cell-detail");
  assert.equal(await detail.locator("tbody tr").count(),2);
  const render2=n=>new Intl.NumberFormat("zh-CN",{minimumFractionDigits:2,maximumFractionDigits:2}).format(n);
  assert.equal(await page.getByTestId("selected-value").innerText(),"+"+render2(point.value_change));
  await page.locator(".curve-values summary").click();
  assert.equal(await page.locator(".point-chip").count(),9);
  await page.locator(".point-chip").first().click();
  assert.equal(await page.getByTestId("selected-value").innerText(),"+"+render2(v.grid.price_curve[0].value_change));
  await page.locator(".curve-values summary").click();
  await page.getByTestId("heatmap-2-2").click();
  mark("keyboard_cell_selection_and_leg_contributions");

  await page.locator(".evidence summary").click();
  assert.ok((await page.locator(".evidence").innerText()).includes(v.source_versions.packet_sha256));
  await page.locator(".evidence summary").click();
  await noCredentials();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.evaluate(()=>window.scrollTo(0,0));
  assert.equal(await page.evaluate(()=>window.scrollY),0);
  await page.screenshot({path:join(evidence,"desktop.png"),fullPage:true,animations:"disabled"});
  mark("desktop_screenshot_no_page_overflow");

  const old=posts;
  await page.locator("#qty-0").fill("0");
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  await page.getByText("参数已修改 · 请重新计算，旧结果已清除。").waitFor();
  await page.getByRole("button",{name:"重新估值"}).click();
  await waitText('[role="alert"]',"不含 0");
  assert.equal(posts,old);
  mark("dirty_result_cleared_invalid_qty_no_request");
  await page.locator("#qty-0").fill("2");await page.locator("#qty-1").fill("-1");
  await page.getByRole("button",{name:"+1 自然日"}).click();
  await page.locator("#iv-range").fill("50");
  await page.locator("#price-range").fill("100");
  const signed=await calculate();
  assert.equal(signed.doc.status,"OK");
  const w=signed.doc.valuation;
  assert.equal(signed.request.scenario_at,"2022-09-08T15:00:00+08:00");
  assert.deepEqual(w.legs.map(x=>x.quantity),[2,-1]);
  assert.ok(Math.abs(w.grid.baseline_value-9050)<0.001);
  assert.ok(w.legs[1].baseline_greeks.vega_per_unit<0);
  assert.notEqual(w.grid.configured_value_change,0);
  await waitText('[data-testid="baseline-value"]',"9,050.00");
  const unavailable=w.grid.heatmap_rows.flat().filter(x=>x.status==="UNAVAILABLE");
  assert.ok(unavailable.length>0);
  assert.ok(unavailable.every(x=>x.value_change===null&&x.contributions.length===0));
  await page.locator(".matrix-cell.cell-invalid").first().click();
  await waitText('[data-testid="cell-detail"]',"此情景不可用");
  assert.equal(await page.getByTestId("selected-value").count(),0);
  mark("real_signed_time_scenario_unavailable_null");

  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.ok(await page.locator("#qty-0").isVisible());
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:join(evidence,"mobile.png"),fullPage:true,animations:"disabled"});
  mark("mobile_screenshot_no_page_overflow");
  await page.setViewportSize({width:1440,height:1100});

  // Explicit browser boundary fault injection, not claimed as live server faults.
  await page.route("**"+endpoint,route=>route.fulfill(failure(429,"BUSY")));
  await page.getByRole("button",{name:"重新估值"}).click();
  await waitText('[role="alert"]',"计算服务忙");
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  await page.unroute("**"+endpoint);
  mark("injected_busy_no_stale_value");
  await page.route("**"+endpoint,route=>route.fulfill(failure(200,"RATE_UNAVAILABLE")));
  await page.getByRole("button",{name:"重新估值"}).click();
  await waitText('[role="alert"]',"历史利率证据不可用");
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  await page.unroute("**"+endpoint);
  mark("injected_domain_unavailable_no_value");
  await page.route("**"+endpoint,route=>route.fulfill({status:200,contentType:"application/json",body:'{"status":"OK"}'}));
  await page.getByRole("button",{name:"重新估值"}).click();
  await waitText('[role="alert"]',"契约不符");
  await page.unroute("**"+endpoint);
  mark("injected_malformed_response_rejected");

  // Abort a delayed intercepted response and verify no late state can commit.
  let intercepted;
  const waiting=new Promise(resolve=>{intercepted=resolve;});
  let release;
  const delayed=new Promise(resolve=>{release=resolve;});
  await page.route("**"+endpoint,async route=>{intercepted();await delayed;await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(base.doc)}).catch(()=>{});});
  await page.getByRole("button",{name:"重新估值"}).click();
  await waiting;
  await page.getByRole("button",{name:"取消等待"}).click();
  release();
  await page.getByText("已取消等待。服务端可能仍在计算，请稍后手动重试。").waitFor();
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  await page.unroute("**"+endpoint);
  mark("cancelled_late_response_dropped");

  await page.getByRole("button",{name:"断开连接"}).click();
  assert.equal(await page.getByTestId("baseline-value").count(),0);
  await noCredentials();
  await page.reload();
  await page.getByRole("button",{name:"连接并验证历史数据"}).waitFor();
  assert.equal(await page.locator("#bearer").inputValue(),"");
  mark("disconnect_reload_no_persistence");

  assert.deepEqual(external,[]);
  assert.deepEqual(errors,[]);
  assert.deepEqual(cspErrors,[]);
  const output={
    status:"PASS",scope:"D07_TEMPORARY_LOOPBACK_REAL_CHROMIUM_ARCHIVED_ONLY",
    chromium:browser.version(),checks,
    actual_api:{coverage:"PASS",baseline_grid_cells:25,curve_points:9,
      max_grid_difference_cny:0,max_curve_difference_cny:0,
      signed_scenario_baseline:w.grid.baseline_value,signed_scenario_change:w.grid.configured_value_change,
      signed_scenario_unavailable_cells:unavailable.length},
    browser_fault_injection:"429, domain UNAVAILABLE, malformed response, cancelled delayed response",
    screenshot_dimensions:{desktop_viewport:[1440,1100],mobile_viewport:[390,844]},
    no_external_requests:true,no_page_errors:true,no_csp_errors:true,no_persistent_credential:true,
    production_valuation_ready:false,
  };
  await writeFile(join(evidence,"browser_result.json"),JSON.stringify(output,null,2)+"\n");
  console.log(JSON.stringify(output,null,2));
}finally{
  await context.close();
  await browser.close();
}

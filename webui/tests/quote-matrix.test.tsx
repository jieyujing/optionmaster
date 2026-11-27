import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {renderToStaticMarkup} from "react-dom/server";
import {makeChain} from "./strategy-fixtures";
import {QuoteMatrix} from "../src/quote-matrix";
import {quoteColumns,putColumns,historicalColumns,historicalPutColumns,formatQuoteField,decodeQuoteDetails,type QuoteDetail,type QuoteField} from "../src/quote-details";

import {mirrorLeft,outwardFraction,alignedTop,extent} from "../src/quote-scroll";

const chain=makeChain("2022-09-07","EX.TEST.F1");
const missing:QuoteField={value:null,basis:"UNAVAILABLE",reason:"SOURCE_INCOMPLETE"};
const ids=chain.rows.flatMap(r=>[r.call,r.put].flatMap(q=>q?[q.contract_id]:[]));
const detail=()=>({schema_version:"1.0",snapshot:chain.snapshot,underlying_id:chain.underlying.underlying_id,quotes:ids.map(contract_id=>({contract_id,...Object.fromEntries(quoteColumns.filter(c=>c[0]!=="symbol"&&c[0]!=="net_pos").map(c=>[c[0],{...missing}]))} as QuoteDetail))});
test("Qt source field parity and 29 cells for all rows including missing side",()=>{
  const src=readFileSync("../vnpy_optionmaster/ui/monitor.py","utf8").split("class OptionMarketMonitor")[1]!.split("def __init__")[0]!;
  const qt=[...src.matchAll(/"name": "([^"]+)", "display": "([^"]+)"/g)].map(m=>[m[1],m[2]]);
  assert.deepEqual(quoteColumns.map(c=>c.slice(0,2)),qt);
  assert.deepEqual(putColumns.map(c=>c.slice(0,2)),[...qt].reverse());
  const html=renderToStaticMarkup(<QuoteMatrix chain={chain} details={null} atm={100} precision={4} renderQuantity={q=><input aria-label={q.contract_id+" 持仓数量"} value="0" readOnly/>}/>);
  const bodies=[...html.matchAll(/<tbody>(.*?)<\/tbody>/g)].map(m=>m[1]!);
  assert.equal(bodies.length,3);
  const rows=bodies.map(b=>[...b.matchAll(/<tr[^>]*data-strike="([^"]+)"[^>]*>(.*?)<\/tr>/g)]);
  const order=[...quoteColumns.map(c=>"CALL:"+c[0]),"CENTER:strike",...putColumns.map(c=>"PUT:"+c[0])];
  for(let i=0;i<chain.rows.length;i++){
    const triplet=rows.map(r=>r[i]!);
    assert.deepEqual(triplet.map(r=>Number(r[1])),Array(3).fill(chain.rows[i]!.strike));
    const cells=triplet.map(r=>[...r[2]!.matchAll(/data-field="([^"]+)"/g)].map(m=>m[1]));
    assert.deepEqual(cells.map(c=>c.length),[14,1,14]);
    assert.deepEqual(cells.flat(),order);
    assert.match(triplet[0]![2]!,/data-field="CALL:bid_price"[^>]*>—/);
    assert.match(triplet[0]![2]!,/data-field="CALL:ask_price"[^>]*>—/);
  }
  assert.deepEqual(rows.map(r=>r.length),Array(3).fill(chain.rows.length));
  assert.match(bodies[2]!,/data-field="PUT:symbol"[^>]*>缺失/);
  assert.match(bodies[0]!,/持仓数量/);
  assert.match(html,/aria-label="CALL 水平滚动"/);
  assert.match(html,/aria-label="PUT 水平滚动"/);
  assert.match(html,/aria-label="固定行权价"/);
});
test("historical matrix displays actual prices and model IV without pretending to have a book",()=>{
  const c={...chain,snapshot:{...chain.snapshot,source_versions:{...chain.snapshot.source_versions,LOCAL_USER_ASSUMPTION:"local"}}};
  const d={...detail(),schema_version:"1.1",snapshot:c.snapshot};
  for(const q of d.quotes){
    q.close_price={value:30.8,basis:"ADMITTED_DAILY",reason:null};
    q.settlement_price={value:31,basis:"ADMITTED_DAILY",reason:null};
    q.model_iv={value:.15702246,basis:"SETTLEMENT_MODEL",reason:null};
  }
  const decoded=decodeQuoteDetails(d,c,true);
  assert.throws(()=>decodeQuoteDetails(d,c));
  const html=renderToStaticMarkup(<QuoteMatrix chain={c} details={decoded} atm={100} precision={4} renderQuantity={()=>null}/>);
  assert.equal(historicalColumns.length,11);
  assert.deepEqual(historicalPutColumns,[...historicalColumns].reverse());
  assert.match(html,/data-field="CALL:close_price"[^>]*>30.8000/);
  assert.match(html,/data-field="PUT:model_iv"[^>]*>15.70%/);
  assert.doesNotMatch(html,/data-field="CALL:bid_price"/);
  d.quotes[0]!.close_price={value:30.8,basis:"ADMITTED_BOOK",reason:null};
  assert.throws(()=>decodeQuoteDetails(d,c,true));
});
test("center-first and outward mirror mapping, symmetric endpoints and round-trip",()=>{
  assert.deepEqual([mirrorLeft("CALL",0,1000),mirrorLeft("PUT",0,1000)],[1000,0]);
  assert.deepEqual([mirrorLeft("CALL",1,1000),mirrorLeft("PUT",1,1000)],[0,1000]);
  for(const f of [0,.125,.5,.75,1]){
    const l=mirrorLeft("CALL",f,800),r=mirrorLeft("PUT",f,800);
    assert.equal(l+r,800);
    assert.equal(outwardFraction("CALL",l,800),f);
    assert.equal(outwardFraction("PUT",r,800),f);
  }
  assert.equal(mirrorLeft("CALL",.25,799),599);
  assert.equal(mirrorLeft("PUT",.25,800),200);
  assert.equal(outwardFraction("CALL",1000,800),0);
  assert.equal(outwardFraction("PUT",-10,800),0);
  assert.equal(mirrorLeft("PUT",Infinity,800),0);
  assert.equal(extent(500,600),0);
  assert.equal(alignedTop(300,280,290,270),270);
  assert.equal(alignedTop(-5,280,290,270),0);
});
test("strict exact source and contract identity, basis, precision and finite values",()=>{
  const d=detail();
  assert.equal(decodeQuoteDetails(d,chain).quotes.length,ids.length);
  assert.throws(()=>decodeQuoteDetails({...d,underlying_id:"OTHER"},chain));
  assert.throws(()=>decodeQuoteDetails({...d,snapshot:{...d.snapshot,trade_date:"2022-09-08"}},chain));
  assert.throws(()=>decodeQuoteDetails({...d,quotes:[...d.quotes,d.quotes[0]]},chain));
  assert.throws(()=>decodeQuoteDetails({...d,quotes:d.quotes.slice(1)},chain));
  const q=d.quotes[0]!;
  q.bid_price={value:5,basis:"SETTLEMENT_MODEL",reason:null};
  assert.throws(()=>decodeQuoteDetails(d,chain));
  q.bid_price={value:0,basis:"UNAVAILABLE",reason:"SOURCE_INCOMPLETE"};
  assert.throws(()=>decodeQuoteDetails(d,chain));
  q.bid_price={value:5,basis:"ADMITTED_BOOK",reason:null};
  q.bid_impv={value:.12345,basis:"ADMITTED_BOOK",reason:null};
  q.volume={value:0,basis:"ADMITTED_DAILY",reason:null};
  assert.equal(formatQuoteField("bid_price",q.bid_price,4),"5.0000");
  assert.equal(formatQuoteField("bid_impv",q.bid_impv,4),"12.35%");
  assert.equal(formatQuoteField("volume",q.volume,4),"0");
  assert.equal(formatQuoteField("ask_price",missing,4),"—");
  assert.equal(formatQuoteField("theo_delta",{value:-1.23456,basis:"SETTLEMENT_MODEL",reason:null},2),"-1.23");
  assert.equal(decodeQuoteDetails(d,chain).quotes.length,ids.length);
  q.bid_price={value:Infinity,basis:"ADMITTED_BOOK",reason:null};
  assert.throws(()=>decodeQuoteDetails(d,chain));
});

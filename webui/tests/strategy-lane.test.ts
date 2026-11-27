import test from "node:test";
import assert from "node:assert/strict";
import {LatestLane,type LaneResult} from "../src/strategy-lane";
const tick=(ms=0)=>new Promise<void>(r=>setTimeout(r,ms));
function deferred<T>(){let resolve!:(v:T)=>void,reject!:(e:unknown)=>void;const promise=new Promise<T>((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
test("one in flight, A-B-A generation, only latest pending",async()=>{
 const calls:{q:number;d:ReturnType<typeof deferred<number>>}[]=[],seen:(LaneResult<number>|null)[]=[];
 const l=new LatestLane<number,number>(q=>{const d=deferred<number>();calls.push({q,d});return d.promise;},v=>seen.push(v),0,500);
 try{
  l.submit("A",1);await tick();l.submit("B",2);l.submit("A",3);assert.equal(calls.length,1);
  calls[0]!.d.resolve(100);await tick();assert.equal(calls.length,2);assert.equal(calls[1]!.q,3);
  assert.ok(!seen.some(v=>v?.value===100));calls[1]!.d.resolve(300);await tick();
  assert.equal(seen.at(-1)?.value,300);
 }finally{l.dispose();}
});
test("debounce drops obsolete pending, deadline does not publish late reply",async()=>{
 const calls:{signal:AbortSignal;d:ReturnType<typeof deferred<number>>}[]=[],seen:(LaneResult<number>|null)[]=[];
 const l=new LatestLane<number,number>((_,signal)=>{const d=deferred<number>();calls.push({signal,d});return d.promise;},v=>seen.push(v),15,25);
 try{
  l.submit("1",1);l.submit("2",2);assert.equal(calls.length,0);await tick(20);assert.equal(calls.length,1);
  await tick(35);assert.equal(calls[0]!.signal.aborted,true);assert.equal(seen.at(-1)?.error,"DEADLINE_EXCEEDED");
  l.submit("3",3);calls[0]!.d.resolve(200);await tick(20);assert.equal(calls.length,2);assert.ok(!seen.some(v=>v?.value===200));
  calls[1]!.d.resolve(300);await tick();assert.equal(seen.at(-1)?.value,300);
 }finally{l.dispose();}
});
test("429 error no retry; clear/dispose rejects late result",async()=>{
 const calls:{signal:AbortSignal;d:ReturnType<typeof deferred<number>>}[]=[],seen:(LaneResult<number>|null)[]=[];
 const l=new LatestLane<number,number>((_,signal)=>{const d=deferred<number>();calls.push({signal,d});return d.promise;},v=>seen.push(v),0,500);
 l.submit("1",1);await tick();calls[0]!.d.reject(Error("BUSY"));await tick();assert.equal(seen.at(-1)?.error,"BUSY");assert.equal(calls.length,1);
 l.submit("2",2);await tick();l.submit(null,null);assert.equal(seen.at(-1),null);
 l.dispose();assert.equal(calls[1]!.signal.aborted,true);calls[1]!.d.resolve(2);await tick();assert.equal(seen.at(-1),null);
});

// Test-only fictitious source. No network/production trust.
import raw from "../../docs/evidence/m1-d12/contract-fixtures.json";
import type { StrategyChainDTO, StrategyCoverageDTO, OptionQuoteDTO } from "../src/generated/strategy-types";
import { validateStrategyEnvelope } from "../src/generated/strategy-validators.mjs";
if(!validateStrategyEnvelope(raw.chain_response))throw Error("Bad synthetic chain fixture");
const fixture=raw.chain_response.chain as unknown as StrategyChainDTO;
export function makeChain(day:string,underlyingId:string,product="TEST"):StrategyChainDTO{
  const id=(day==="2022-09-07"?"a":"e").repeat(64);
  const month=underlyingId.endsWith("F2")?"TEST2":"TEST1";
  const underlying={...fixture.underlying,underlying_id:underlyingId,delivery_month:month,market_price:day==="2022-09-07"?100:110};
  const template=fixture.rows[0]!.call!;
  const rows=[90,100,110,120,130].map(strike=>{
    const call:OptionQuoteDTO={...template,underlying_id:underlyingId,contract_id:underlyingId+".C"+strike,strike,expiry_id:underlyingId+".EXP",
      market_price:day==="2022-09-07"?5:7,expiry_date:"2022-10-01"};
    const put:OptionQuoteDTO={...call,contract_id:underlyingId+".P"+strike,option_type:"PUT"};
    return {strike,call,put:strike===130?null:put};
  });
  return {snapshot:{...fixture.snapshot,snapshot_id:product==="TEST"?id:"f".repeat(64),product,trade_date:day,reference_at:day+"T15:00:00+08:00"},
    underlying,rows,next_cursor:null};
}
export const fixtureCoverage:StrategyCoverageDTO={products:[{
  exchange:"EX",product:"TEST",latest_complete_trade_date:null,spot_status:"UNAVAILABLE",spot_reason:"SCOPE_UNAVAILABLE",
  days:["2022-09-07","2022-09-08"].map(day=>({trade_date:day,status:"ARCHIVE_ONLY",reason:null,
    months:["EX.TEST.F1","EX.TEST.F2"].map((underlying_id,i)=>({underlying_id,delivery_month:"TEST"+(i+1),
      discovered_contracts:9,admitted_contracts:day==="2022-09-08"&&i===1?0:9,
      status:day==="2022-09-08"&&i===1?"DISCOVERY_ONLY":"ADMITTED",
      reason:day==="2022-09-08"&&i===1?"CANDIDATE_ONLY":null}))}))
},{
  exchange:"EX",product:"OTHER",latest_complete_trade_date:null,spot_status:"UNAVAILABLE",spot_reason:"SCOPE_UNAVAILABLE",
  days:[{trade_date:"2022-09-07",status:"ARCHIVE_ONLY",reason:null,
    months:[{underlying_id:"EX.OTHER.F1",delivery_month:"OTHER1",discovered_contracts:9,admitted_contracts:9,status:"ADMITTED",reason:null}]}]
}],next_cursor:null};

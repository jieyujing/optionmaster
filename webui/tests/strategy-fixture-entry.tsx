import { createRoot } from "react-dom/client";
import { StrategyBuilder } from "../src/strategy-builder";
import { fixtureCoverage,makeChain } from "./strategy-fixtures";
import type { StrategyDataClient } from "../src/strategy-api";
const client:StrategyDataClient={
  async coverage(){return fixtureCoverage;},
  async chain(s){if(s.trade_date==="2022-09-08"&&s.underlying_id.endsWith("F2"))throw Error("CANDIDATE_ONLY");
    return makeChain(s.trade_date,s.underlying_id,s.product);}
};
createRoot(document.getElementById("root")!).render(<StrategyBuilder client={client} fixture/>);

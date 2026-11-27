import {createRoot} from "react-dom/client";
import {StrategyResearchApp} from "./strategy-app";
const root=document.getElementById("root");
if(!root)throw Error("ROOT_MISSING");
createRoot(root).render(<StrategyResearchApp/>);

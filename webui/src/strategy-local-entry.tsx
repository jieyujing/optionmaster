import {createRoot} from "react-dom/client";
import {StrategyResearchApp} from "./strategy-app";
const root=document.getElementById("root");
if(!root)throw Error("ROOT_MISSING");
// Only the dedicated local preview runner builds this entry.
createRoot(root).render(<StrategyResearchApp localPreview/>);

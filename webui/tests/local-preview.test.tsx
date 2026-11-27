import assert from "node:assert/strict";
import test from "node:test";
import {renderToStaticMarkup} from "react-dom/server";
import {StrategyResearchApp} from "../src/strategy-app";
test("only the explicit local entry skips the login UI",()=>{
  const normal=renderToStaticMarkup(<StrategyResearchApp/>);
  assert.match(normal,/研究访问令牌/);
  assert.match(normal,/type="password"/);
  const local=renderToStaticMarkup(<StrategyResearchApp localPreview/>);
  assert.match(local,/期权策略构建器/);
  assert.match(local,/本机预览/);
  assert.doesNotMatch(local,/研究访问令牌|type="password"|断开会话/);
});

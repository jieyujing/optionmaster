# OptionMaster IV Phase 1 — Standards and Spec Review (2026-11-23)

- **Goal:** `wc_goal_60H7BlqG-tRBeJAf`
- **Session:** `wc_sess_eb6OiX8SQJ8lLPcr`
- **Base:** `0ec421f9`, branch `fix/optionmaster-audit-20261122`, **uncommitted workspace differences**, including untracked task-owned files
- **Spec:** `docs/optionmaster-iv-performance-risk-snapshot-phase1.md`
- **Standards:** `AGENTS.md`, `README.md`; smell baseline used as review heuristic only
- **Scope:** Offline code/tests and benchmark; no real gateway, exchange calendar, external market data, orders, deployment, Git publication or production approval
- **Review mode:** Two axes inspected sequentially from actual source and fixtures. No parallel subagent review was used.

## Axis 1 — Standards

| Severity | Finding | Source / reasoning |
| --- | --- | --- |
| PASS (observed) | Hard trading and market-data guards remain in the code under review; there is no automatic Q45/N/W release, no test calendar made into a production policy, and Black IV inversion stays Python. | `AGENTS.md`; `trading_sessions.py`, `engine.py`, `pricing/backend.py` |
| LOW (maintainability) | Risk revocation fields are manually reset at multiple lifecycle points, raising *possible Duplicated Code / Shotgun Surgery* concerns; future additions to risk identity could miss a path. | `pricing/iv_runtime.py` attach/submit/pump/close; `risk_safety.py` |
| LOW (harness design) | The runnable chain benchmark imports the private `tests/test_iv_chain_burst.py` fixture through `sys.path`; acceptable in the current offline-only harness, but a dedicated shared test-support module would make reuse less fragile. | `script/benchmark_iv_chain.py`, `tests/test_iv_chain_burst.py` |
| PASS (observed) | Worker restart now requires a **new Runtime**, avoiding reuse of stale dispatcher epoch state. Worker Black-76 and Black-Scholes startup numerical selftests run before readiness, and their samples do not prewarm the operational LRU. | `pricing/iv_runtime.py`, `pricing/iv_worker.py`, focused tests |

No new hard violation of the documented development-time safety rules was identified in the inspected changes. These findings are **not** a security certification or full code audit.

## Axis 2 — Spec

| Severity | Contract | Finding / action |
| --- | --- | --- |
| **OFFLINE REMEDIATED / UI FOLLOW-UP** | P1-06 / `SnapshotCurrent`, coherent portfolio aggregation | The original finding was reproduced (4 held, only 1 current, displayed `portfolio.pos_delta` changed). A separate actionable `PortfolioRiskSnapshot` now requires all held dependencies fresh and is revalidated at read/send time; Delta Hedge checks it. Numeric display totals may still be mixed until all replies arrive, so UI consumers need an explicit validity presentation contract before production adoption. The fix passed 3 new focused tests plus full regression; see remediation below. |
| **HIGH / BLOCKED BY UNSET POLICY** | P1-08/P1-09 / G3 reconnect | Q45 clock, positive N, finite W, trading-day/adapter replay identity are unresolved. The stronger latching source-switch quarantine is implemented, but there is **no authorised auto-release**. Keep existing strict ingestion and operator reconciliation. |
| **HIGH / G5 NO-GO** | P1-00/P1-10 measured performance | Repeatable **synthetic** full-chain 12/72 and 24/144 mixed-feed runs exist, including p50/p95/p99, CPU observations, bounded queue stats and sampled worker RSS. No real CTP/replay file, approved latency/CPU/memory thresholds, production rollback drill, or Windows/CTP acceptance. In the 24/144 paced benchmark, event-to-risk-ready p95 was **24.583 ms sync vs 76.352 ms Worker**. Faster Worker *ingress* is not proof of faster actionable risk. Source: `docs/optionmaster-iv-phase1-implementation-status.md` and `docs/optionmaster-iv-chain-benchmark-synthetic-20261123.json` (the latter is an independent 24/96 sample). |
| MEDIUM / RESOLVED IN THIS CONTINUATION | P1-07 Electronic Eye actual send seam | Previously `OptionAlgoEngine.send_order` could send directly when `algo.trading_active=False` because risk validation was conditional. It now rejects inactive algorithms and checks `risk_snapshot_is_fresh` unconditionally before and immediately before order construction. The old unit fixture was aligned to model explicitly **active** synthetic quote dependencies; separate inactive and cancel-audit/restart tests cover fail-closed paths. |
| MEDIUM / RESOLVED IN THIS CONTINUATION | P1-03 Worker generation recovery | Prior `OptionIVRuntime.start` could start a stopped Worker within the old dispatcher state. This is now refused; new Runtime construction is required for a new epoch. |
| MEDIUM / RESOLVED IN THIS CONTINUATION | P1-05 cold exact-input LRU | Startup model selftest previously inserted numerics in the operational LRU. Regression reproduced **3** hits where only **2** within-batch hits were expected; selftest now uses a separate cache and the operational cache is cold. |
| MEDIUM / UNVERIFIED | P1-01 numeric speed | The safeguarded high-precision solver passed synthetic numerical parity checks but was slower than legacy Brent in an earlier 600-case microbenchmark. The live default stays Brent; no proof of net numerical performance gain. |

### Decision and acceptance

**Recommendation:** **NO PRODUCTION GO** for the Worker-backed risk pipeline or reconnect auto-release. Retain the audited synchronous Brent path and existing strict source/session/order guards. The code and synthetic benchmark results are valid development evidence only. Approval to connect CTP, publish/commit, or deploy has not been given.

**Next safe engineering slice:** Portfolio actionable epoch and Greeks-monitor readiness presentation are implemented and offline-tested. Obtain representative offline market-data captures and preapproved G5 thresholds; Q45/N/W/day/session/source policy remains independently blocked pending explicit separate decision.

**Validation note:** Earlier entries describe the pre-remediation state. The latest project-wide regression after the chain-integrity patch is **245 passed, 4 third-party warnings**, with Ruff, mypy, dependency check, wheel build and isolation PASS. The implementation-status document records details.

## Remediation record: actionable whole-portfolio epoch

- **Original evidence:** In offline Worker replay, 1/4 held options was current while `portfolio.pos_delta` had changed. Neither a display aggregate nor individual validity alone constituted a coherent actionable risk publication.
- **Contract added:** `PortfolioData.actionable_risk_snapshot` is an immutable, current aggregate snapshot or `None`. Its underlying epoch includes all held option and held-underlying dependencies; it is invalid after an input, holding, quote, Worker or numerical change. A real Delta Hedge path consumes and revalidates this snapshot before sending.
- **Scope:** No changes to pricing model selection, strategy logic, existing source-time safety, automatic reconnect release or unified order Sender. Display Greeks may still be intermediate and must not be interpreted as ready without the new field; UI labeling remains pending.
- **TDD:** `tests/test_iv_portfolio_epoch.py` reproduced missing API with 2 failures; after patch, all 3 tests pass, including a mocked outbound hedging gate. Full test suite 244 passed.
- **Post-change synthetic 24/144:** paced risk-ready p95 20.339 ms sync versus 73.947 ms Worker; burst ingress p95 20.181 versus 0.606 ms, plus 66.22 ms Worker drain. These figures do not establish numerical speedup or release readiness.
- **Current decision:** P1-06 actionable risk publication **addressed in offline code**; final G5 and G3 still NO-GO. Keep Worker disabled by default.

### Additional consistency proof

A further test intentionally changed a held position and OptionData position Greeks without refreshing ChainData. The prior actionable snapshot accepted this stale chain sum (**red**). `portfolio_risk_identity` now verifies both per-instrument position Greek consistency and portfolio aggregation against directly summed current holdings; the test is **green**. The new four-test portfolio-epoch suite and the full **245-pass** regression (with 4 external warnings) support the bounded offline fix. Latest synthetic 24/144 paced risk-ready p95 remains slower in Worker (**68.395 ms**) than sync (**20.969 ms**); burst ingress p95 is faster (**0.629 vs 21.026 ms**), but this is not an actionable performance Go. UI readiness display and real replay G5 approval remain unverified.

## Follow-up review: UI risk presentation (2026-11-23)

**UI gap remediated at the presentation seam:** `OptionGreeksMonitor` labels a portfolio row `快照有效` only when the actionable aggregate is current, or `仅供展示` when unavailable. Existing Greeks remain visible with a status and tooltip. `EVENT_TIMER` independently refreshes the row after asynchronous Worker completion or time-based invalidation.

**Offline tests:** `tests/test_iv_greeks_ui_readiness.py` produced 2 red failures against the old UI; 3 tests now pass (display validity, timer refresh, event listener). Latest full suite **248 passed, 4 third-party pandas warnings**; Ruff, mypy, dependency check, wheel build, artifact isolation, diff check PASS.

**Residual caveats:** The tests use Qt cell doubles, not full rendered desktop/Windows UI acceptance. Only the portfolio summary currently displays aggregate validity; other rows remain numerical. Display readiness is never trading permission. **NO PRODUCTION GO:** Q45/N/W/source/session proof, real market replay, G5 p95/p99/CPU/RSS targets, rollback and CTP/Windows remain unverified. Worker opt-in and reconnect auto-release off.

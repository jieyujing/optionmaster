# OptionMaster IV Phase 1 — implementation evidence and remaining gates

- **Date:** 2026-11-23
- **Source Spec:** `docs/optionmaster-iv-performance-risk-snapshot-phase1.md`
- **Workflow Goal:** `wc_goal_60H7BlqG-tRBeJAf`
- **Workflow Session:** `wc_sess_eb6OiX8SQJ8lLPcr`
- **Checkout:** `fix/optionmaster-audit-20261122` at base `0ec421f9d0639f3eeb0444b2d3a7bc7adc954bf0`
- **Status:** PARTIAL IMPLEMENTATION — not authorized for production CTP or automated live trading. No Git commit, push, PR, deployment, or live order.

## Verified implementation slices

| Spec item | Source and owned seam | Current evidence | State |
| --- | --- | --- | --- |
| P1-00 baseline | Existing code and `tests/` | Original 189 tests passing; fixed source base. No representative market replay dataset/target frozen. | PARTIAL |
| P1-01 structured IV | `pricing/iv_solver.py`, `black_76.py`, `black_scholes.py` | New `IVResult`, explicit invalid/fail status, safeguarded Newton/bisection, high-precision values; existing Brent float API retained as sync production default. | IMPLEMENTED, OFFLINE TESTED |
| P1-02 immutable identity | `pricing/iv_worker.py` and `pricing/iv_snapshot.py` | Immutable scalar IPC request plus local Tick-object identity, generation, sequence, model version and validated input snapshots. | IMPLEMENTED, OFFLINE TESTED |
| P1-03 one Worker lifecycle | `pricing/iv_worker.py` | Spawned isolated process, numerical startup selftest, bounded queues, no worker access to shared risk, explicit generation, fail-closed process exit and no silent synchronous fallback when stopped. | IMPLEMENTED EXPERIMENTALLY; operator restart/production timeout policy not frozen |
| P1-04 scheduling | `LatestWinsScheduler`, `IVDispatcher` | Risk-first, pending-only latest-wins, bounded fairness, queue overflow refusal and late-result discard. | IMPLEMENTED, OFFLINE TESTED |
| P1-05 exact LRU | `WorkerCache` | Bounded, only accepted numerical outputs, exact scalar inputs/model version, cold on restart; no cached trading permission. | IMPLEMENTED, OFFLINE TESTED |
| P1-06 risk commit | `pricing/iv_snapshot.py`, `iv_runtime.py`, `base.py`, `risk_safety.py`, `engine.py` | An immutable `PortfolioRiskSnapshot` is published as **actionable** only when every held dependency is current. The read-time epoch guard rejects stale holdings, quote generations and altered totals; Delta Hedge uses it at the send boundary. Intermediate display aggregates remain non-actionable and UI display labeling remains unverified. | OFFLINE FIX VERIFIED; UI/PRODUCTION GATE OPEN |
| P1-07 outbound guards | `engine.py` | Delta Hedge revalidates held-option risk and portfolio Delta; Electronic Eye now always rejects inactive algo or stale option risk at the direct send boundary. Failed-close audit requires explicit stop/restart. | IMPLEMENTED, OFFLINE TESTED |
| P1-08 adapter evidence | `engine.py`, `trading_sessions.py` | Strict chronology remains; source-switch now quarantines at ingress even if the new source Tick is stale/invalid. Real gateway event-ID/reconnect replay evidence absent. | SAFETY GUARD ADDED; ADAPTER CONTRACT BLOCKED |
| P1-09 reconnect release | **No new auto-release path activated** | Affected contract quarantine stays latched until explicitly reconciled. Q45 clock, N/W, official day and lifecycle evidence unresolved; no N/W auto-release counters are enabled. | BLOCKED, FAIL-CLOSED |
| P1-10 rollout | `script/benchmark_iv_pipeline.py`, `script/benchmark_iv_chain.py` + tests | Reproducible synthetic 12/24-option mixed-feed paced and burst results, queue, CPU and sampled RSS exist; independent real market-data replay, agreed thresholds, rollback drill, Windows and CTP remain unverified. | PARTIAL, G5 NO-GO |

## Last observed verification

- `.venv/bin/python -m pytest tests -q` — **468 passed, 4 pandas deprecation warnings** (latest post-solver-fastpath patch).
- `.venv/bin/ruff check .` — **PASS**.
- `.venv/bin/mypy vnpy_optionmaster` — **PASS**, 25 source files.
- `uv pip check --python .venv/bin/python` — **PASS**, 67 installed packages.
- `uv build` — **PASS**, universal `py3-none-any` wheel and sdist.
- `.venv/bin/python -m pytest tests/test_build_artifacts.py -q` — **3 passed**.
- `git diff --check` — **PASS** for tracked differences; additional newly created paths are **untracked** and must be reviewed before any authorized commit.

**Historical pre-fastpath numerical benchmark qualification:** 600 seeded synthetic option-price inversions compared against SciPy `brentq`: full-precision structured solver reached approximately 1e-8 absolute IV agreement on tested cases, but was approximately **3.3–3.5x slower** than direct Brent inversion in this **older** microbenchmark. The later scalar fast-path changes this performance; see the latest section below. This is not a representative Tick-to-risk latency benchmark. The original synchronous Brent code was intentionally preserved for backward compatibility and performance.

## Material blockers and next safe work

1. **Q45 and N/W**: freeze the eligible recovery clock, valid-N window logic, operator-approved per-contract trading day and proven gateway replay identity before any reconnect auto-release; otherwise leave it off.
2. **Performance gate G5**: supply the real offline replay workload, initial/memory/queue ceilings and acceptable p95/p99 thresholds; measure identical before/after option+underlying Tick streams with complete chain Greeks and position updates. No performance Go without results.
3. **Worker lifecycle**: explicit `inflight_timeout_seconds` is optional and defaults to `None` in the current experimental constructor (no timeout). This is **not an approved production default**. Freeze timeout/restart/runbook and test genuine blocked Worker recovery before exposure.
4. **Production integration**: worker remains opt-in; inspect its model/backend selection and synthetic-underlying handling under real gateway adapters. Never hot-switch during active trading; verify live options/underlying dependencies and every send/close/cancel branch.
5. **Separate production gates**: official verified exchange trading calendars, real CTP order/trade/cancel races and reconnect reconciliation, Windows qualification, operator audit and deployment authorization.

All new work is local and auditable. Passing offline unit tests or packaging is **not production readiness**. Original untracked `docs/agents/*` and Spec files were preserved.

## Continuation checkpoint — 2026-11-23

**Scope:** The same Goal/Session, branch and baseline were explicitly resumed. All changes remain local; no live CTP, order send, commit, push, PR or deployment.

### Safety and memory corrections

- **Source switch quarantine:** `OptionEngine.process_tick_event` now quarantines the previously accepted instrument when another gateway appears **before** its timestamp, session or book quality is evaluated. This also catches a stale/crossed first switched-source Tick. The quarantine flag stays latched on the affected `InstrumentData` and cannot be cleared by fresh same-source Ticks. `risk_snapshot_is_fresh`, Delta Hedge underlying order checks, and Electronic Eye outbound checks all reject quarantined dependencies. **It is not a reconnect auto-release implementation**; adapter/session/official-day identity and Q45/N/W are still required for recovery.
- **Worker tracking bound:** `IVDispatcher` releases `_latest` metadata for accepted completed requests; pending/in-flight remain guarded against stale generations and outdated requests. Tested with 120 distinct sequential symbols.
- **Risk generation and input identity:** On `OptionIVRuntime.attach`, existing synchronous risk readiness is invalidated immediately. Main-owner commits record the Worker generation and exact pricing-dependency fingerprint. Order-time risk checks reject new-generation or modified rate/TTE/adjustment/underlying/model inputs, even if Tick object identities did not change. Worker close still blocks automatic risk readiness.
- **Verified tests:** Red tests reproduced the unquarantined source switch, stale first-source switch, unlimited completed-request metadata and synchronous-to-Worker hot-swap acceptance before repairs. After repairs: **236 passed, 4 third-party warnings**, `ruff check .` PASS, `mypy vnpy_optionmaster` PASS (25 source files), `uv pip check --python .venv/bin/python` PASS, `uv build` PASS and wheel isolation **3 passed**.

### Repeatable synthetic experiment — NOT production G5

Command: `.venv/bin/python script/benchmark_iv_pipeline.py --events 24`

Environment: macOS 27.0.1 ARM64, Python 3.13.18. Four cases: Black-76, synthetic test session, fixed underlying, one option, 24 chronological events for each run, no gateway or real orders. These runs wait for each Tick to become risk-ready; they are **not burst-load or full-chain capacity measurements**.

| Pilot run | Scenario | Sync p95 (ms) | Worker p95 (ms) | Both ready |
| --- | --- | ---: | ---: | --- |
| First | Same premium | 0.768 | 1.618 | 24/24 |
| First | Cycling premium | 0.960 | 2.689 | 24/24 |
| Second (after safety patch) | Same premium | 1.009 | 1.615 | 24/24 |
| Second (after safety patch) | Cycling premium | 1.012 | 2.587 | 24/24 |

Worker startup in these two runs was roughly 0.53–0.57 s per worker, excluded from per-Tick measurements. The result **does not support claiming a latency performance gain**. CPU, RSS, mixed option/underlying fan-out and p95/p99 under bursts are not measured; representative workload and acceptance thresholds are not approved. The legacy Brent synchronous path and Worker-off default remain the safe operational setting.

**Open production gates:** G3 (Q45 W clock, positive N, finite W, day/session identities, source-specific replay evidence); G5 (representative same-feed benchmark, approved performance/resource targets, rollback drill); Windows and CTP real reconnect/order/cancel/reconciliation acceptance. Complete Phase 1 production sign-off is **NOT ACHIEVED**.

## Additional continuation — offline full-chain synthetic benchmark (2026-11-23)

**Scope:** Script `script/benchmark_iv_chain.py` uses the actual OptionEngine → PortfolioData → ChainData → OptionData path, a synthetic 12/24-option Black-76 chain and 72/144 seeded chronological mixed option/underlying Tick events. The market values and trading session are synthetic; no CTP or real orders, and **not a production G5 dataset**.

Commands: `.venv/bin/python script/benchmark_iv_chain.py --options 12 --events 72 --seed 20261123` and `.venv/bin/python script/benchmark_iv_chain.py --options 24 --events 144 --seed 20261123`.

| Options / events | Mode | Sync ingress p95 | Worker ingress p95 | Sync event→risk-ready p95 | Worker event→risk-ready p95 |
|---|---|---:|---:|---:|---:|
| 12 / 72 | Paced | 10.071 ms | 0.398 ms | 10.088 ms | 33.824 ms |
| 24 / 144 | Paced | 24.543 ms | 0.790 ms | 24.583 ms | 76.352 ms |
| 12 / 72 | Burst | 9.915 ms | 0.339 ms | Not separately measured | Not separately measured |
| 24 / 144 | Burst | 24.038 ms | 0.593 ms | Not separately measured | Not separately measured |

**Burst interpretation:** For 24/144, synchronous feed processing took 910.02 ms versus Worker ingress 30.99 ms, **but Worker required another 58.59 ms to drain pending work**. Worker recorded 933 superseded requests, 15 stale responses, 0 overflows, and all 24 options risk-ready after drain. Ingress throughput is **not** end-to-end actionable risk throughput. Worker and sync paths also use different IV numerical implementations (structured full-precision solver vs legacy Brent), so the comparison does not isolate IPC cost or establish net computation speedup.

**Offline verification after these changes:** `.venv/bin/python -m pytest tests -q` **238 passed, 4 third-party warnings at that earlier checkpoint**; `.venv/bin/ruff check .`, `.venv/bin/mypy vnpy_optionmaster`, `uv pip check --python .venv/bin/python`, `uv build`, `.venv/bin/python -m pytest tests/test_build_artifacts.py -q` (**3 passed**), and `git diff --check` all passed in the resumed local Session.

**Remaining barriers:** No external real-data replay, independent G5 thresholds, CPU/RSS capacity qualification, production rollback drill, Q45/N/W/day-source contract, CTP/Windows gateway qualification or live-trading permission. Single Worker reduces event-loop blocking but increases paced event-to-ready tail latency. Its numerical/operational performance improvement remains **UNVERIFIED**; Worker stays opt-in and reconnect auto-release remains disabled.

## Final offline review and post-fix validation (2026-11-23)

**Review artifact:** `docs/optionmaster-iv-phase1-review-20261123.md`. Both Standards and Spec axes were examined directly against source code, `AGENTS.md`, the original Spec and offline tests. This is a sequential review, not an independent parallel-subagent or production audit.

**Additional verified fixes:** (1) an unexpectedly stopped or explicitly closed Worker Runtime cannot restart with stale dispatcher state; a new Runtime is mandatory for a new generation; (2) both Black-76 and Black-Scholes pass startup numerical selftests while the operational LRU starts genuinely cold (test observed the previous incorrect 3 hits versus 2 expected); (3) Electronic Eye direct send now requires `trading_active` and fresh option/underlying risk at the order boundary; (4) after a failed-close cancellation, operator audit cannot silently restart algorithmic trading. Each flaw was first reproduced by a focused failing test, then repaired.

**Latest full local verification against the final edited source:**

- `.venv/bin/python -m pytest tests -q`: **241 passed, 4 pandas deprecation warnings**.
- `.venv/bin/ruff check .`: **PASS**.
- `.venv/bin/mypy vnpy_optionmaster`: **PASS**, 25 source files.
- `uv pip check --python .venv/bin/python`: **PASS**, 67 packages.
- `uv build`: **PASS**; universal wheel and source distribution.
- `.venv/bin/python -m pytest tests/test_build_artifacts.py -q`: **3 passed**.

**Machine-readable auxiliary run:** `docs/optionmaster-iv-chain-benchmark-synthetic-20261123.json` records a separate 24-option / 96-event seeded offline chain benchmark. Its paced event-to-risk-ready p95 was **24.382 ms sync vs 89.187 ms Worker**; burst ingress p95 was **29.183 ms sync vs 0.957 ms Worker**, with **84.94 ms** additional Worker drain. Both finished with 24/24 risk-ready; Worker RSS was approximately **146,912 KiB sampled**, not a high-water proof. The earlier 24/144 result remains a separate exploratory run; neither is production capacity evidence.

**Earlier Spec defect (HIGH, now addressed for actionable risk):** Per-option Worker responses still update display-oriented `portfolio.pos_delta` incrementally, but the new `PortfolioRiskSnapshot` is actionable only when all necessary held dependencies are current; the hedge sender revalidates it. UI presentation of aggregate validity and production readiness remain unverified.

**Final Go/No-Go:** **NO-GO** for Worker production activation and reconnect auto-release; keep the original synchronous Brent default. Q45/N/W/source-trading-day policy, UI risk-readiness presentation, representative exchange replay, approved latency/CPU/RSS targets, rollback exercise and platform/CTP acceptance remain open. An offline-only actionable portfolio risk epoch has now been added and tested; this does not prove production readiness. Nothing has been committed, published, deployed or traded.

## P1-06 focused remediation — portfolio actionable epoch (2026-11-23)

**Red observed:** An actual six-option portfolio with four held positions was run through the isolated Worker: after one current held-option IV arrived, `portfolio.pos_delta` had already changed while only 1 of 4 held risk snapshots was fresh. The former code had no actionable aggregate status. Two new tests first failed with `AttributeError` for the missing contract.

**Green implementation:** `risk_safety.portfolio_risk_identity` checks all held option Greeks, underlying/option source quote freshness, generations, model inputs, holdings and nonzero underlying positions. `PortfolioData` retains intermediate numeric totals for display but publishes `PortfolioRiskSnapshot` only after all held dependencies are current. The `actionable_risk_snapshot` property rechecks the epoch dynamically and returns `None` immediately when holdings, pricing inputs, quotes or risk flags change. `OptionHedgeEngine.run` requires that snapshot on real PortfolioData instances, and rechecks it immediately before actual outbound sends. Legacy isolated test fixtures keep their prior per-option safety contract.

**Evidence:** `tests/test_iv_portfolio_epoch.py` covers sync epoch invalidation, partial Worker completion and direct mocked hedge-send blocking; `244 passed, 4 pandas third-party warnings` in full `pytest tests -q`. Ruff, mypy (25 source files), `uv pip check` (67 packages), `uv build`, wheel isolation (3 passed), and `git diff --check` all PASS.

**24 options / 144 events after safety change, synthetic only:** Paced event-to-risk-ready p95 **20.339 ms sync vs 73.947 ms Worker**; paced ingress p95 **20.313 ms vs 0.787 ms**. Burst ingress p95 **20.181 ms vs 0.606 ms**; Worker drain **66.22 ms**. Both paths finished 24/24 ready; Worker recorded 928 superseded requests and 18 stale results. This is one host-dependent run and is NOT an approved G5 benchmark.

**Remaining:** UI consumers still read display-only numeric totals and have not been separately migrated to a visible aggregate-ready flag. Q45/N/W, original source-time/official-calendar proof, representative exchange-market replay, G5 latency/resource targets, rollback and CTP/Windows production gates remain unresolved. Worker stays opt-in and source-switch auto-release stays disabled.

## Final P1-06 chain-aggregation invariant and evidence (2026-11-23)

**Second regression, red then green:** Updating a held option's net position and own position Greeks while leaving its ChainData aggregate stale still allowed a portfolio actionable snapshot. An additional targeted regression reproduced this failure. `portfolio_risk_identity` now checks that each held option's position Greeks equal its current theoretical Greeks times its net position; underlying Delta similarly matches its position; the portfolio-level Delta/Gamma/Theta/Vega must match the direct sum of current positions, rather than trusting a possibly stale chain aggregate.

**Tests:** Four tests in `tests/test_iv_portfolio_epoch.py` now cover missing/invalid epoch, partial Worker commits, stale chain exposure sums and real mocked hedge gateway rejection. Full `.venv/bin/python -m pytest tests -q`: **245 passed; 4 third-party pandas warnings**. Ruff, mypy (25 source files), `uv pip check` (67 packages), `uv build`, artifact isolation (3 passed) and `git diff --check`: PASS.

**Latest separate 24-option / 144-event seeded synthetic rerun:** paced event-to-risk-ready p95 **20.969 ms sync / 68.395 ms Worker**; paced ingress p95 **20.932 / 0.708 ms**; burst ingress p95 **21.026 / 0.629 ms** with Worker drain **67.70 ms**, 934 superseded requests, 14 stale responses; all 24 options ready after drain. This result is neither production load capacity nor a G5 success, and Worker remains slower for per-Tick risk readiness.

**Decision:** P1-06 current actionable aggregate is verified at the covered offline seams. Intermediate UI numbers are still display-only without a separately audited visible readiness indicator. **G3/G5 remain NO-GO**, and Q45/N/W, real source data/replay, CTP/Windows, rollout approvals and rollback exercise remain open. No commit, push, PR, deploy or trading action.

## Greeks Monitor portfolio risk-readiness indicator (2026-11-23)

**Finding and change:** The existing `OptionGreeksMonitor` renders portfolio Delta/Gamma/Theta/Vega even when an asynchronous Worker has only completed part of its aggregate. Before this revision there was no visible distinction between intermediate display Greeks and a current actionable `PortfolioRiskSnapshot`.

The Greeks monitor now has a `风险状态` column: `快照有效` denotes that the aggregate passed current dependency, generation and exposure-sum checks; this does **not** authorize orders or bypass exchange/price/session checks. `仅供展示` identifies partial/stale/invalid data which cannot be used directly for auto-hedging. A tooltip explains the distinction and the status color differs; raw Greeks remain visible. An independent `EVENT_TIMER` refresh handles Worker completion and quote expiry without requiring an underlying Tick. Only the portfolio row receives the aggregate-ready assertion.

**TDD and verification:** Two focused tests failed on the prechange UI (status column and timer update missing); the new `tests/test_iv_greeks_ui_readiness.py` now has 3 passing tests, covering rendering, timer refresh and listener registration. Full `.venv/bin/python -m pytest tests -q` **248 passed with 4 third-party pandas warnings**. Ruff, mypy (25 source files), `uv pip check` (67 packages), `uv build`, isolated artifact tests (3 passed), and `git diff --check`: PASS.

**Limits:** The UI tests use Qt cell/event doubles, not a rendered native desktop/Windows acceptance. No trading or production data was accessed. Worker remains offline opt-in, synchronous Brent stays the default, and automatic reconnect release stays disabled. Q45/N/W, authoritative day/source proof, G3/G5 metrics, CPU/RSS capacity, rollback and CTP/Windows acceptance remain NO-GO.

## Structured IV scalar hot-path optimization (2026-11-23)

**Approved local optimization:** Profiled 180 cases of Black-76/Black-Scholes safeguarded Newton inversion; 176 valid, 2 no-solution and 2 iteration-limit failures. Original success iteration median 18, max 34. In 360 profiler calls, repeated SciPy stats normal CDF/PDF wrappers dominated cost. A new optional fused scalar `price_vega_function` in `pricing/iv_solver.py` and private model-specific `_price_vega_for_iv` in Black-76/Black-Scholes reuse d1 and call `scipy.special.ndtr` plus analytic normal PDF. Public legacy price/Greeks and Brent inversion, numerical tolerances and fail-closed status rules are unchanged.

**Correctness evidence:** Test-first regression in `tests/test_iv_solver_fastpath.py` (initially red, then green). All 180 before/after reference scenarios matched status **and iteration count**; successful IV max absolute deviation **1.017e-13**. Latest full suite **468 passed, 4 pandas warnings**. Ruff, mypy (25 source files), `uv pip check` (67 packages), `uv build`, wheel isolation (3 passed), and `git diff --check`: PASS.

**Synthetic performance observed:** In prior 24-request microbenchmark, structured direct cold median 1.889 ms and Worker cold median 2.304 ms (0.5-ms polling). After change first rerun 0.177 ms direct and 0.669 ms Worker; separately archived after rerun 0.173 and 0.671 ms. Latest 24-option/144-Tick paced risk-ready p95 **19.903 ms sync Brent / 21.690 ms Worker**, versus prior separate run **20.969 / 68.395 ms**. These measurements are host-dependent and not a paired production result. Worker **still has not demonstrated superiority** on the trading risk-ready p95.

**Evidence:** `docs/optionmaster-iv-solver-fastpath-optimization-20261123.md`; `docs/optionmaster-iv-worker-attribution-scalar-fast-20261123.json`; `docs/optionmaster-iv-chain-benchmark-scalar-fast-20261123.json`. The earlier benchmark note above is historical pre-fastpath evidence, not the current implementation cost.

**Release boundary unchanged:** G3 Q45/N/W/official trading day/source proof BLOCKED; G5 real exchange replay/approved latency+CPU+RSS targets/rollback NO-GO; CTP/Windows and native Qt production qualification unverified. Worker stays opt-in offline, synchronous Brent default. No commit/push/PR/deploy/real orders.

## G5 repeated synthetic chain evidence (2026-11-23)

Executed nine further bounded synthetic full-chain invocations: 6/36, 12/72 and 24/144 option/Tick sizes, seeds 20261123/24/25 (four modes per invocation). The median of the three run-level paced *actionable* risk-ready p95s was **5.188 vs 5.576 ms** (6 options), **9.688 vs 10.424 ms** (12), **21.343 vs 26.956 ms** (24), sync vs Worker. At 24 options the Worker p95 was **21.694–49.316 ms** compared with sync **20.631–23.403 ms**. The 24-option Worker p99 median was **46.436 ms**, sync **22.055 ms**. Burst ingress remains faster with Worker, but not equivalent to actionable risk readiness. All **36** mode executions finished fully risk-ready, all paced events met the test wait bound, and no scheduler overflows occurred; burst drops/supersessions/stale replies preserve fail-closed behavior. Worker paced pending peak reached **22** in 24 option loads, reflecting per-contract fanout and one in-flight request. CPU/RSS estimates are nonisolated, startup-inclusive or sampled and do not establish capacity.

**Repeatability conclusion:** Previous single-run 21.69ms Worker p95 was not a stable workload-scale conclusion. On several synthetic seeds the Worker tail became substantially worse, so **G5 remains NO-GO** with no live-source replay, owner-approved SLO or rollback exercise. No recorded exchange Tick dataset found under project after excluding virtual environments/caches. Report: `docs/optionmaster-iv-g5-repeated-synthetic-20261123.md`; raw seed matrices `docs/optionmaster-iv-g5-repeat-{6,12,24}opt-20261123.json`. No business code changed during this repeated benchmark slice.

## G5 fair numerical path: in-process structured Newton versus process Worker (2026-11-23)

To remove the earlier Brent-versus-Newton confound, added *test-only* `script/benchmark_iv_fair.py` with an in-process numeric adapter sharing the same `OptionIVRuntime`, `IVDispatcher`, bounded `WorkerCache`, `calculate_batch` and `IVResult` acceptance/owner commit. `script/benchmark_iv_chain.py` gained a backwards-compatible `readiness_scope="portfolio"` option; its original default affected-contract metric is untouched. Both experimental transports require the current **complete portfolio actionable risk snapshot** for readiness, which is not the same as outbound order permission.

Nine paired synthetic groups (6/12/24 option chains × seeds 20261123/24/25; paced and burst each) showed median-of-run p95 complete-portfolio risk-ready **1.926 vs 5.403 ms**, **3.831 vs 9.916 ms**, and **8.234 vs 19.945 ms** for in-process vs isolated Worker, respectively. 24-option median p99 **8.919 vs 23.378 ms**. Worker burst ingress p95 remains faster, e.g. **0.668 vs 7.757 ms** at 24 options. Each of 36 full-chain runs ended complete risk-ready with zero scheduler overflows, but interim states were not automatically authorized to trade. Single-contract micro (24 different snapshots + warm repeats) had 72/72 IV side cache hits and reference-identical IVResults in both modes; it is limited by fixed polling and cannot measure pure IPC separately.

Four additional test-first tests in `tests/test_iv_fair_benchmark.py` are green. Full project suite **472 passed, 4 external pandas warnings**, Ruff/mypy/dependencies/uv build/3 wheel isolation checks/Git diff whitespace passed. Evidence: `docs/optionmaster-iv-fair-solver-benchmark-20261123.md`, machine-readable `docs/optionmaster-iv-fair-solver-summary-20261123.json` and nine run files `docs/optionmaster-iv-fair-solver-{6,12,24}opt-{20261123,20261124,20261125}.json`. No business code or production Worker default was changed. **G5 remains NO-GO**; G3 Q45/N/W and real-source/Windows/CTP/rollback gates still unapproved.

## G5 next candidate — grouped numerical IPC proof of concept (2026-11-23)

Test-only `script/benchmark_iv_batch_prototype.py` groups up to 24 existing immutable `IVWorkRequest` messages in one spawned-process IPC round trip and runs the unchanged structured safeguarded Newton `calculate_batch` with bounded `WorkerCache`; serial baseline is the actual `SingleIVWorker`. It does **not** alter the production Worker, enqueue policy, single-writer risk commit or sender, and the returned numerical group is **not** an actionable portfolio risk snapshot.

Three shuffled-order seeds for each of 6, 12, 24 options showed median cold total-response times serial versus full grouping: **4.267 / 2.253 ms (1.89×)**, **8.273 / 2.941 ms (2.81×)**, **16.702 / 5.364 ms (3.11×)**. With 24 options, bounded batch size 8 yielded **5.882 ms** and full batch 24 **5.364 ms**; latency gains diminish with larger batches, while priority/staleness/head-of-line semantics remain unproven. All warm runs had full exact-input cache matches and reference-equal individual `IVResult`s. Six new test-first cases cover numerical parity, one stale/invalid member fail-closed, generation and capacity limits.

Latest full `.venv/bin/python -m pytest tests -q` **478 passed with 4 pandas warnings**; Ruff, mypy (25 source files), uv dependency check, uv build, 3 wheel isolation tests, Git diff --check passed. Source report `docs/optionmaster-iv-batch-ipc-prototype-20261123.md`; summary JSON and 9 raw files `docs/optionmaster-iv-batch-ipc-{6,12,24}opt-{20261123,20261124,20261125}.json`. **G5 remains NO-GO** until full OptionEngine/PortfolioData portfolio actionability, real-source replay, preapproved SLO, rollback and CTP/Windows acceptance are separately proven. Q45/N/W stay unresolved; production synchronous Brent default and auto-release OFF remain unchanged.

## G5 bounded microbatch — real owner/portfolio offline path (2026-11-23)

A second, *test-only* integration experiment (`script/benchmark_iv_microbatch_integration.py`) makes 4/8-item batches from the same `LatestWinsScheduler` but runs the actual `OptionIVRuntime.submit` capture, `commit_option_iv`, Greek and `PortfolioData.actionable_risk_snapshot` read-time validation. The batch subprocess comes from `script/benchmark_iv_batch_prototype.py`, not production `SingleIVWorker`. No registered runtime, pricing backend, live order or source policy was modified.

Three seeds × 6/12/24 options × batch size 4/8 gave **18 synthetic invocations**, each with paced and burst modes, and all **36 path-end full portfolios** actionable with zero scheduler overflow and zero paced wait failure. Median of three within-run complete-portfolio risk-ready p95s (ms): **6: 5.446 / 4.129; 12: 7.823 / 7.240; 24: 27.983 / 11.357**, batch4 / batch8. A contemporaneous separately rerun 24-option single-request Worker baseline was **20.430 ms**. Batch4 underperformed the baseline at 24; batch8 outperformed, but tests are non-randomized synthetic sequential measurements, not G5 production proof. Startup, CPU/RSS and real Tick provenance need independent qualification.

7 new tests in `tests/test_iv_microbatch_integration.py` are red→green: owner-held risk priority/fairness, latest-wins stale member discard with unaffected sibling, malformed response/timeout fail-closed, Worker process death immediate portfolio risk revocation, and full-chain batch4/8 actionability. Full project pytest **485 passed, 4 pandas warnings**; Ruff, mypy (25 files), dependency checks, uv build, 3 wheel tests and diff check PASS. Evidence report: `docs/optionmaster-iv-microbatch-owner-integrated-20261123.md`; summary JSON and 18 group/3 control raw JSON files.

**Disposition:** Test-only integrated proof complete. Neither this numerical experiment nor the original isolated IPC prototype authorizes a production batch Worker; CTP/Windows, real source replay, approved tail-latency/resource SLO, in-flight priority, production numerical selftest and rollback are not accepted. **G5 NO-GO; G3 Q45/N/W BLOCKED**. Worker opt-in and synchronous Brent default unchanged; no commit/push/PR/deploy/CTP/orders.

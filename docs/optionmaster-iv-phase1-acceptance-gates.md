# OptionMaster IV Phase 1 — Release and Acceptance Gate Matrix

**Evidence date:** 2026-11-23

**Goal:** `wc_goal_60H7BlqG-tRBeJAf`

**Workflow Session:** `wc_sess_eb6OiX8SQJ8lLPcr`

**Base:** `0ec421f9` on `fix/optionmaster-audit-20261122`; local uncommitted working-tree modifications, not a released revision

**Source:** `docs/optionmaster-iv-performance-risk-snapshot-phase1.md`, implementation status and review

**Decision:** **NO-GO for Worker production activation, automatic reconnect release and live trading**

## Purpose and scope

This is an **evidence reconciliation record**, not an authorization, production readiness certificate, operational runbook or approved SLO. It distinguishes the finished local implementation/testing milestone from external approvals that have NOT occurred. Synthetic sessions, test Tick data and mocked order gateways cannot be substituted for real exchange/CTP proofs.

## Gate matrix

| Gate | Current result | Required independent evidence | Action until approved |
|---|---|---|---|
| P1-01–P1-05 IV numerical and Worker contracts | **OFFLINE PASS** | Confirm numerical reference set and approved tolerances on target environments; verify lifecycle under fault injection | Worker remains opt-in/offline only |
| P1-06 portfolio actionable aggregate | **OFFLINE PASS** | `PortfolioRiskSnapshot` all-held-dependency identity, sum consistency, partial Worker commit and outbound hedge tests; further native UI/Windows test needed | Display values cannot confer order permission |
| P1-07 execution-boundary safety | **OFFLINE PASS, PRODUCTION UNVERIFIED** | Real CTP order/fill/cancel race + operator reconciliation evidence, verified trading calendar and gateway boundary proof | Keep existing hard gates and failed-close audit |
| P1 UI risk readiness | **OFFLINE PASS, NATIVE QA OPEN** | Greeks-monitor `风险状态` readout in actual supported Qt/Windows desktop and asynchronous/expires cases | Explicitly show `仅供展示` when aggregate not actionable |
| G3 / Q45, N/W reconnect recovery | **BLOCKED / NO AUTO RELEASE** | Approved W clock semantics; positive uniform N; finite W/window algorithm; official exchange trading day; adapter session/identity; source replay threat tests | `recovery_quarantined` stays latched; operator reconciliation remains necessary |
| G5 / performance | **NO-GO** | Representative recorded market feed covering option+underlying fanout, controlled before/after repeat runs with independently approved p95/p99, CPU/RSS/queue/latency SLO and rollback criteria | Keep legacy synchronous Brent default |
| Platform / gateway | **BLOCKED** | Windows qualification, actual CTP subscription and reconnect chronology, order/trade/cancel state reconciliation, gateway approval | No live gateway connection or orders |
| Release / rollback | **NOT AUTHORIZED** | Owner-approved fallback/rollback drill, artifact/revision verification, security/code review, separate commit/push/deploy permissions | No remote writes, releases, deployments or live enablement |

## Observed local verification (not production evidence)

- Full suite: `.venv/bin/python -m pytest tests -q` → **248 passed, 4 third-party pandas warnings**; no failed test.
- Static and build: Ruff PASS; mypy PASS (25 source files); `uv pip check --python .venv/bin/python` PASS (67 packages); `uv build` PASS; isolated wheel tests 3 passed; `git diff --check` PASS.
- Greeks monitor: three focused tests passed. Two red failures established the absence of status and timer refresh before code changes. Current statuses are **`快照有效`** and **`仅供展示`**, with explanatory tooltip and `EVENT_TIMER` refresh. This is cell/event-double testing, not visual native GUI validation.
- Full-portfolio identity: four focused regression tests established non-actionable intermediate Worker updates and rejected stale chain aggregate sums; Delta Hedge checks the current actionable snapshot at the outbound boundary.
- All changes remain uncommitted. There was no CTP connectivity, exchange data subscription, physical order, remote publish or deployment.

## G5 benchmark evidence and the actual trade-off

Single seeded synthetic scenario, macOS ARM64 / Python 3.13, 24 option contracts and 144 chronological mixed option/underlying events (test-only sessions; no real gateway). Earlier rerun after P1-06 chain consistency:

| Metric (p95) | Sync Brent path | Opt-in Worker path | Interpretation |
|---|---:|---:|---|
| Paced Tick → actionable risk ready | **20.969 ms** | **68.395 ms** | Worker **slower** at the primary risk-readiness metric |
| Paced market ingress | 20.932 ms | **0.708 ms** | Worker reduces event-loop blocking |
| Burst market ingress | 21.026 ms | **0.629 ms** | Worker accepts events faster, not necessarily faster actionable risk |

- Worker burst drain (after ingress): **67.70 ms**, with **934** pending requests superseded and **14** stale responses rejected; all 24 options were risk-ready after drain.
- Different numerical inversion paths were exercised (legacy Brent versus full-precision Worker), so this comparison does not isolate the subprocess/IPC overhead. It is neither an accepted production baseline nor a controlled solver parity performance claim.
- Worker startup, cold cache, memory high-water, production feed characteristics and SLO thresholds are not fully measured/approved. Do not infer capacity or expected profit from these times.

## Unresolved decisions and prerequisites

**G3 (cannot automatically enable):**

1. **Q45 PENDING:** choose W clock: monotonic elapsed, exchange-session elapsed, or dual restriction. The Grill explicitly did not decide this.
2. Freeze approved **N > 0** and finite **W**, precise sliding/restart window semantics, midnight/night-session official Trading Day, session boundary and config-change rules.
3. Verify actual gateway Tick identity, chronology and replay handling. Q40=A operational N-count recovery is **not** proof that buffered replay has ended. Existing stricter timestamp and source-switch safeguards remain.
4. Confirm who approves reactivation and auditable reconciliation on source-switch and failed-close paths. There is **no** approved auto-release patch here.

**G5 (cannot claim performance improvement):**

1. Supply representative **offline recorded** Tick streams or approved anonymized exchange-grade fixtures covering liquid/illiquid strikes, option/underlying fanout, burst/quiet periods, source transitions and different chain sizes.
2. Freeze benchmark methodology (paired stream, cold/warm cache, worker startups, measurement location, event-to-actionable-risk vs ingress vs final drain, CPU and RSS sample/high water, deterministic machine/environment).
3. Freeze numerical quality and operational performance **acceptance thresholds** in advance. Use p50/p95/p99, queue overflow/cancellation, Superseded/Stale counts, cost of IPC, and correctness/Fail-Closed guardrails.
4. Demonstrate a safe fallback/rollback with no hot model switch during trading. If Worker cannot meet risk SLOs, reject production activation even if ingress is substantially faster.

**Production (independent authorization required):**

1. Verified exchange calendars and real CTP contract/session semantics, Windows install and run acceptance.
2. Proved exchange/CTP order-trade-cancel race handling, immutable order identity, failed-close operator audit, source-switch reconciliation.
3. Separate approval for Git commit/push/PR, release/deploy, real gateway connectivity, and any real trading order. None has been granted in this Goal.

## Stop conditions

- Any failed numerical or risk dependency makes the relevant normal automated order ineligible, even if the UI still shows last-known Greek values.
- A Worker cache hit or locally received Tick does not silently restore Risk-Ready, bypass source timestamp checks, or lift reconnect quarantine.
- The GUI status **`快照有效`** is only the aggregate numerical identity assertion. It is **not** a complete trade authorization or permission to ignore session, book, limits, orders and account gates.
- Do not switch away from the synchronous Brent default or enable Q40/N/W auto-release without approved gate evidence and operator authority.

## Additional G5 attribution: solver versus IPC (2026-11-23)

Reproducible purely synthetic diagnostic: `script/benchmark_iv_attribution.py`, JSON `docs/optionmaster-iv-worker-attribution-synthetic-20261123.json`, and interpretation `docs/optionmaster-iv-worker-performance-attribution-20261123.md`.

For 24 requests with three IV sides each, the median **cold** duration was 0.720 ms/request for the legacy rounded Brent path, 1.889 ms/request for the *new structured* solver run directly, 2.304 ms/request for the identical new solver in a spawned Worker polled at 0.5 ms, and 1.836 ms/request with more aggressive polling. Repeated-input structured cache lookup was ~0.002 ms direct versus ~0.661 ms Worker with 0.5 ms polling; the fast polling variant measured ~0.070 ms but polled far more often. The legacy API has no LRU, so its second pass is not a cache hit.

**Inference limited:** Changing both solver and process invalidates a simple sync-vs-Worker IPC attribution; one in-flight request at a time and portfolio fanout further delay all-held-contract risk readiness. More aggressive polling has potential CPU/fairness costs, not approved for production. G5 retains **NO-GO** until real representative offline feeds, matched numerical acceptance and approved performance/resource targets are available. No changes to Q45/N/W or default trading path.

## G5 follow-up: safe scalar hot-path optimization (2026-11-23)

Local implementation: private Black-76 / Black-Scholes scalar price+Vega kernel wired into the structured Safeguarded Newton solver, no change to the numerical acceptance contract, public legacy Brent/pricing APIs, Worker topology or default trading path. All 180 reference scenarios preserved the same result status and iteration count with successful IV differences at most **1.017e-13**; full **468-test** regression passed; Ruff/mypy/uv dependency/build/artifact tests passed.

Previously reported structured direct cold median 1.889 ms/request was reduced to **0.173–0.177 ms/request** on subsequent same-host 24-request synthetic trials. Worker round-trip cold median went from 2.304 ms to **0.669–0.671 ms**. Full-chain paced risk-ready p95 is still **21.690 ms Worker versus 19.903 ms sync Brent** on the archived latest run, so **G5 remains NO-GO**. Previous and after runs were not controlled paired production samples.

Report: `docs/optionmaster-iv-solver-fastpath-optimization-20261123.md`; JSON: `docs/optionmaster-iv-worker-attribution-scalar-fast-20261123.json` and `docs/optionmaster-iv-chain-benchmark-scalar-fast-20261123.json`.

## G5 follow-up: multi-seed, multi-size synthetic replay (2026-11-23)

Nine invocations of the same offline full-chain script, three seeds each for 6/36, 12/72 and 24/144 option/Tick sizes; 36 path executions total (paced/burst × sync/Worker). For each size, medians of per-seed within-run **paced actionable risk-ready p95** were: **5.188 vs 5.576 ms** (6 options, sync vs Worker); **9.688 vs 10.424 ms** (12); **21.343 vs 26.956 ms** (24). In the 24-option group, Worker p95 varied **21.694–49.316 ms** across three seeds versus **20.631–23.403 ms** sync; 24-option Worker median per-run p99 was **46.436 ms**, sync **22.055 ms**.

Worker **burst ingress** p95 was lower at every size (6: 0.261 vs 5.789 ms; 12: 0.393 vs 11.631 ms; 24: 0.835 vs 20.416 ms, Worker vs sync) but burst ingest does **not** mean risk-ready during bursts. All 36 final paths had every option risk ready; no scheduler overflow; large burst superseded and stale-reply counts were expected fail-closed behavior, not new trades or order cancellations. 24-option Worker paced pending peak **22** and burst pending peak **24** reflect the one-in-flight design and synthetic fanout.

**Conclusion strengthened:** The scalar fast-path improves numerical work, but **Worker aggregate tail latency varies and does not consistently beat legacy sync**. Three runs per size are insufficient for any confidence interval or approved release percentile. Worker CPU/RSS observations include startup, process imports, sampled rather than true high-water RSS, and are not independently approved capacity metrics. No eligible recorded real Tick capture was found in the working project (excluding virtual environments/caches). The *only* outcome authorized here is documentation and a continued **G5 NO-GO**.

Full report: `docs/optionmaster-iv-g5-repeated-synthetic-20261123.md`, backed by `docs/optionmaster-iv-g5-repeat-{6,12,24}opt-20261123.json`.

## G5 follow-up: identical structured solver, same cache, in-process vs Worker (2026-11-23)

New test-only direct/IPC comparison with exactly the same structured Safeguarded Newton `calculate_batch` solver, `IVResult`, exact-input `WorkerCache`, single-in-flight `IVDispatcher`, bounded Latest-Wins, owner commit and **complete portfolio actionable risk snapshot** criterion. No changes to actual pricing/order/runtime defaults. The old affected-contract benchmark metric must not be conflated with the new complete-portfolio readiness metric.

Three seeds per size (9 paired groups, 36 full-chain paths): median of run-level paced **complete portfolio** risk-ready p95, in-process vs isolated Worker: 6 options **1.926 / 5.403 ms**, 12 options **3.831 / 9.916 ms**, 24 options **8.234 / 19.945 ms**. At 24 options p99 medians were **8.919 / 23.378 ms**. Worker burst Tick-ingress p95 was faster at 24 options (**0.668 vs 7.757 ms**) but this is not risk actionability.

One logical contract with 24 distinct inputs and exact-input repeats gave 72/72 warm-cache hits in **both** transports and reference-equal Bid/Ask/Mid `IVResult` values. The microbenchmark has a 0.5-ms polling floor and fixed run order: it **cannot** isolate pure IPC serialization or establish production tail SLAs. All 36 full-chain executions ended risk-ready with zero scheduler overflows; no live CTP or real orders.

Latest full project regression **472 passed, 4 pandas warnings** plus Ruff, mypy, dependency, wheel build/isolation and Git whitespace checks passed. Evidence report: `docs/optionmaster-iv-fair-solver-benchmark-20261123.md`; summary: `docs/optionmaster-iv-fair-solver-summary-20261123.json`, nine raw run JSONs. **G5 remains NO-GO** until real recorded feed, approved performance/resource budgets and rollback/CTP qualification.

## G5 research: isolated numerical batch IPC prototype (2026-11-23)

A local, disposable spawned-process prototype groups **existing `IVWorkRequest` messages** into bounded `BatchEnvelope`s, calls the **unchanged** `calculate_batch`/exact-input `WorkerCache` serially inside one process, and returns each original `IVWorkResult` together. The baseline is the existing `SingleIVWorker` one-request/IPC. This was **not** integrated into `IVDispatcher`, `OptionIVRuntime`, portfolio risk commits, trading/session logic or production settings.

Across 6/12/24 synthetic options and three order-only random seeds per size (nine datasets), the median **cold numerical-response-completion time** improved when one IPC request grouped the entire set: **6: 4.267→2.253 ms (1.89×)**, **12: 8.273→2.941 ms (2.81×)**, **24: 16.702→5.364 ms (3.11×)**. A bounded 8-item group at 24 options returned in **5.882 ms (2.84×)**. The measurements exclude owner-side Greek/portfolio risk mutation and cannot establish G5 risk-ready p95/p99, production CPU/RSS or external source correctness.

Six new test-first `tests/test_iv_batch_prototype.py` cases now pass, covering identical Bid/Ask/Mid `IVResult`s, warm cache matches, one stale/invalid member rejecting numerical release, bounded group size, generation and duplicate symbol rejection. Full project suite **478 passed, 4 third-party pandas warnings**; Ruff, mypy, dependency check, wheel build, wheel isolation and Git whitespace checks PASS.

Full report: `docs/optionmaster-iv-batch-ipc-prototype-20261123.md`; normalized evidence: `docs/optionmaster-iv-batch-ipc-summary-20261123.json`; nine source JSON runs: `docs/optionmaster-iv-batch-ipc-{6,12,24}opt-{20261123,20261124,20261125}.json`.

**Decision remains NO-GO:** a bounded microbatch design with genuine latest-wins, held-position priority, per-option quote epoch checks, full portfolio owner-only actionability and head-of-line/timeout/overflow/recovery tests would require separate authorization. G3 Q45/N/W and verified source/trading-day semantics remain blocked. This prototype is **not** live Worker implementation approval.

## G5 research: owner-integrated 4/8 bounded microbatch replay (2026-11-23)

**New evidence:** A test-only `MicrobatchIVRuntime` swaps only the grouped subprocess and dispatcher while reusing the actual `OptionIVRuntime` owner capture, `commit_option_iv`, `LatestWinsScheduler`, quote/model version checks, Greek aggregation and complete `PortfolioData.actionable_risk_snapshot`. This is **not** a production Worker protocol change or real exchange/CTP acceptance.

Three synthetic seeds each for 6/12/24 option chains and caps 4/8 (18 invocations, 36 paced/burst paths). Median of within-run **full actionable portfolio risk-ready p95** (ms): **6: batch4 5.446 / batch8 4.129; 12: 7.823 / 7.240; 24: 27.983 / 11.357**. Historical single-request Worker p95 medians were 5.403/9.916/19.945 ms; a separate fresh 24-option control returned **20.430 ms**. These are sequential synthetic runs, **not randomized paired comparisons**.

At 24 options, batch4 was **slower than single-request** despite fewer IPC messages; batch8 was faster, but the cause of batch4 tail inflation is unisolated. Fast burst Tick ingress does not grant Risk-Ready. All 36 integrated path-end snapshots were actionable; stale members were discarded and genuine Worker death, malformed reply and deadline rejection were tested fail-closed. Full regression **485 passed, 4 external pandas warnings**; Ruff, mypy, dependency check, wheel build/isolation and Git diff pass.

**No production Go:** The test-only batch subprocess lacks production startup numerical selftest and real traffic, and a group in flight cannot preempt for newly urgent held requests. Batch8 is an investigation candidate only; G5 still lacks representative recorded market data, approved risk-ready/queue/CPU/RSS SLO, Windows/CTP, rollback and production release approval. G3 Q45/N/W unresolved, auto-release OFF.

Full report: `docs/optionmaster-iv-microbatch-owner-integrated-20261123.md`; normalized JSON: `docs/optionmaster-iv-integrated-microbatch-summary-20261123.json`; 18 raw group files plus 3 fresh 24-option control files.

## G5 follow-up: measured urgent held-position HOL under injected service (2026-11-23)

The isolated `script/benchmark_iv_microbatch_urgent.py` sends a new synthetic held-position Tick during an already-running ordinary batch while using **actual owner capture/commit and full held-portfolio actionability**. Test-only Worker service includes a deliberately injected **12 ms per numerical request**: this measures the non-preemptible scheduling mechanism, **not** actual production processing speed. Three seeds × caps 1/4/8 × two arrival offsets = **18 samples**.

Median urgent quote-to-full-portfolio actionable delay (ms): at ordinary batch **start** cap1 **27.283**, cap4 **65.271**, cap8 **113.579**; at batch **midpoint** cap1 **20.183**, cap4 **39.245**, cap8 **65.424**. Associated queue-to-dispatch medians at batch start were **13.812 / 52.047 / 99.850 ms**, explaining the main effect. Every sample preserved fail-closed actionability and reached ready without queue overflow. The previous 24-option throughput test favored batch8, but it is a different workload with no injected delay; the results are **not directly comparable**. Batch1 here is also the **experimental protocol**, not production `SingleIVWorker`.

Document `docs/optionmaster-iv-g5-urgent-hol-20261123.md`; raw JSON `docs/optionmaster-iv-g5-urgent-hol-stress-20261123.json`; tests `tests/test_iv_microbatch_urgent.py` (11 targeted pass). Three urgent arrivals per cap/phase provide **no statistically defensible p95/p99**. G5 still needs representative recorded feed, urgent held-priority p95/p99/queue deadline and CPU/RSS budgets, non-injected Worker service profiles, startup selftest and rollback. **G3 and G5 remain NO-GO.**

## G5 research: no-injected numeric service and experimental adaptive batch cap (2026-11-23)

A further **script/-only** study measured the ordinary Worker numerical interval directly around the existing `calculate_group` (no artificial compute sleep), with test synchronization barrier wait excluded from the numerical interval. Three seeds × cold/warm × option-held vs three-held-underlying fanout × batch cap1/4/8 = **36 runs**. Median (N=3/cell) **cold ordinary service** for cap1/4/8: **0.257/0.826/1.541 ms**; **cold one-held Tick→full actionable portfolio** **2.897/3.804/5.158 ms**, while **three-held underlying fanout** **6.230/4.196/6.479 ms**. This is a synthetic guaranteed-arrival experiment, *not* natural production p95/p99.

A matched **8 staged ordinary requests** comparison (3 seeds × 2 cache × 2 held/fanout modes × fixed 1/4/8 or **test-only cap heuristic** = **48 runs**) finds median complete held-portfolio Risk-Ready (ms) for **fixed8 vs candidate**: cold one-held **5.412 vs 2.983**, cold three-held **6.382 vs 3.877**, warm one-held **3.590 vs 3.071**, warm three-held **5.267 vs 3.800**. Candidate proposal is **ordinary cap1 when any held position is exposed, cap4 when >=2 held requests pending, cap8 only with no held exposure**. It cannot preempt an active batch, and it has **no approved SLO**. Comparable fixed1/fixed4 outcomes sometimes match or beat candidate; three seeds do not establish tail significance. All study cases preserved full held-position actionability, with no scheduler overflow.

Evidence: `docs/optionmaster-iv-g5-numerical-adaptive-20261123.md`, `docs/optionmaster-iv-g5-numerical-hol-20261123.json`, `docs/optionmaster-iv-g5-adaptive-matched-backlog-20261123.json`. Tests: 35 focused pass; full project venv **535 passed, 4 third-party warnings**; Ruff/mypy/dependency/build/wheel isolation/diff checks PASS. **G5 production NO-GO**, G3 Q45/N/W BLOCKED, no production Worker modifications or actual CTP/Windows/real feed validation.

## Next permissible action

Before enabling any actual production batch Worker, independently approve an integration spec, recorded real-feed acceptance dataset, explicit max-batch/priority/deadline contracts, p95/p99 *complete actionable portfolio risk-ready* and CPU/RSS/rollback limits; implement/test the production numerical selftest and crash/restart contracts. Q45/N/W official trading-day/source rules remain a separate required decision. **G3/G5 production NO-GO**.

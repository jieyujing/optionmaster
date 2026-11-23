# OptionMaster IV Phase 1 — Same-Solver In-Process vs Worker Benchmark

**Date:** 2026-11-23

**Goal:** `wc_goal_60H7BlqG-tRBeJAf`

**Session:** `wc_sess_eb6OiX8SQJ8lLPcr`

**Branch/base:** `fix/optionmaster-audit-20261122` / `0ec421f` (uncommitted dirty local tree)

**Status:** Offline comparison complete; G5 **NO-GO**, no production/CTP/orders/release authorization.

## Experiment question and conclusion

Does moving the already-optimized structured full-precision Safeguarded Newton IV calculation to a separate process improve actionable portfolio risk readiness after controlling for the earlier Brent-versus-Newton numerical algorithm mismatch?

**Observed:** In this synthetic single-host benchmark the isolated Worker improved event-loop ingress responsiveness but consistently **increased the tail latency of a complete current portfolio risk snapshot**. With the same structured solver, bounded LRU, dispatcher, request/commit contracts and 0.5-ms polling policy, the median of three per-seed within-run paced p95s for 24 options was **8.234 ms in-process versus 19.945 ms Worker**. This narrows the cause to the execution/transport/concurrency path, **not pure IPC alone**, and does not establish a production performance SLO.

## Exact implementation and measurement boundaries

- `script/benchmark_iv_fair.py` introduces a **test-only** `InProcessIVWorker` and `InProcessIVRuntime`. The same owner `OptionIVRuntime`, immutable `capture_option_iv`, `IVDispatcher` (one in-flight request), bounded `LatestWinsScheduler`, exact-input `WorkerCache`, `calculate_batch`, full-precision `IVResult`, `commit_option_iv`, Greek and complete portfolio risk identity checks are used in **both** paths. The test transport executes `calculate_batch` in the main process when polled; the other transport is the existing spawn-mode `SingleIVWorker` with IPC.
- Existing `script/benchmark_iv_chain.py` received an optional `readiness_scope="portfolio"` selector; the **default `"affected"` behavior remains unchanged**. All fair-chain timings wait for `PortfolioData.actionable_risk_snapshot` (held positions, current quote/Greeks/model generations and portfolio sums), **not merely** the option tick that caused the update. A published portfolio snapshot does not grant order-sending permission.
- The full event ingress path is still `OptionEngine.process_tick_event → PortfolioData → ChainData → OptionData`. No business logic, numerical acceptance, worker topology, pricing model, risk guard, session check or trading order code was changed.
- Test-only in-memory all-open sessions and fresh **per-run** timestamp anchors; 6/12/24 synthetic options, 36/72/144 deterministic-value option+underlying Tick updates, three seeds `20261123`, `20261124`, `20261125`. The value sequence matches within each paired comparison; the *literal* wall-clock anchors/quote identity tokens differ across sequential runs, so the experiment is not an identical byte-for-byte source feed recording.
- Each size/seed runs in-process/Worker in **fixed order**, each in paced and burst modes, totaling **9 paired groups × 4 paths = 36 full-chain modes**. Per-event wait bound five seconds; both are polled with 0.5-ms sleep. Worker startup excluded from per-event latency but reported separately; CPU/RSS are observational and not isolated capacity measurements.
- `script/benchmark_iv_fair.py --micro` also runs one **logical contract** through the exact same `IVDispatcher` and `calculate_batch` with 24 distinct pricing inputs (cold) and then exact-key repeats (warm), checking **every Bid/Ask/Mid IVResult** against the same reference. Both paths had **72/72 warm cache hits**, so the numerical/caching contract is genuinely matched in that test.

## Primary result: complete actionable portfolio risk readiness

Values below are **medians of three independent within-run p95/p99 values** in milliseconds. They are not percentiles over a pooled event sample or confidence intervals.

| Options / Ticks | In-process p95 | Worker p95 | In-process p99 | Worker p99 | Paired Worker minus in-process p95 by seed |
|---|---:|---:|---:|---:|---|
| 6 / 36 | **1.926** | 5.403 | **2.001** | 5.550 | +3.538, +3.385, +3.520 ms |
| 12 / 72 | **3.831** | 9.916 | **3.937** | 10.706 | +6.567, +5.961, +6.085 ms |
| 24 / 144 | **8.234** | 19.945 | **8.919** | 23.378 | +11.711, +12.859, +11.826 ms |

**At 24 options**, the Worker p95 is around **2.42×** the in-process p95 in the median-of-three synthetic metric. The positive paired differences at every seed support a consistent local association with process/dispatch/poll execution; they do not independently identify kernel IPC as the sole cause.

## Separate metric: Tick ingress under burst conditions

| Options | In-process burst-ingress p95 | Process Worker burst-ingress p95 |
|---|---:|---:|
| 6 | 1.957 ms | **0.237 ms** |
| 12 | 3.806 ms | **0.367 ms** |
| 24 | 7.757 ms | **0.668 ms** |

Fast ingress is the direct benefit of deferring IV calculation. **It does not imply the complete portfolio was actionable during the burst**. The benchmark separately drains latest-valid pending results and asserts final complete readiness. Queue, source identity, generation and numeric safety gates remained active.

## One logical contract: same dispatch and numeric cache

For one logical option contract, 24 cold Bid/Ask/Mid snapshots followed by the same 24 exact-input snapshots:

| Mode | Cold median round-trip | Cold p95 | Warm median | Warm p95 | Warm IV side hits |
|---|---:|---:|---:|---:|---:|
| In-process compute | 0.811 ms | 0.877 ms | 0.632 ms | 0.634 ms | 72 / 72 |
| Process Worker | 0.650 ms | 0.666 ms | 0.646 ms | 0.652 ms | 72 / 72 |

All **48 / 48** outputs in each mode were valid, each comprising three reference-matched IV results. The slight cold advantage for the process Worker is **not evidence IPC is negative cost**: the driver uses a 0.5-ms polling interval and CPU scheduling differs (the child may calculate while the parent sleeps). This small microbenchmark cannot separately measure pure IPC serialization, wakeup, core allocation or cache cost. Its purpose is numerical-contract parity and single-contract overhead context; the full-chain risk tail is the decision metric.

## Correctness, limitations and unresolved release requirements

- All **36/36** paced/burst path executions reached final complete portfolio actionability and all options were current. Every paced path completed within its bounded wait; scheduler overflow was zero. The new comparison did not cause price/quote, source freshness, ownership or order logic changes.
- New `tests/test_iv_fair_benchmark.py` started red because the intended comparison module did not exist; four tests now pass, including exact `IVResult` parity and cache reuse, micro benchmark checks, complete portfolio risk readiness, and preservation of the old benchmark's default affected-contract semantics.
- Full project regression: **472 passed, 4 pandas third-party warnings**; Ruff/mypy and dependency/build/wheel isolation/diff checks passed in the authenticated project Runner.
- This is **not a randomized controlled experiment**. Three seeds, sequential mode ordering, local-only synthetic values and different per-run wall-clock anchors limit reproducibility and generalization. Source event/gateway real-times, true process CPU and RSS high water, production pricing workload/memory pressure, and Windows/CTP behavior were not verified.
- This is a **process execution path comparison**, not a mathematically isolated pure IPC measurement. Moving computation to a child also changes concurrency, scheduler wakeup, background CPU allocation and timing of owner-commit. A separate timestamp-instrumented worker protocol would be needed to apportion individual IPC stages with confidence.
- Legacy synchronous Brent is **not** part of this direct A/B experiment. Both A and B used structured high-precision Safeguarded Newton and identical numerical acceptance/caching code. Results should **not** be numerically mixed with previous `sync Brent vs Worker` series because the readiness metric here is complete *portfolio*, rather than the original affected-option-only scope.

## Decision and next safe step

**G5 production Worker activation remains NO-GO.** This benchmark supports prioritizing the **single-in-flight dispatcher and per-contract fanout/commit scheduling** for investigation before increasing Worker count or reducing polling delay. Any proposed batching/wakeup change needs a separately approved, test-first code slice and numerical/risk/order regression; do not infer that adding Workers or busy polling is automatically safe.

Representative **recorded real-feed offline data**, preapproved risk-ready p95/p99 and CPU/RSS SLOs, fairness under realistic fanout, production rollback and Windows/CTP acceptance are still missing. G3/Q45/N/W/official exchange trading-day and replay-provenance rules remain blocked; reconnect auto-release remains disabled. **Do not enable real Worker trading or change the existing synchronous Brent production default.**

## Reproducible artifacts

- Benchmark: `script/benchmark_iv_fair.py` (the test-only transport switch) and the backwards-compatible `script/benchmark_iv_chain.py` selector.
- Tests: `tests/test_iv_fair_benchmark.py`.
- Summary: `docs/optionmaster-iv-fair-solver-summary-20261123.json`.
- Nine individual raw results: `docs/optionmaster-iv-fair-solver-{6,12,24}opt-{20261123,20261124,20261125}.json`.

**No commits, push, PR, merge, deployment, real gateway connection, live orders, or paid external calls occurred.**
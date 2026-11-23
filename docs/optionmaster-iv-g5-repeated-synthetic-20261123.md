# OptionMaster IV Phase 1 — G5 Repeated Synthetic Full-Chain Evidence

**Date:** 2026-11-23

**Goal:** `wc_goal_60H7BlqG-tRBeJAf`

**Session:** `wc_sess_eb6OiX8SQJ8lLPcr`

**Project branch/base:** `fix/optionmaster-audit-20261122` / `0ec421f` (local dirty, all work uncommitted)

**Verdict:** **G5 production NO-GO**; safe offline measurement completed, no live trading/CTP/order/remote writes.

## Scope and reproducibility

Nine bounded standalone invocations of the existing full-chain simulator: **6 options / 36 ticks**, **12 / 72**, and **24 / 144**, with seeds `20261123`, `20261124`, `20261125` for each size. Every invocation executed four distinct modes: sync/paced, Worker/paced, sync/burst and Worker/burst, for **36 measured path executions**. The same synthetic value/seed descriptors were used for each invocation's four modes. Each mode started fresh and initialized current in-memory timestamps; there is no real exchange capture or historical Tick source.

Commands for each combination: `.venv/bin/python script/benchmark_iv_chain.py --options {6,12,24} --events {36,72,144} --seed {20261123,20261124,20261125}`. The suite uses the real OptionEngine → PortfolioData → ChainData → OptionData ingress/Greek paths, with mocked all-open sessions and **no real gateway or order**. Opt-in Worker uses one process, single in-flight request, bounded queue, Latest-Wins, independent per-option risk identity and the previously optimized scalar structured Black-76 IV solver; the sync path uses legacy rounded Brent. All tests ran on the same current macOS ARM64 Python 3.13.18 Runner, in sequential order; this is **not** a randomized/counterbalanced instrumented experiment.

## Results — primary decision metric

Each table entry is the **median of the three seed-specific within-run p95s**, in ms. This is not the p95 of all observations pooled, not a confidence interval, and not a production SLO.

| Options / ticks | Sync paced risk-ready p95 median (range) | Worker paced risk-ready p95 median (range) | Worker-minus-sync paired p95, by seed |
|---|---:|---:|---|
| 6 / 36 | **5.188** (4.911–6.375) | 5.576 (5.564–6.049) | +0.653, −0.326, +0.388 ms |
| 12 / 72 | **9.688** (9.531–15.338) | 10.424 (10.202–10.869) | −4.469, +0.736, +0.671 ms |
| 24 / 144 | **21.343** (20.631–23.403) | 26.956 (21.694–49.316) | +5.613, +25.913, +1.063 ms |

At **24 options**, the median of run-specific p99s was **22.055 ms sync versus 46.436 ms Worker**. The Worker p99 varied **22.497–50.921 ms** across the seeds; even after the scalar-IV optimization, the Worker is not uniformly superior on the actionable-risk latency tail. Its 24-option p95 median is approximately 26% slower than sync in this small set of runs (26.956 versus 21.343 ms); **do not extrapolate this as a production degradation estimate**.

## Event-loop responsiveness is a separate metric

For burst-mode Tick ingress, the median of three within-run p95s was:

| Options | Sync burst ingress p95 | Worker burst ingress p95 |
|---|---:|---:|
| 6 | 5.789 ms | **0.261 ms** |
| 12 | 11.631 ms | **0.393 ms** |
| 24 | 20.416 ms | **0.835 ms** |

The Worker accepts events promptly by deferring computation, but burst ingress p95 is **not** Tick-to-actionable-risk p95, queue drain time, end-to-end order readiness or a guarantee of risk correctness during the burst.

## Queue, correctness, CPU and RSS evidence

- **All 36/36 mode executions ended with every option's risk snapshot current** (`all_risk_ready_end=true`). All nine paced sync and nine paced Worker executions had **zero paced event failures**. All measured Worker scheduler-overflow totals were **zero**.
- Worker paced pending-queue peak was **5 / 11 / 22** for 6 / 12 / 24 option chains, respectively; Worker burst peak **6 / 12 / 24**. This is consistent with the existing single-in-flight `IVDispatcher`, where multiple option contracts wait for one Worker request at a time. **Queue growth is not evidence of unbounded capacity**, as the scheduler is explicitly bounded.
- Under burst-mode 24-option runs, Worker stale replies discarded were **112 / 99 / 101**, with **798 / 802 / 814** superseded queued requests and **7 / 13 / 8** rejected results/commits respectively. These reflect Latest-Wins and version checking under synthetic churn, **not trading cancellations**. Final risk readiness does not mean each interim Tick had an actionable risk snapshot.
- 24-option **paced** Worker child CPU (post-reap including process initialization/selftest and actual computation) was **0.9486 / 1.0082 / 0.8134 seconds**; 24-option Worker process RSS sampled via `ps` was **146,896 / 146,672 / 146,992 KiB**. Values are not an independent per-Tick CPU price or true peak memory. Child RSS is sampled rather than full high-water; parent peak RSS is lifetime peak and cannot directly establish the incremental impact of Worker.
- Worker paced startup time in the 24-option runs was **686.09 / 556.08 / 583.02 ms** (the session includes import/selftest/startup). Startup is reported separately from per-event timing; no production restart SLO is inferred.
- No real CTP/reconnect sequence, official exchange trading day/phase, live quote staleness, order/fill/cancel race or Windows GUI scenario was executed. The script uses fresh local timestamps and in-memory all-open synthetic sessions for research only.

## Source-verified mechanism and interpretation

`vnpy_optionmaster/pricing/iv_worker.py` defines `IVDispatcher` with **at most one request in flight**, and `iv_runtime.py` uses a single isolated Worker. An underlying Tick may invalidate many option Greeks simultaneously. Thus, owner-side per-option serialization and the periodic `pump()`/queue path remain plausible contributors to growing aggregate risk-ready tails, even when fused price/Vega computation is fast. The repeated runs show high option-count variability but do **not** isolate its exact causal breakdown (OS scheduling, workload mix, IPC, Python GC and cache can co-vary). Do not interpret a large apparent outlier as a proven scheduler defect without finer profiling.

**Decision:** Keep default **synchronous Brent**; keep Worker **opt-in/offline only**. Do not bypass version checks, weaken numerical tolerances, enable busy polling or increase Worker count solely on this evidence. Optimizing ingress is not equivalent to meeting G5 risk readiness.

## Blockers and next independently authorized evidence

1. **Representative real-feed offline captures**: option+underlying Tick identities, liquid/illiquid strikes, multiple maturities, burst/quiet regimes, source changes, exact receiving and event times, without connecting to production here. No eligible recorded dataset was found in the project tree after excluding `.venv`, `.uv-cache`, `.git`, build caches.
2. **Pre-approved G5 release matrix**: p50/p95/p99 *actionable* risk-ready SLO by chain size and market regime; event-loop throughput guardrail; queue/cancel budget; CPU/RSS memory ceiling and startup/restart limits; acceptable numerical errors; rollback drill criteria. The user has not frozen specific values.
3. **Fair paired performance design**: same dataset, reference numerical acceptance contract, separate cold/warm conditions, repeated and randomized sync/Worker run ordering, controlled host load, explicit confidence/variance and per-contract fanout time-series; unambiguous CPU/RSS high-water instrumentation.
4. **G3** remains blocked on Q45 clock, N/W, official trading calendar, trustworthy source/replay boundaries; keep auto-release disabled.
5. **Independent production gate**: CTP/Windows order/cancel/reconcile/quote validation, native GUI state, model backend, operator approval and authorized release. No commit, push, PR, deployment, real gateway or orders.

## Archived raw evidence

- `docs/optionmaster-iv-g5-repeat-6opt-20261123.json`
- `docs/optionmaster-iv-g5-repeat-12opt-20261123.json`
- `docs/optionmaster-iv-g5-repeat-24opt-20261123.json`
- Related earlier synthetic scalar benchmark: `docs/optionmaster-iv-solver-fastpath-optimization-20261123.md`.

**Bottom line:** local repeatability evidence materially strengthens the **G5 NO-GO** assessment. This is a bounded diagnostic, not a complete Phase 1 production approval. Goal remains Active pending external evidence and signoff.

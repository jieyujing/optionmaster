# OptionMaster IV Phase 1 — Worker Performance Attribution (Offline)

**Date:** 2026-11-23

**Goal:** `wc_goal_60H7BlqG-tRBeJAf`

**Session:** `wc_sess_eb6OiX8SQJ8lLPcr`

**Repository:** `fix/optionmaster-audit-20261122`, base `0ec421f` (uncommitted local changes)

**Status:** Reproducible local diagnostic only, **NO-GO for production Worker**

**Source:** `script/benchmark_iv_attribution.py` and `docs/optionmaster-iv-worker-attribution-synthetic-20261123.json`

## Problem / why the older benchmark was not a clean IPC comparison

The earlier full-chain 24-option / 144-event synthetic replay reported **20.969 ms** p95 paced event-to-risk-ready for the legacy synchronous route versus **68.395 ms** for the opt-in Worker. The two routes do **not** execute the same numerical algorithm: the synchronous compatibility API invokes `scipy.optimize.brentq` and rounds IV to four decimal places; the Worker invokes an explicit full-precision, guarded Newton/bisection `IVResult` solver. The full-chain benchmark also measures event ingress, option/underlying fanout, portfolio aggregation, one-at-a-time Worker dispatch and owner-side polling.

A performance difference across those paths cannot be attributed entirely to subprocess IPC or used as evidence that adding Workers will reduce actionable risk latency.

## Experimental setup

- macOS ARM64, Python 3.13.18, local offline project `.venv`, **no live gateway, trading, user feeds, deployment or paid services**.
- Construct 24 independent synthetic Black-76 option inputs, each requiring exactly three IV inversions (Bid/Ask/Mid); use correct option prices and fixed model inputs for all compared paths.
- Run the same 24 requests against four modes: legacy in-process rounded Brent; structured full-precision solver with in-process LRU; same structured solver in a single spawned Worker with `sleep(0.0005)` polling; same Worker path with `sleep(0)` polling.
- Cold phase has no IV cache entries. Warm phase repeats the identical pricing inputs and may hit the structured solver's exact-input LRU. **Legacy Brent has no LRU; its 'warm' pass is only a second run, not a cache hit.**
- Before timing Worker round trips, independently calculate the direct structured reference results. After each timed reply, validate the worker's Bid/Ask/Mid numerical output against that precomputed reference to **1e-12**. Do not include reference recomputation in timed IPC measurements.
- Worker starts fresh with separate numerical selftest and cold operational cache. Report startup separately; one request at a time; bound each round-trip deadline to five seconds.

## Primary result: 24 requests, medians (ms/request)

| Path | Cold numeric / roundtrip median | Repeated-input median | Notes |
|---|---:|---:|---|
| Legacy sync Brent (rounded) | **0.720** | 0.731 | No LRU in either pass |
| Structured solver, in-process | **1.889** | **0.002** | 72 cached Bid/Ask/Mid values in second pass |
| Worker, structured, 0.5 ms poll sleep | **2.304** | **0.661** | Startup separately 536.465 ms; 72 cache hits |
| Worker, structured, `sleep(0)` | **1.836** | **0.070** | Startup separately 529.246 ms; 72 cache hits; much more polling |

Cold p95 (24 sample) for those paths respectively: **0.785 / 2.475 / 2.829 / 2.547 ms**. Warm p95: **0.788 / 0.003 / 0.672 / 0.087 ms**. `sleep(0.0005)` case polled 157 times across both passes, compared with 3,200 `sleep(0)` polls; this is a poll-count proxy, not an independent CPU utilization measurement.

## Secondary result: 12 requests, independent run

| Path | Cold median (ms) | Repeated-input median (ms) |
|---|---:|---:|
| Legacy sync Brent | 0.730 | 0.721 |
| Structured solver, in-process | 1.871 | 0.002 |
| Worker, structured, 0.5 ms poll sleep | 2.018 | 0.662 |
| Worker, structured, `sleep(0)` | 2.042 | 0.070 |

Both sample sizes show the same broad pattern, but the runs are host-scheduled and too small to establish stable production tail latencies.

## Source-grounded interpretation

1. **Solver algorithm differences are material.** On the 24-request experiment the cold direct high-precision structured solver median is about **2.62×** the legacy Brent median. This is algorithm and numerical-contract comparison, not a fair assertion that Brent is interchangeable with the new risk-accepted solver. The new solver uses full precision, bounded iterations, explicit status and residual/vega tests; the compatibility route rounds its result.
2. **A worker round trip has a nonzero latency floor even when all numerical IVs are cached.** Warm numerical lookup in-process costs ~0.002 ms, but worker round-trip median is ~0.661 ms with a 0.5 ms poll sleep, or ~0.070 ms under aggressive polling.
3. **Polling strategy is measurable but not a free performance win.** The more aggressive loop generated ~20× more poll attempts in the 24-request microbenchmark. These numbers do not justify a production busy-spin without CPU, responsiveness and fairness budgets.
4. **Serial fanout amplifies the delay.** The current `IVDispatcher` has **at most one request in flight**. An underlying Tick can invalidate many option risk results; each option must wait for its own Worker trip, so full-portfolio actionable readiness accumulates latency under fanout. More Workers would contradict the explicitly agreed initial single-Worker scope, so is not an automatically approved fix.
5. **Exact-input cache only helps when all material pricing inputs repeat.** Warm 72/72 hits are deliberately idealized; live market-data reuse must be measured, not assumed. Cache hit also never renews Risk-Ready by itself.
6. **Full-chain p95 includes additional path differences.** This microbenchmark purposefully excludes Tick admission, IPC batching at portfolio level, aggregations, source freshness and order checks. It explains likely contributors but does not decompose the earlier 68.395 ms by mathematical subtraction.

## Scope-preserving follow-up priorities

1. **First:** profile guarded solver iterations and pricing CDF/PDF calls under parity-critical scenarios. Seek improvements *without* weakening IV acceptance tolerances, silently switching to rounded Brent, changing selected model backends, or introducing a new unapproved model.
2. **Second:** evaluate bounded owner-side polling/event wakeup strategies with explicit CPU/queue fairness and cancellation budgets; test in the existing single-Worker architecture before discussing topology changes.
3. **Third:** instrument real representative offline quote streams to measure cache hit rate, affected fanout size, time-to-all-held-dependencies-ready, Worker CPU/RSS and cold/warm startup costs.
4. **Fourth:** freeze explicit G5 p95/p99 actionable risk, CPU, memory and rollback limits **before** an optimization acceptance trial; compare identical economics, solver precision and source stream in both paths.

## Remaining non-negotiable gates

**G3**: Q45 W clock unresolved; N, W and official trading-day/source identity unapproved; no auto-release. **G5**: no representative offline CTP/exchange capture or pre-approved production latency/resource SLO; default sync Brent preserved. **Platform**: Windows, real CTP reconnect and order/trade/cancel races unverified. **Release authority**: no user approval for commit, push, PR, deployment or live orders.

**Verdict:** Attribution is useful and reproducible, but it **does not** demonstrate a Worker performance gain for actionable risk and **does not** satisfy the Goal's external production completion gates. Keep the Goal Active and production activation NO-GO.
# OptionMaster IV Phase 1 — Safeguarded Newton Scalar Hot-Path Optimization

**Date:** 2026-11-23

**Status:** Local implementation verified offline, **production NO-GO**

**Goal:** `wc_goal_60H7BlqG-tRBeJAf`

**Session:** `wc_sess_eb6OiX8SQJ8lLPcr`

**Branch/base:** `fix/optionmaster-audit-20261122` / `0ec421f` (local dirty working tree; no commit)

**Scope authorization:** User approved profiling and small local solver optimization. No live CTP, order, deploy, push or release.

## 1. Baseline and source-grounded diagnosis

The structured Black-76 / Black-Scholes IV solver used guarded Newton inside a bounded volatility bracket. Each iteration separately called `calculate_price()` (two SciPy `stats.norm.cdf` calls plus recomputed d1) and `calculate_vega()` (SciPy `stats.norm.pdf` plus another d1). Under a 180-scenario grid of models, calls/puts, strikes, expiries and volatilities, **176 succeeded, 2 returned NO_SOLUTION and 2 returned MAX_ITERATIONS**. Successful cases had **median 18, max 34 iterations**. These represent the existing numerical contract; unsuccessful scenarios were not made successful by weakening checks.

The original `cProfile` run measured **360 solver invocations** (twice the 180 grid), approximately **1,326,299 function calls in 0.371 s**. Most cumulative time was concentrated in SciPy distribution-level CDF/PDF wrappers, their broadcasting/argument processing, and repeated pricing/vega calculations. These are profiled timings, not production latency or reliable isolated CPU percentages.

Prior single-run attribution (24 synthetic requests, Bid/Ask/Mid IV on same machine): in-process structured cold median **1.889 ms/request**, Worker structured at 0.5 ms polling **2.304 ms/request**. Earlier full-chain synthetic 24-option/144-event paced Worker risk-ready p95 **68.395 ms**, sync Brent p95 **20.969 ms**. The legacy Brent path and structured Worker path are different solvers; compare with care.

## 2. Narrow implementation

Updated source files:

- `vnpy_optionmaster/pricing/iv_solver.py`: added an optional internal `price_vega_function` callback. If omitted, the original public `price_function`/`vega_function` path continues unchanged. Bracket endpoints, max iteration limit, minimum Vega, residual-based acceptance, error-to-Vega budget, guarded Newton fallback and `IVStatus` semantics are unchanged.
- `vnpy_optionmaster/pricing/black_76.py`: added private scalar `_price_vega_for_iv` and wired it only to the structured result solver. It reuses one `sqrt(t)`, one d1 and d2, uses `scipy.special.ndtr` for the scalar normal CDF, and computes analytic standard normal PDF with `math.exp`.
- `vnpy_optionmaster/pricing/black_scholes.py`: same private fused-kernel approach with Black-Scholes-specific d1, discount and Vega formulas.

**Not changed:** Public legacy Brent `calculate_impv`, original public price/Greeks APIs, pricing model names, quote/session checks, worker scheduling/topology, cache identity, IVResult fields, numerical acceptance thresholds, error status definitions, default synchronous production path, Q45/N/W settings and reconnect quarantine.

## 3. Test-first and numerical invariants

New test file: `tests/test_iv_solver_fastpath.py`.

- **Red:** The first focused fast-kernel parity test failed as expected because `_price_vega_for_iv` did not exist.
- **Green:** Tests for the scalar private kernels versus the original public pricing/Vega implementations, optimized solver versus the original non-fused callback path, bounded errors and fail-closed behavior passed.
- **Extended invariant grid:** All **180** original scenarios retained **exactly the same success/no-solution/iteration-exhaustion status**, **exactly the same iteration counts**, and the **176 successful** IV values differed by at most **1.0173e-13** from the original non-fused solver. This verifies the tested grid, not every floating-point platform or real market input.
- **Focused pricing tests:** 288 passed with 4 third-party pandas warnings.
- **Full suite:** `.venv/bin/python -m pytest tests -q --tb=short` → **468 passed, 4 third-party pandas warnings**.
- **Static/build:** `.venv/bin/ruff check .` PASS; `.venv/bin/mypy vnpy_optionmaster` PASS (25 source files); `uv pip check --python .venv/bin/python` PASS (67 installed packages); `uv build` PASS; artifact/wheel tests 3 passed; `git diff --check` PASS.

## 4. Profiling and local performance

**Profile of same 360 solver invocations** after the private scalar optimization: approximately **180,865 function calls in 0.044 s** (before 1,326,299 / 0.371 s). Bracket boundary evaluations continue to use the original public price callback. Profile timing is affected by instrumentation and host scheduling; it is evidence of reduced Python/SciPy wrapper work, not a production SLO.

### Microbenchmark — first observed after-change run

| Metric, 24 synthetic requests × three IV sides | Prior implementation | New scalar hot path |
|---|---:|---:|
| Structured direct, cold-cache median | 1.889 ms | **0.177 ms** |
| Worker, cold-cache median, 0.5-ms poll sleep | 2.304 ms | **0.669 ms** |
| Worker, hot-cache median, 0.5-ms poll sleep | 0.661 ms | 0.633 ms |

An additional persisted after-change run yielded structured direct cold median **0.173 ms**, Worker cold median **0.671 ms**, sync legacy Brent cold median **0.800 ms**. Host variability is visible and these samples are not statistically controlled confidence intervals.

### Full-chain replay — 24 synthetic options / 144 mixed option+underlying Tick events

| Metric | Earlier observed | After optimized scalar kernel |
|---|---:|---:|
| Worker paced Tick→risk-ready p95 | 68.395 ms | **21.690 ms** |
| Synchronous Brent paced Tick→risk-ready p95 | 20.969 ms | **19.903 ms** |
| Worker burst ingress p95 | 0.629 ms | 0.687 ms |
| Worker final backlog drain | 67.70 ms | 19.58 ms |

**Interpretation:** The opt-in Worker now approaches, but does not beat, the legacy synchronous path on the key paced risk-readiness p95 in this small synthetic benchmark. A drop in cold direct solver latency does not prove lower risk latency in all trading conditions; cache hit rates, one-at-a-time dispatch, source event patterns, host scheduler and fanout still matter. Never convert this into an expected trading profit or production approval.

### Archived evidence

- Baseline attribution: `docs/optionmaster-iv-worker-attribution-synthetic-20261123.json`.
- After hot path: `docs/optionmaster-iv-worker-attribution-scalar-fast-20261123.json`.
- Full-chain after hot path: `docs/optionmaster-iv-chain-benchmark-scalar-fast-20261123.json`.
- Repro scripts: `script/benchmark_iv_attribution.py`, `script/benchmark_iv_chain.py`.

## 5. Review and remaining gates

**Safety review:** Change is restricted to structured IV price/Vega evaluation and the optional callback into the bounded solver. Public Black model pricing/Greeks and legacy Brent inversion remain untouched. Solver success/status/iterations match the non-fused reference over 180 deliberately varied offline scenarios. No live Worker activation, no reduction of risk numerical acceptance, no autonomous reconnect release, and no unexpected source tree cleanup.

**G5 remains NO-GO:** the Worker has not demonstrated a durable real-feed improvement against independently approved p50/p95/p99, CPU/RSS, queue capacity, timeout/recovery and rollback budgets. Same-host single runs and a synthetic all-open session do not qualify. Prefer representative recorded market data and a pre-frozen parity/SLO matrix for the next gate.

**G3 remains BLOCKED:** Q45 W clock choice, uniform N, finite W, official trading-day proof, market-data source identity and safe replay completion are unresolved. Auto-release stays OFF.

**Platform/production remains unverified:** Qt/Windows UI rendering, real CTP chronology, order/fill/cancel races, gateway ownership, operator reconciliation and the independent release approval. Default synchronous Brent remains unchanged.

**No commit, push, PR, merge, deploy, real gateway connectivity, live order or paid external API call was made.**
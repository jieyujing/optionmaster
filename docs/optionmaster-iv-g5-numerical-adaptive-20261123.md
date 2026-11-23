# OptionMaster IV G5 — Non-injected Numerical HOL and Adaptive-Cap Candidate (OFFLINE)

**Date:** 2026-11-23

**Goal:** `wc_goal_60H7BlqG-tRBeJAf` · **Session:** `wc_sess_eb6OiX8SQJ8lLPcr`

**Branch/HEAD:** `fix/optionmaster-audit-20261122` / `0ec421f` (existing dirty/untracked tree preserved)

**Decision:** **Test-only hypothesis supported directionally; no production enablement. G5 NO-GO; G3 Q45/N/W BLOCKED.**

## Research question and actual measurement boundary

The previous deliberately injected **12 ms per request** experiment established that a submitted ordinary group cannot be preempted by an urgent held-position quote. It did **not** measure real calculation time. This study surrounds the unchanged `calculate_group` and `WorkerCache` computation with subprocess `monotonic()` timestamps and **does not inject a sleep into the numerical path**.

The new `script/benchmark_iv_microbatch_service_hol.py` still uses a **test-only synchronization gate** to ensure a newly arriving held-position or underlying Tick is processed while an ordinary group is already accepted by a single Worker. **The gate wait is outside the recorded compute interval.** Its release/OS scheduling overhead remains in end-to-end quote-to-actionable measurements. Timestamps for compute start/end are published only **after** calculation, excluding trace-queue writes from the numerical interval. These are not a natural arrival-time or production Worker performance trace.

The actual `OptionEngine.process_tick_event` path, `OptionIVRuntime.submit` capture/versioning, owner `commit_option_iv`, full `PortfolioData.actionable_risk_snapshot`, and `LatestWinsScheduler` priority/fairness rules are exercised. **Only experimental IPC and the batch selection seam differ.** The synthetic chain has 12 options, either **one held option with an option Tick** or **three held options with an underlying Tick**. No CTP/gateway/recovery/real orders are accessed.

## Study A — 36 fixed-cap numerical-service observations

Three seeds × cold/warm cache × single-held option / three-held underlying update × fixed caps 1/4/8. All finished with complete actionable held-portfolio snapshots and zero scheduler overflow. Below are medians of **three individual runs per cell**, in ms (not p95/p99).

| Scenario | Metric | Fixed 1 | Fixed 4 | Fixed 8 |
|---|---|---:|---:|---:|
| Cold, one held option | Ordinary batch calculation | 0.257 | 0.826 | 1.541 |
| Cold, one held option | Tick → actionable portfolio | 2.897 | 3.804 | 5.158 |
| Warm, one held option | Ordinary batch calculation | 0.012 | 0.017 | 0.028 |
| Warm, one held option | Tick → actionable portfolio | 2.843 | 3.177 | 3.907 |
| Cold, three-held underlying fanout | Tick → actionable portfolio | 6.230 | 4.196 | 6.479 |
| Warm, three-held underlying fanout | Tick → actionable portfolio | 5.981 | 3.639 | 5.298 |

**Interpretation:** Ordinary in-flight non-preemptible computation increases with cold batch size, but a 3-held underlying fanout may benefit from processing several required Greeks in one batch. A tiny warm numerical service interval does **not** mean all quote-to-portfolio time vanishes: dispatch/poll and owner commits still contribute.

**Data:** `docs/optionmaster-iv-g5-numerical-hol-20261123.json`. The first diagnostic run was superseded after moving trace writes outside the compute timing window; the JSON and table above reflect the subsequent rerun, not the preliminary first-run numbers.

## Study B — 48 matched-backlog comparison of a pure experimental cap selector

A further **offline-only heuristic** chooses at the *next group dispatch*:

- **No held-position exposure:** up to 8 ordinary messages (unit-tested selector branch; not exercised as a standalone market scenario).
- **Held positions exist, zero/one held request pending:** cap **1**, limiting future urgent head-of-line waiting behind ordinary work.
- **Two or more held requests pending:** cap **4**, aggregating simultaneous portfolio dependencies.

The test-only `CandidateAdaptiveDispatcher` is not included in production Worker code and **cannot interrupt an already sent batch**. No independently approved HOL budget or calibrated per-member time bound was supplied, so the numbers 1/4/8 are an experiment hypothesis, not a deployable control policy.

`script/benchmark_iv_microbatch_adaptive.py` compares fixed 1/4/8 against the candidate with **the same 8 pre-staged ordinary requests** and seeded synthetic event values, rotating treatment order. There are 3 seeds × 2 cache states × 2 urgency/fanout modes × 4 policies = **48 sequential runs**. Each run launches its own spawned subprocess and uses an experimental arrival gate.

### Complete held-portfolio Risk-Ready, ms (median across 3 runs/cell)

| Policy | One held, cold | Three held, cold | One held, warm | Three held, warm |
|---|---:|---:|---:|---:|
| Fixed 1 | **2.810** | 6.194 | 3.123 | 5.924 |
| Fixed 4 | 4.689 | 4.039 | 3.407 | **3.785** |
| Fixed 8 | 5.412 | 6.382 | 3.590 | 5.267 |
| **Candidate** | 2.983 | **3.877** | **3.071** | 3.800 |

The candidate beats fixed 8 on **these four observed medians**, but does not always beat the best fixed cap. For simultaneous three-held updates, fixed cap 1 requires **three held-result batches**; fixed cap 4 and the candidate can restore all three in one (with mixed ordinary fairness slots). For a single urgent held update, ordinary batch cap 1 reduces the non-preemptible interval.

**Data:** `docs/optionmaster-iv-g5-adaptive-matched-backlog-20261123.json` includes all 48 records and seeded within-condition candidate-minus-fixed8 differences. These are sequential rotated comparisons and **not randomized simultaneous pairs**.

## Safety and validation

- New files: `script/benchmark_iv_microbatch_service_hol.py`, `script/benchmark_iv_microbatch_adaptive.py`, `tests/test_iv_microbatch_service_hol.py`, `tests/test_iv_microbatch_adaptive.py`, and the two data JSONs.
- **35 new focused tests passed.** Project venv full suite `.venv/bin/python -m pytest tests -q`: **535 passed, 4 external Pandas warnings**. Ruff PASS; mypy PASS on 25 source files; dependency check 67 packages PASS; `uv build` PASS; isolated wheel tests **3 passed**; `git diff --check` PASS.
- Portfolio actionability remains revoked during the urgent replacement and returns only after valid owner commits; the experimental IPC still uses one Worker epoch and fail-closed overflow/timeout checks. No production module, order sender, model/backend default, Q45/N/W or reconnect gate changed.
- **Not demonstrated:** production p95/p99/worst-case tail, natural arrival-time distribution, representative recorded feed, high-contention queue behavior, age/deadline budgets, actual Windows/CTP behavior, CPU/RSS high water, Worker startup numerical selftest and rollback.

## Decision and next valid gate

Keep the heuristic **research-only**. Before considering production code, acquire representative *offline recorded* option/underlying fanout including held/nonheld and burst/quiet, pre-approve complete-portfolio urgent **p95/p99 and queue-deadline** constraints and CPU/RSS budget, and use enough observations for tail estimation. Evaluate throughput and risk-ready tails jointly with the original `SingleIVWorker` baseline and a genuine one-Worker protocol startup/selftest + rollback design.

The original Goal is **ACTIVE**, with **32/32 existing milestones already completed and no new slots**; this work should be recorded as a new evidence Checkpoint in the exact Session rather than creating a duplicate Goal. **No commit, push, PR, deployment, CTP connectivity, real trade, paid API, Agent or release authorization was used.**

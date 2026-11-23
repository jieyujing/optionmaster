# OptionMaster G5 — Offline Microbatch HOL and Interleaved Comparison

Date: 2026-11-23. Goal: `wc_goal_60H7BlqG-tRBeJAf`. Session: `wc_sess_eb6OiX8SQJ8lLPcr`.
Branch: `fix/optionmaster-audit-20261122`, HEAD `0ec421f`, dirty/uncommitted.
**Decision: G5 NO-GO; G3 Q45/N/W BLOCKED. No production Worker changes.**

## Code-level head-of-line finding

In `script/benchmark_iv_microbatch_integration.py` the test-only dispatcher accepts at most one in-flight batch. It drains `_ready` before submitting the next batch. `LatestWinsScheduler.take()` orders **only pending requests** by active-risk priority and bounded fairness; it does not interrupt a batch already executing inside `PrototypeBatchWorker`. Therefore a newly arriving urgent held-position request can be blocked behind **the entire remaining in-flight ordinary batch**, plus owner consumption of existing replies. It does not have a bounded priority response deadline independent of batch service time.

Four parametrized tests added in `tests/test_iv_microbatch_integration.py`: for batch sizes 4 and 8, test urgent held-position request cannot preempt an already-sent ordinary batch and is first in the next one; test an updated urgent quote discards the stale in-flight member but accepts independent fresh siblings. These are **deterministic scheduler/owner-contract tests**, not empirical millisecond HOL measurements. Focused tests: **11 passed** with 4 pandas third-party warnings.

## Interleaved same-prototype IPC experiment

Script: `script/benchmark_iv_microbatch_paired.py`; source JSON:
`docs/optionmaster-iv-microbatch-interleaved-24opt-20261123.json`.

Exactly 24 synthetic options and 144 event descriptors, three seeds, using the real `OptionIVRuntime` owner commit/portfolio actionability and a single experimental batch subprocess. Per-seed order rotates: (1,4,8), (8,1,4), (4,8,1). Each run starts a separate Worker and is sequential, **not randomized simultaneous pairing**, with the same synthetic value sequence and independent fresh timestamps. Batch=1 here is the *test-only prototype*, **NOT `SingleIVWorker`** and cannot replace the archived 20.430 ms production single-request-path offline control.

| Seed | Prototype batch=1 p95 | Batch=4 p95 | Batch=8 p95 |
|---|---:|---:|---:|
| 20261123 | 84.994 ms | 28.439 ms | 11.431 ms |
| 20261124 | 90.016 ms | 27.814 ms | 11.585 ms |
| 20261125 | 89.839 ms | 19.929 ms | 11.378 ms |
| Median of per-run p95 | **89.839 ms** | **27.814 ms** | **11.431 ms** |

**Interpretation:** The same experimental IPC implementation benefits strongly from grouping under this paced, synthetic, portfolio-ready workload. This strengthens the within-prototype batch-size association but does not isolate exact causes of batch-4 delay; batch-1's much higher times highlight implementation differences, not a production speed regression. The previous independent `SingleIVWorker` 24-option p95 20.430 ms remains a separate unmatched control; do not combine or claim an 8-versus-production statistical speedup. The synthetic scenario omits real feed priorities, queue waits over stressed intervals, real source identities, trading days, and burst-specific urgent held-position risk-ready tail.

## Remaining empirical acceptance work

1. Instrument monotonic request enqueue, batch submit, Worker start/end, result dequeue, owner commit and first complete portfolio-actionable time; record priority class, sequence identity, age and in-flight batch position **without modifying trading defaults**.
2. Replay independent urgent held-position requests arriving after a non-risk batch dispatch, mixed churn/stale and underlying fanout. Evaluate p50/p95/p99/worst urgent queue wait and actionability after stale replacement, alongside ordinary starvation, parent/child CPU and RSS.
3. Compare same-run baseline vs caps 4/8 with representative **recorded** feed, approved independent SLO and numerical quality. Set fail-closed fallback, startup selftest, crash/timeout and rollback criteria prior to any production integration.
4. Preserve Q45 W-clock, N/W, official trading-day and source replay blockers; retain auto-release OFF, G5 NO-GO, Brent default and no live/CTP/gateway use.

This revision only creates an offline benchmarking script, tests, JSON and this report; it does not commit, publish, deploy or authorize orders.

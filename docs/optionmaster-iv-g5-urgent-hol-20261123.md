# OptionMaster G5 — Urgent Held-Risk Microbatch Head-of-Line Stress

**Date:** 2026-11-23

**Goal:** `wc_goal_60H7BlqG-tRBeJAf` · **Session:** `wc_sess_eb6OiX8SQJ8lLPcr`

**Branch / HEAD:** `fix/optionmaster-audit-20261122` / `0ec421f`; pre-existing dirty tree preserved

**Decision:** **OFFLINE HOL mechanism confirmed; G5 production NO-GO, G3 Q45/N/W BLOCKED**

## Question

Does a newly received, higher-priority **held-position** option Tick become actionable promptly when a lower-priority ordinary microbatch has already started running?

In the current test-only dispatch scheme, pending risk-first scheduling has no authority to interrupt a previously submitted subprocess batch. The prior 24-option paced result (batch8 p95 11.431 ms versus batch4 27.814 ms in the interleaved prototype) is for **portfolio throughput**, not a new urgent held quote arriving during non-preemptible work.

## Bounded instrumented fixture

`script/benchmark_iv_microbatch_urgent.py` adds an **entirely disposable** subprocess with a deliberately injected, bounded 12 ms sleep *per member* before numerical work, plus a separate subprocess trace queue for start/end times (shared host monotonic clock). It does not alter the production `SingleIVWorker`, order senders, `OptionIVRuntime`, `IVDispatcher`, default Brent, cache rules, Greeks or source validation.

- Real owner: `MicrobatchIVRuntime` inherits `OptionIVRuntime.submit` (immutable `capture_option_iv`), `commit_option_iv`, `PortfolioData.calculate_pos_greeks` and `actionable_risk_snapshot`. The only substituted component is this *test-only* subprocess behind the earlier experimental dispatcher.
- Synthetic chain: 12 option contracts; **one held net position**, all others zero-position. Bootstrap a coherent actionable held-position portfolio; then accumulate exactly B ordinary requests under a temporary test-only pump guard, dispatch that batch, and wait for observed Worker `start`.
- Trigger: inject a **new actual option Tick via `OptionEngine.process_tick_event`** at batch-start (0%) or half of injected service time (50%). The new Tick invalidates the previous portfolio risk snapshot. Recalculate via the real owner and stop timing only when the **whole held-position portfolio** becomes actionable again.
- Trace: record urgent quote arrival, ordinary process start/finish, urgent batch dispatch, urgent Worker start/finish, owner option freshness, portfolio actionability; assert their causal monotonic order, urgent enters the next batch, zero queue overflows/timeouts, and no premature actionable snapshot.
- Caps **B=1/4/8**, arrival fractions **0/0.5**, three seeds **20261123–25**; 18 sequential executions; rotate cap ordering per seed. No real gateway or external requests. **Batch1 here is a test-only batch protocol**, not the actual production `SingleIVWorker`.

## Results — ms, medians across 3 executions per condition

The times are **not run-level p95/p99**: each independent run has one urgent arrival, so there are only three observed values per cell. With N=3, statistical tail quantiles and production SLO claims would be invalid.

| Arrival phase | Batch cap | Remaining non-preemptible service | Urgent queue-to-dispatch | Quote-to-actionable portfolio |
|---|---:|---:|---:|---:|
| 0% (immediately after ordinary starts) | 1 | 12.860 | 13.812 | 27.283 |
| 0% | 4 | 50.534 | 52.047 | 65.271 |
| 0% | **8** | **98.013** | **99.850** | **113.579** |
| 50% (middle of ordinary service) | 1 | 6.286 | 7.346 | 20.183 |
| 50% | 4 | 24.691 | 26.211 | 39.245 |
| 50% | **8** | **49.713** | **51.719** | **65.424** |

Every sample finishes actionable, dispatches the urgent held request **in the next batch**, and has **zero scheduler overflows/stale responses**. The parent and subprocess time ordering was validated for each run. The ordinary batch's injected service accounts for most of urgent queue waiting; the urgent request itself then incurs its own process/calculation and owner commit.

### Causal interpretation and trade-off

- Within **this deliberately slow, serial, non-preemptible service model**, increasing B from 4 to 8 increases the worst-placement urgent wait. This is an architectural **priority inversion / head-of-line** mechanism, not a discovery that batch8 production computation takes 96 ms.
- The prior **separate** 24-option paced synthetic batch-throughput benchmark favored batch8; that result used no injected sleep, different contract counts/events and a different requested latency distribution. The two studies expose competing objectives and **must not be numerically combined**.
- Batch size is not a monotonic optimizer of **both** tail risk-readiness and computational throughput. Without a separately approved urgent held-position service budget, selecting batch8 as production default would be premature. The scheduler's risk priority applies to *pending*, not in-flight requests.

## Verification / independent remaining gates

- New `tests/test_iv_microbatch_urgent.py`: four parametrized real owner/portfolio timing tests, six invalid-config tests, one aggregation/meta-safety test (11 focused tests passed, four third-party Pandas deprecation warnings).
- Output: `docs/optionmaster-iv-g5-urgent-hol-stress-20261123.json` (18 raw records and 6 median rows); no sample with spurious actionability, overflow or timeout.
- Requires a follow-up **non-injected instrumented** run using actual numerical processing times, workload stress with multiple held positions and underlying fanout, recorded real source chronology, and enough urgent arrivals for p95/p99 confidence. The artificially fixed per-member service time excludes realistic CPU scheduling dispersion and native platform behavior.
- Formal G5 still needs approved **urgent risk-ready p95/p99/worst-case bounds**, queue fairness, service/deadline policy, CPU/RSS resource budgets, fail-closed fallback/rollback, Worker startup numerical selftest, Windows and real CTP state reconciliation. G3 Q45/N/W session/reconnect policy is independent and unresolved.

**No change to production Worker or order code, no commit/push/PR/deploy, no CTP connection or live orders, no external paid API. Existing dirty/untracked work is not staged or discarded.**

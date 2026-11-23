# OptionMaster IV Phase 1 — Owner-Integrated Microbatch Offline Evaluation

**Date:** 2026-11-23

**Goal:** `wc_goal_60H7BlqG-tRBeJAf`

**Session:** `wc_sess_eb6OiX8SQJ8lLPcr`

**Branch/base:** `fix/optionmaster-audit-20261122` / `0ec421f` (dirty local tree, all changes uncommitted)

**Decision:** Research candidate tested; **production G5 NO-GO**. Existing synchronous Brent production default unchanged.

## Question and outcome

The isolated numerical batch IPC prototype reduced 24-contract response completion time, but did not prove owner-side risk readiness. This follow-up evaluates whether **4/8-contract bounded microbatches** can use the actual `OptionIVRuntime` owner-side `capture_option_iv`/`commit_option_iv`, `LatestWinsScheduler`, option Greeks and coherent `PortfolioData.actionable_risk_snapshot` under synthetic Tick fanout, with version and crash controls still fail-closed.

**Finding:** The test-only 8-request microbatch achieved complete actionable portfolio-risk p95 medians of **4.129 / 7.240 / 11.357 ms** for 6 / 12 / 24 options. Batch-4 showed **5.446 / 7.823 / 27.983 ms** respectively. At 24 options, batch-4 was slower than both the previously archived single-request Worker (**19.945 ms**) and a separately rerun near-session single-request Worker (**20.430 ms**), while batch-8 was faster. This is an observed synthetic workload effect, not a proven causal diagnosis or production speedup.

## Exact tested architecture

- New **test-only** `script/benchmark_iv_microbatch_integration.py` subclasses `OptionIVRuntime` and replaces only the Worker transport/dispatcher with `PrototypeBatchWorker` plus `MicrobatchDispatcher`. The real owner `submit`, immutable capture, per-contract `commit_option_iv`, `PortfolioData` Greek aggregation and `actionable_risk_snapshot` are reused **unchanged**.
- `MicrobatchDispatcher` selects up to 4 or 8 pending requests from the existing `LatestWinsScheduler` (risk-first with `max_priority_streak=2`); there is only **one batch in flight**. New offers supersede a symbol's older pending work by version. The main owner validates each reply's exact symbol/generation/sequence/quote/model identities and discards stale members while allowing independently valid siblings to reach the usual owner-only commit code.
- A structurally malformed/failed reply, refused IPC enqueue, or an explicitly expired in-flight deadline closes this experimental Worker epoch and fails closed. A Worker process death immediately invalidates the real portfolio snapshot at the next risk read, including without another Tick.
- A complete batch is **not** automatically a valid risk state. The owner needs current quote/model/position dependencies across all held instruments, correct chain sums and the full portfolio snapshot. Individual invalid Mid results remain non-actionable through the unchanged commit path.
- The prototype has no production numerical startup selftest, real market gateway or official exchange session. Its batch protocol exists in `script/`, **not** in `vnpy_optionmaster/pricing/iv_worker.py`; no registered production Worker or order sender is changed. This test is not a release candidate.

## Risk-ready latency by size

Three synthetic seeds each for 6, 12 and 24 options; paced IV input events plus underlying fanout, 6 events per option; each run also includes a separate burst mode. Values are **medians of three run-level p95 / p99 measurements**, not pooled event quantiles or statistical confidence intervals. Units: ms.

| Options | Previously archived single-request Worker p95 | Batch 4 p95 | Batch 8 p95 | Batch 4 p99 | Batch 8 p99 |
|---:|---:|---:|---:|---:|---:|
| 6 | 5.403 | 5.446 | **4.129** | 5.535 | 4.815 |
| 12 | 9.916 | 7.823 | **7.240** | 8.640 | 8.007 |
| 24 | 19.945 | 27.983 | **11.357** | 32.813 | 12.026 |

**Independent contemporaneous 24-option control:** reran the prior `script/benchmark_iv_fair.py` single-request path at the same three seeds, producing p95 values **20.615, 20.430, 20.157 ms** (median **20.430 ms**), and the in-process structured Newton comparison **7.731, 7.664, 7.848 ms** (median **7.731 ms**). These runs were performed after the microbatch samples and were **not randomized or truly paired/interleaved**; host scheduling and cache conditions still confound strict causal speedup estimates.

**Anomaly:** Batch-4 at 24 options returned p95 **28.629 / 27.983 / 26.441 ms** across the three seeds. Batch-8 returned **11.305 / 11.357 / 11.627 ms**. Under batch-4 seed 20261124, paced parent CPU was 0.2828s and feed wall 834.91ms, versus batch-8 0.1727s and 527.50ms. The batch-4 scheduling / repeated owner polling / short-batch IPC / Python runtime overhead are hypotheses, **not a proven isolated cause**. Do not silently select group size from these few runs.

## Event-loop ingress, queue and system observations

- At 24 options, synthetic burst Tick ingress p95 median was **0.671 ms (batch 4)** and **0.599 ms (batch 8)**, while owner-only in-process structured Newton previously measured 7.757 ms. This is event-loop responsiveness, **not** risk-ready during every burst Tick.
- In all **18** new integrated runs (9 size/seed groups × two batch caps), both paced and burst paths completed with **all 36 path-end portfolios actionable**, all options with current Greeks, zero paced wait failures and zero pending-scheduler overflows. In-flight outdated replies may be discarded and the group is not prematurely tradable.
- Worker process RSS is sampled via `ps`, and post-reap child CPU includes startup/import/selftest differences. Neither is a true incremental high-water capacity proof. The numerical prototype has no production numerical startup selftest; the published G5 CPU/RSS gate remains unapproved.
- The current benchmark still includes fixed-order sequential modes, local time anchored synthetic Tick identities and all-open synthetic sessions. **No replay of real exchange/CTP source identifiers, trading-day transitions or order/fill/cancel races.**

## Test-first and regression evidence

- `tests/test_iv_microbatch_integration.py` was initially red because the test-only integration module did not exist. Seven tests now pass: risk-first fairness within one group; latest-wins supersession during an in-flight group; valid sibling preservation; malformed reply and timeout fail-closed; Worker process death immediately invalidating portfolio actionability; 4- and 8-size full-chain owner risk readiness.
- Related runtime, portfolio and single-request Worker contract tests all remained green. Full `.venv/bin/python -m pytest tests -q` **485 passed, 4 third-party pandas warnings**.
- `.venv/bin/ruff check .` PASS; `.venv/bin/mypy vnpy_optionmaster` PASS (25 source files); `uv pip check --python .venv/bin/python` PASS; `uv build` produced the original universal wheel; wheel isolation tests **3 passed**; `git diff --check` PASS.
- Earlier prototype's six numerical IPC tests remain green; no product ordering/risk or selected pricing model was modified.

## Remaining safety caveats and required future acceptance

1. **No default activation:** This `script/`-only adapter is not a real runtime implementation. The existing `SingleIVWorker`, `IVDispatcher`, user settings and default synchronous Brent path remain unchanged.
2. **Batch deadlines and risk-priority preemption:** One in-flight batch cannot be interrupted when a held-position or fresh Tick becomes urgent. Full priority/deadline/queue fairness under real recorded Tick bursts must be demonstrated; batch size 8 may have worse head-of-line behavior under other regimes.
3. **Per-member/whole-batch validity semantics:** Structural batch failure is fatal; independent member staleness is dropped, but all held positions must be fresh before a portfolio can become actionable. Full source identity checks at owner and order-time revalidation are mandatory.
4. **Production Worker selftest, process failure and restart:** The test-only batch process does not perform the audited production startup numerical selftest. Reconnect policy and epoch generation, bounded IPC queue/backpressure, crash recovery, cold startup, memory high-water and restart/rollback require design and tests before a real integration.
5. **G3 independent blocker:** Q45 W clock, N and W values, official trading-day and source replay proof remain undecided; automatic recovery release stays **OFF**.
6. **G5 independent blocker:** No representative real recorded feed, approved per-portfolio risk-ready p95/p99/CPU/RSS targets, queue and throughput guardrails, rollback or Windows/CTP acceptance. No real-order authorization was requested or granted.

**Disposition:** Keep this as a *local test-only research artifact*. If the next implementation slice is explicitly authorized, propose a real Worker protocol integration limited to one Worker and 4/8 bounded microbatches, first with red failure tests for prioritized held requests and versioned owner commits, then replay with real recorded market data. **Production Worker remains NO-GO.**

## Artifacts

- `script/benchmark_iv_microbatch_integration.py` — integrated test-only owner + batch dispatcher.
- `tests/test_iv_microbatch_integration.py` — red-to-green tests.
- `docs/optionmaster-iv-integrated-microbatch-summary-20261123.json` — normalized synthetic results for 6/12/24 and batch 4/8.
- `docs/optionmaster-iv-integrated-microbatch-{6,12,24}opt-b{4,8}-{20261123,20261124,20261125}.json` — 18 raw run files.
- `docs/optionmaster-iv-microbatch-control-24opt-{20261123,20261124,20261125}.json` — three separately rerun single-request controls.

No commit, push, PR, merge, deploy, CTP connectivity, live orders, or paid API calls occurred.
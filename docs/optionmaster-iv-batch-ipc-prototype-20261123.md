# OptionMaster IV Phase 1 — Bounded Batch IPC Prototype

**Date:** 2026-11-23

**Goal:** `wc_goal_60H7BlqG-tRBeJAf`

**Workflow Session:** `wc_sess_eb6OiX8SQJ8lLPcr`

**Branch/base:** `fix/optionmaster-audit-20261122` / `0ec421f` (local uncommitted work preserved)

**Status:** **OFFLINE NUMERICAL FEASIBILITY ONLY**; neither production Worker nor G5 approved.

## Research decision

The prior same-solver comparison demonstrated that, at 24 options, a separate single-inflight Worker was slower to complete the *actionable portfolio risk snapshot* than an in-process implementation despite lower event-loop ingress latency. This experiment tests one narrower architectural hypothesis: **can sending several independent IV requests in one IPC message reduce the cost of 24 single-request round trips?**

**Result:** Yes, for synthetic numerical reply completion on this runner. With identical guarded Black-76 `calculate_batch` numerical work and exact-input `WorkerCache`, 24 independent cold IV requests were returned in a median **16.702 ms** via the existing serial `SingleIVWorker`, versus **5.364 ms** via a disposable one-message grouped subprocess (about **3.11×** faster). With 8 requests per message, the cold median was **5.882 ms** (2.84×), only 0.518 ms slower than sending all 24 together.

**This result is NOT full portfolio Risk-Ready.** The experiment deliberately does not call `OptionIVRuntime`, `capture_option_iv`, `commit_option_iv`, `PortfolioData`, or any order-sending gateway. No numerical response is published to trading risk state. The grouped process is a prototype, not the production `SingleIVWorker` implementation.

## Actual implementation and invariants

- `script/benchmark_iv_batch_prototype.py` contains a disposable `PrototypeBatchWorker` based on `multiprocessing` spawn, capped at 24 requests per group. Its immutable `BatchEnvelope` carries one generation, batch ID, and tuple of existing `IVWorkRequest` records; its `BatchReply` contains the unchanged individual `IVWorkResult`s.
- `calculate_group` invokes existing `calculate_batch` **once per constituent request**, preserving all three Bid/Ask/Mid `IVResult` values and exact-input `WorkerCache`. It does not vectorize the solver, add Workers or alter a numerical acceptance threshold.
- The **serial baseline is the project's existing `SingleIVWorker`**, using one request per IPC round trip. The grouped variant uses a separate test-only subprocess. Each mode has its own cold bounded cache and one live process, with a 0.5-ms owner polling sleep.
- A strict prototype owner-local `validate_group` checks **every** symbol's exact request identity, Worker generation, sequence, quote identity, model version and valid Mid result. A stale request, failed group or missing/invalid Mid invalidates the **whole group for numerical release**; no partial result becomes an approved risk snapshot.
- Startup and numerical-reference reconstruction/parity checking are excluded from timed intervals; process start is separately reported. The test-only worker does not replicate production selftests, queue prioritization, reconnection, Latest-Wins supersession or owner-side versioned risk commits.
- The experiment uses 6, 12 and 24 synthetic options (one shared seed-shuffled but otherwise identical numerical input set per size), three seeds `20261123`, `20261124`, `20261125`. Seed alters **processing order only**, not the options' financial inputs. Each size executes batch size 1, 4, 8 (when feasible), and all-options group; 33 mode runs in total, each with cold and exact-input warm passes. No actual market Tick stream is reconstructed here.

## Cold-cache results

Values are the median across **three mode runs**, for total wall time from the first numerical IPC submission to the last response, in milliseconds. These are not p95/p99 per-Tick risk-ready latencies, confidence bounds, or production SLOs.

| Options | Batch size | IPC messages | Cold median (ms) | Relative to serial |
|---:|---:|---:|---:|---:|
| 6 | 1 | 6 | 4.267 | 1.00× |
| 6 | 4 | 2 | 3.854 | 1.11× |
| 6 | 6 | 1 | **2.253** | **1.89×** |
| 12 | 1 | 12 | 8.273 | 1.00× |
| 12 | 4 | 3 | 4.293 | 1.93× |
| 12 | 8 | 2 | 3.543 | 2.34× |
| 12 | 12 | 1 | **2.941** | **2.81×** |
| 24 | 1 | 24 | 16.702 | 1.00× |
| 24 | 4 | 6 | 8.028 | 2.08× |
| 24 | 8 | 3 | 5.882 | 2.84× |
| 24 | 24 | 1 | **5.364** | **3.11×** |

Under 24-option warm exact-input hits, the same serial/group-4/group-8/group-24 total times were **15.404 / 3.978 / 2.137 / 0.712 ms**. In every warmed mode, all **72 option-side IV cache lookups** were hits (24 options × three Bid/Ask/Mid premiums), so the warm comparison mostly illustrates process messages and polling, not true production IV cache effectiveness. As group size increases the additional cold benefit diminishes; group-8 and group-24 are separated by only about **0.518 ms** in these runs.

## Focused correctness tests

New `tests/test_iv_batch_prototype.py` was first red (missing prototype module) and then green. It covers:

1. Reference-identical per-option Bid/Ask/Mid numerical results across grouped and existing serial process calculations; all warm exact-input cache hits.
2. One changed quote identity, invalid Mid or duplicate symbol preventing batch acceptance, with no partial approval.
3. Rejecting batch-size overflows and mixed Worker generations; cap is enforced before process enqueue.
4. Serial and grouped variants using exactly the same synthetic numerical input fingerprint and finishing only after *every* constituent response has passed validation.

Validation and the raw nine JSON files were performed locally. The benchmark's `full_numerical_batch_ready` flag means **all numerical replies validated**, not that the application portfolio has an actionable risk snapshot; its `full_portfolio_risk_committed` field is explicitly **false**.

## Architecture risk review before production implementation

| Risk | Required production invariant or acceptance test |
|---|---|
| Tick or underlying version changes while group is in flight | Per-option and shared-underlying snapshot identity and generation must be rechecked by the owner; stale values may be displayed but may not authorize trading |
| All-or-nothing group rejection after one stale member | Evaluate whether overly conservative group failure causes starvation; keep independent field-level validity and complete portfolio Risk-Ready separate |
| New risk-critical request arrives while a large group runs | Risk-first, Latest-Wins, bounded fairness and cancellation capacity must hold; a group must not prevent an urgent held-position request from progressing within approved bounds |
| Large group head-of-line blocking | Freeze max batch size and bounded wait using measured **actionable portfolio** p95/p99, not only IPC throughput; group size 4/8 is a *candidate*, not accepted architecture |
| Reconnection and replay | Q45 clock, N, W and authoritative trading-day/source identity remain unapproved; grouping must never bypass source quarantine |
| Crash, queue full, timeout or truncated response | Fail closed, discard entire invalid response, reconcile Worker generation/owner epoch and do not silently fall back to old Greeks or a live order |
| Main-thread risk publication | Only the owner process mutates per-option Greeks; coherent portfolio aggregates must be version-consistent and revalidated at the order boundary |
| CPU/RSS and Windows/CTP operational performance | Separate real-feed offline replay and source-verified production qualification required, with preapproved G5 p95/p99, CPU, RSS and rollback budgets |

## Recommendation and explicit non-decision

**Proceed to a separately approved test-first integration design for bounded microbatches (initial candidates 4 and 8), not an unconditional full-chain batch.** This is a research priority based on diminishing numerical IPC savings and potential staleness/priority tradeoffs; it is **not** a request to enable a live Worker or change the approved single-worker architecture. A real implementation must first prove equivalent Latest-Wins scheduling, failure semantics and *full* portfolio risk-ready latency using the real OptionEngine/PortfolioData path, not only an isolated numerical-response benchmark.

**G5 remains NO-GO.** Real representative recorded market data, approved risk-ready SLO, CPU/RSS headroom, exchange calendars, Windows/CTP order/fill/cancel and rollback approval are missing. **G3 remains blocked on Q45/N/W.** Legacy synchronous Brent remains the production default, source recovery auto-release remains OFF. No commit, push, PR, merge, deployment, real gateway connection, live order or paid external call was made.

## Raw evidence

- `docs/optionmaster-iv-batch-ipc-summary-20261123.json` — aggregated 3-seed median and per-seed timings.
- `docs/optionmaster-iv-batch-ipc-{6,12,24}opt-{20261123,20261124,20261125}.json` — nine independent machine-readable invocations.
- `script/benchmark_iv_batch_prototype.py` — runnable isolated numerical transport.
- `tests/test_iv_batch_prototype.py` — regression and fail-closed tests.

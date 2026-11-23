# Spec: OptionMaster IV Performance + Risk Snapshot Correctness (Phase 1)

- **Status:** Specification draft — Grill closed; not an implementation authorization
- **Date:** 2026-11-22
- **Source:** OptionMaster IV 性能优化 Grill Q1–Q44、最终 Q46=B；Q45 未选择
- **Decision count:** 45 accepted selections (Q1–Q44 + Q46)
- **Project:** OptionMaster / VeighNa option volatility trading module
- **Priority:** Phase 1 performance and risk correctness only
- **Owner:** TBD
- **Publication:** Local project document; GitHub Issues unavailable for this repository at authoring time

**Code/production status:** No application code changes, commits, deployment, or live trading authorized by this spec.

### Problem Statement

OptionMaster 在高频 Tick 更新时，对期权 Bid/Ask/Mid 的隐含波动率及相关 Greeks 进行重复计算。同步串行计算和重复输入求解可能增加风险更新延迟与计算积压；引入异步 Worker 和缓存后，又可能出现旧结果覆盖新快照、缓存命中误恢复 Risk-Ready、数值失败却仍放行自动交易等失效模式。

用户希望在 **不一次性重构全部交易执行系统** 的前提下，获得可复现、可度量的 IV 性能改善，并以风险快照正确性作为不可牺牲的验收条件。

已核验的仓库现状：

- OptionData.calculate_option_impv 当前逐项计算 Ask/Bid/Mid IV；OptionData.calculate_theo_greeks 使用 Mid IV，并维护 impv_valid、greeks_valid、greeks_quote_version。
- OptionEngine.process_tick_event 先检查已核验交易会话、Tick 时间戳、盘口质量、事件顺序，再调用 portfolio.update_tick。当前会拒绝同合约时间戳重复或冲突、乱序和未经对账的来源切换。
- risk_snapshot_is_fresh 检查期权/标的 Tick 时间有效性、Greeks 有效标记与报价版本绑定；当前 MAX_QUOTE_AGE_SECONDS 为 30，时间允许偏差另有约束。
- pricing backend 在启动时验证可兼容的 Cython price/Greeks 扩展；IV inversion 明确保留 Python 路径，运行中不得静默热切换模型。
- 自动 Delta Hedge / Electronic Eye 等新订单必须遵守人工核验的连续交易时段、可执行盘口、最终价限制和故障后人工对账/审计约束。真实 CTP 乱序、Windows、生产准入尚未验证。

以上是代码及仓库指令所支持的现状，不代表性能瓶颈已测量，也不代表生产系统已完成验收。

### Solution

交付一个 **Phase 1 计算与风险一致性改造**：

1. 为适用定价模型构建具数值验收的 IV 求解契约：Safeguarded Newton + 有界求根，完整精度输出，结构化 IVResult，必要失败 Fail-Closed。
2. 将 IV 数值工作移至 **单独立 Worker 进程**；主事件循环保留唯一风险状态提交权。调度采取 Risk-First、待执行 Latest-Wins、普通任务有界公平；按合约和标的不可变快照批量提交一次 IPC 请求。
3. Worker 内采用完整定价输入和求解配置精确匹配的 **Bounded LRU**，只缓存已验收数值。跨 Tick 版本可复用相同计算输入的 IV，但不可复用旧风险就绪结论。
4. 为计算、行情、Worker 生命周期建立可验证版本关系；主事件循环只提交匹配当前必要输入版本的结果；Bid/Ask/Mid 分别报告有效性，Feature-Level Risk-Ready 按公共硬门禁和具体风险依赖评估。
5. 维持现有行情质量及自动交易门禁；在此基础上定义 Tick 准入、正常连接的本地接收顺序回退、重连隔离和合约级恢复计数。**这些设计不授权放宽现有的时间戳新鲜度、已核验交易时段、盘口校验、来源切换对账及订单执行门禁。**
6. 对实际新增自动订单发送路径保留当前风险重验证；发现数值失败、旧版本或缺失必要依赖时禁止新增风险。现有撤单、订单对账和人工审计机制不得被重构绕过。
7. 用相同离线数据、参考模型和可复现压力条件建立 Before/After 性能与数值质量验收。

**阶段划分：** 完整 Unified Order Sender、Fencing Epoch、持久化紧急授权/订单意图、复杂故障接管与完整 Sender 背压重构作为 Phase 2，第一阶段仅保持已有相关安全边界并补齐本次风险结果使用所需的必要门禁。

### User Stories

1. As an options trader, I want IV and Greeks refreshed promptly during market bursts, so that risk information remains useful for decisions.
2. As a quantitative researcher, I want a repeatable before/after performance baseline, so that any latency improvement can be demonstrated rather than asserted.
3. As a pricing engineer, I want bounded IV solvers with numerical acceptance checks, so that invalid or non-convergent inversions never appear successful.
4. As a portfolio risk operator, I want Bid, Ask, and Mid IV results to have independent validity, so that one bad field does not silently contaminate unrelated calculations.
5. As an options trader, I want retained last-known values clearly marked invalid for trading when inputs fail, so that display continuity is never confused with actionable risk.
6. As a system operator, I want stale computation requests superseded before execution, so that old Tick bursts do not create unbounded queues.
7. As a risk operator, I want active-risk contracts scheduled first with bounded fairness for others, so that latency is controlled without starvation.
8. As a system operator, I want a single isolated IV Worker with bounded restart behavior, so that numerical failures cannot silently corrupt main-process risk state.
9. As a pricing engineer, I want exact-input caching only for accepted numerical outputs, so that identical calculations can be reused without reusing failures.
10. As a risk operator, I want all Worker responses version-checked by the sole risk-state writer, so that a late response cannot roll risk state backward.
11. As an options trader, I want a cache hit to reuse only numerical IV and not prior Risk-Ready, so that newer risk dependencies are still checked.
12. As an integration maintainer, I want existing model names and saved settings to remain compatible, so that upgrade does not silently change pricing behavior.
13. As an integration maintainer, I want startup-verified Cython pricing and Greeks behavior preserved while IV inversion remains on the audited Python path unless explicitly migrated, so that backend behavior is predictable.
14. As a risk operator, I want Tick admission to reject heartbeats, identifiable duplicates, out-of-order ticks and replay, so that such events do not refresh trading risk.
15. As a market-data operator, I want unchanged-price legitimate new Ticks handled consistently, so that price movement alone does not define market-data arrival.
16. As a risk operator, I want local receive-order fallback to be explicitly distinguished from source freshness proof, so that missing event IDs cannot create false confidence.
17. As a risk operator, I want existing exchange-time quote age, session verification and book quality checks to remain in force, so that local receive-time renewal cannot revalidate stale or invalid market data.
18. As a system operator, I want reconnect quarantine applied to affected contracts, so that a disconnected feed cannot immediately restore normal auto-trading.
19. As a system operator, I want recovery counted per contract with a uniform N and session reset rules, so that active instruments cannot restore unrelated thinly traded options.
20. As a risk operator, I want recovery counts limited to one official trading day and a bounded W, so that old ticks are not accumulated across days or long idle gaps.
21. As a risk operator, I want the limitations of tick-count recovery documented, so that replay-driven false recovery is not misrepresented as verified live synchronization.
22. As a trader using an option and its underlying, I want both required feeds recovered before the feature re-evaluates Risk-Ready, so that a recovered underlying cannot stand in for a missing option book.
23. As an automated-hedging operator, I want new order submission rechecked at the actual existing gateway entry boundary, so that an invalid IV or outdated quote cannot produce a new order.
24. As an automated-hedging operator, I want quote-side executability, final post-payup price limits and verified continuous-trading windows preserved, so that performance work cannot bypass existing order safeguards.
25. As an operations user, I want cancel requests, fills and reconciliation handled consistently through changes, so that failed-close and operator-audit requirements are not lost.
26. As a tester, I want offline replay and mocked gateway scenarios for numerical, worker, reconnect and order failures, so that the work can be verified without connecting to a real trading account.
27. As a maintainer, I want a controlled fallback or rollback plan, so that a failed or slower Worker path cannot leave automated risk-taking active on untrusted data.
28. As a project owner, I want Phase 2 trading architecture deferred explicitly, so that Phase 1 remains reviewable and testable.
29. As a release reviewer, I want unverified CTP, Windows, source-session and production behaviors labeled as unverified, so that passing local tests is not mistaken for live-trading authorization.
30. As an administrator, I want unresolved N, W, clock and solver tolerances exposed as mandatory configuration decisions, so that the implementation never invents production defaults.

### Implementation Decisions

#### A. Phase 1 functional boundaries

- **Pricing:** Retain the three existing saved model labels (Black-76、Black-Scholes、二叉树). Preserve ABI-compatible startup verification for optional native price/Greek backends and Python fallback. **Do not silently move IV inversion into native code or hot-switch backends.**
- **Computation:** One isolated IV Worker process for the first phase; no distributed/multi-Worker pool or second-level main-process IV cache.
- **Scheduling:** Risk-First Scheduling, Latest-Wins for pending and not-yet-started calculation work, bounded fairness for ordinary workloads. Do not cancel or roll back already-sent orders because of Latest-Wins.
- **Snapshot:** Submit one immutable option-contract + necessary underlying input snapshot per IPC batch; all outputs carry request generation and lifecycle identity so stale response cannot commit.
- **Writer:** Main event loop alone owns shared risk state and performs version-checked atomic commit. Worker does not directly mutate shared OptionData/portfolio risk fields.
- **Numerics:** Safeguarded Newton + bounded root-finding for applicable Black-76 / Black-Scholes implied volatility inversion, with explicit input legality, residual, convergence and risk sensitivity gates. Binomial-tree IV and existing model compatibility must be preserved; its solver-specific extension is not automatically approved by the Black model decision.
- **API:** Structured IVResult plus a backwards-compatible legacy interface. Internal IV is not rounded to display precision. Error/success statuses must be unambiguous.
- **Cache:** Worker-only exact-input bounded LRU, valid results only. Cache key includes all material pricing inputs and relevant solver/model configuration; never cache risk readiness or numerical failure. Actual key fields and capacity await audit.
- **Risk validity:** Bid/Ask/Mid independently valid; no fabricated midpoint when either necessary side is missing. Greeks validity and quote-version binding remain explicit. Common Hard Gates + Feature-Specific Risk Dependency Set determine Feature-Level Risk-Ready.

#### B. Market-data freshness and reconnect constraints

Record the Grill decisions without silently overriding repo safety policy:

- **Q36=A:** admitted new Tick arrival may renew the appropriate *arrival-based* TTL even with unchanged quote values.
- **Q37=B:** heartbeats, identifiable duplicate, out-of-order and replayed events do not renew.
- **Q38=A:** in a normal connected session, if the source cannot reliably prove event uniqueness, the chosen fallback trusts receive order for otherwise admissible normal Tick events. **This is not proof of source freshness.**
- **Q39=B:** reconnect/source switch starts quarantine.
- **Q40=A supersedes one clause of Q39:** release from quarantine after connection recovery and N consecutive accepted Ticks; strict independent live-sync proof is no longer the selected release condition. This is only an operational criterion, not evidence that replay ended.
- **Q41=B:** count per necessary market-data contract; MarketRecoveryReady of a feature requires all necessary feed dependencies recovered.
- **Q42=A:** one uniform positive integer N across contracts; no automatic thin-market relaxation or timeout release.
- **Q43=B:** counts are session scoped. Disconnect, source change and trusted session reset clear counts. Heartbeat and identifiable duplicate do not count and do not clear. Identified old out-of-order/replay breaks the sequence and clears counts. Late prior-session events never mutate current counters.
- **Q44=B:** recovery count must belong to the applicable official exchange trading day, not calendar date, and the N Ticks must be within finite window W.
- **Q45=PENDING:** the clock semantics of W are unresolved; cannot implement as an approved final time policy without a separate recorded decision.

**Mandatory compatibility guardrail from checked-in code:** The source Timestamp/Quote freshness gate, currently 30-second quote age with explicit future-skew checks, verified continuous-trading session policy, crossed/locked/empty/invalid book screening, quote-version matching, directional one-sided book behavior, source-switch reconciliation and price-limit checks remain hard gates. Q36/Q38 local receive order must **not** be interpreted as permission to overwrite an expired exchange timestamp or to bypass a rejected Tick. If new rules conflict with existing tests, record and resolve the conflict explicitly before enabling them in any auto-trading path.

**Specific observed conflict:** Current event ingestion rejects equal source timestamps even when quotes differ and rejects gateway source changes pending reconciliation. The proposed weak-source fallback and N-count release are not yet implemented and do not supersede these verified policies by assumption. Any change to their interaction is blocked on source-specific evidence, test design and a safety-reviewed implementation decision. Q40 release cannot substitute for existing operator reconciliation and durable audit after failed-close events.

#### C. Recovery state and invariants

- Recovery state identity is conceptually scoped to affected source/session/contract; exact fields await adapter inspection.
- Logical recovery formula: connection eligible AND same recovery session AND N accepted current-contract Ticks AND same official trading day AND within W AND no reset-triggering event.
- Feature's market recovery readiness = conjunction of recovery states of its actual necessary market-data dependencies.
- MarketRecoveryReady ≠ Feature-Level Risk-Ready ≠ authorization to transmit an order.
- Invalid required numerical output → no new normal risk-increasing order for dependent feature.
- IV cache hit does not renew arrival TTL or live trading permission.
- Lost Worker Generation, stale calculation snapshot or old session data cannot override current risk state.
- A new release after reconnect still requires existing session/quote/execution/position-order reconciliation gates.

#### D. Interfaces, compatibility, migration

- Align with existing OptionEngine event ingress, OptionData pricing functions, market-data-quality and risk-safety boundary, and existing automatic Delta Hedge/Electronic Eye order gates.
- Retain public model labels and persisted model_name settings, current backend startup selection and wheel packaging boundaries.
- Allow a controlled old/new computation-path comparison or rollback, but do not silently switch active pricing implementations during trading.
- Avoid schema migrations to persisted order/authorization state in Phase 1.
- Existing user-visible risk values may preserve last-known display values only with explicit invalid-for-trading flags.

#### E. Phase 2 decisions preserved, not implemented here

Q24–Q28: independently verified emergency risk reduction, pre-frozen exposure constraints, preauthorized templates, atomic reservation, in-flight exposure accounting, durable authorization ledger and gateway reconciliation.

Q29–Q33: persistent single-writer ownership, Fencing Epoch, actual gateway fencing, unified controlled Sender for submit/cancel/amend, cancellation priority, per-order causality, bounded gateway limits, staged backpressure and overload escalation.

Phase 1 only verifies/retains the existing necessary order safety gates; it does not claim to implement the entire Phase 2 architecture.

### Repository alignment: implementation boundaries and evidence (2026-11-22)

**Evidence baseline:** Compared with checkout HEAD `0ec421f9` on `fix/optionmaster-audit-20261122`. The items in this section are **observed current behavior** or **proposed Phase 1 contracts** as labeled; they are not claims of implemented Worker/recovery functionality. The locally present `docs/` files, including this spec, are currently **untracked** in Git. Existing tests: `.venv/bin/python -m pytest tests -q` passed **189** with **4 third-party deprecation warnings** before this documentation-only edit; this does not validate future behavior.

#### Exact integration map and collision resolution

| Boundary / concrete evidence | Checked-in behavior (VERIFIED) | Phase 1 delta / resolution | Status |
|---|---|---|---|
| `vnpy_optionmaster/base.py:158-252,281-300` (`OptionData`) | `calculate_option_impv()` calls Bid/Ask/Mid inversion synchronously, exposes three floats but only one `impv_valid` (Mid), and computes Greeks from Mid IV. `update_tick()` and `update_underlying_tick()` immediately recompute IV, Greeks and position Greeks. | Separate numerical validity **per side** and a full-precision `IVResult`; retain the legacy float `calculate_impv` public contract and last-known display values. Never treat an invalid or absent side as a tradable Mid. Avoid partial writes during async commit. | CHANGE REQUIRED |
| `pricing/black_76.py:156-196`, `pricing/black_scholes.py:160-200` | Both Python IV solvers already use bounded Brent root-finding, reject many invalid inputs by returning `0.0`, and **round successful IV to four decimals**. | Q4 is **not** a missing root bracket: add safeguarded Newton only if it passes the agreed numerical and performance gates; keep a bounded fallback, explicit failure reasons and full precision **internally**. Retain legacy rounding at the compatibility adapter, not at the risk-calculation boundary. | COMPATIBILITY / NUMERICAL GATE |
| `pricing/binomial_tree.py:210+`, `pricing/backend.py:86-128`, `base.py:314-318` | Binomial IV has different numerical semantics; optional Cython is startup-verified for **price/Greeks**, while `calculate_impv` remains Python and model callables are bound into `OptionData`. | Do not route binomial IV through a Black-model solver by accident, remove existing saved names, or hot-switch a backend in-flight. Freeze an explicit model/backend/solver contract per request. | HARD GATE |
| `base.py:336-343,440-464,672-683` | Every underlying Tick fans out through each chain to **all options**, with synchronous IV/Greek recompute, followed by chain/portfolio position-Greek aggregation. Trades and repricing settings also affect that aggregation. | Introducing an IV Worker without changing **fan-out completion and risk aggregation semantics** would leave mixed-version risk. Snapshot identity and main-owner atomic commit must cover option Tick, underlying Tick, model/rate/expiry/adjustment, and trade/position changes; numerics may be reused, but exposure must always be aggregated from current positions. | CRITICAL INTEGRATION SEAM |
| `risk_safety.py:49-66` | `risk_snapshot_is_fresh` validates Greek/mark flags, two timestamps, and the `greeks_quote_version` **Tick-object identity pair**. For an object missing this attribute it permits a `legacy_no_version` compatibility path. | New async snapshots **must** carry an explicit generation/version and fail closed if it is absent or mismatched. Do not silently equate the legacy compatibility path with verified snapshot identity, nor break existing test doubles without a migration plan. | RISK CONTRACT GAP |
| `engine.py:187-255`, `trading_sessions.py:99-139` | Ingress accepts only a verified continuous session, fresh source timestamps, interpretable book, strictly increasing timestamp on a fixed gateway; same-time identical/conflicting events, older events and source switch are rejected. `tick_rejections` already records reasons. | Q36/Q38 arrival TTL and receive-order fallback **cannot** quietly override this chronology rule. Source-proven sequence/reconnect signals and any relaxation need separate adapter evidence, explicit tests, and a reviewed decision; until then preserve the stricter behavior. | BLOCKED FOR WEAK-SOURCE FALLBACK |
| `trading_sessions.py:34-111`, `risk_safety.py:10-46` | `SessionPolicy.trading_day(vt_symbol, at)` derives official-day identity only from the applicable **verified continuous window**; source time freshness permits up to 30 s age and 2 s future skew, independently of receive time. No official calendar is synthesized. | Q44 recovery counters must use this verified trading day, not system date or inferred night-session date. Missing policy/day or Q45's unspecified W clock means recovery release stays closed; no guessed N, W or clock defaults. | PARAMETER / DATA BLOCKER |
| `base.py:580-611` | Synthetic underlying marks may be derived from a put/call pair and emit a synthetic Tick; this is **not** by itself a verified underlying feed/session or a guaranteed current underlying `tick` for `risk_snapshot_is_fresh`. | Model synthetic-underlying dependencies explicitly and keep automated risk closed until both version/freshness semantics and real executable order underlying are proven. Never grant recovery from a display-only synthetic price. | SPECIAL-CASE GATE |
| `engine.py:598-768` (`OptionHedgeEngine.run`) | Hedging checks held-option risk before calculating `portfolio.pos_delta`, but final pre-send check primarily revalidates the **hedge instrument's Tick/session**, then sends through `MainEngine.send_order` at multiple branches (close/open/partial close). | At **each existing send branch**, verify the still-current aggregated risk generation, all required option/underlying dependencies, positions, and final executable price; retain close reconciliation. Phase 1 does **not** create a universal sender. | REAL OUTBOUND SEAM |
| `algo.py:386-450`, `engine.py:942-993` (`ElectronicEye`) | `do_trading()` checks option risk; `OptionAlgoEngine.send_order()` checks tick/session/quote/price and checks option risk only when `algo.trading_active`. The latter delegates to `MainEngine.send_order`. | Revalidation belongs at the actual engine send boundary for **new automated orders**; distinguish active algo flow from other callers and retain direction-specific one-sided book rules. Do not assume all callers flow through `do_trading()`. | REAL OUTBOUND SEAM |

**Terminology and contract distinctions (proposed, not currently implemented):**

- `TickAdmitted`: ingress passed existing verified-session, source-time, quote-quality and chronology gates. **Not** proof of source live synchronization.
- `IVFieldValid(side, snapshot)`: accepted numerical result for exactly one of Bid/Ask/Mid, with explicitly stated status/residual. `Mid` requires a reliable two-sided mark; other side validity does not imply valid Greeks.
- `SnapshotCurrent`: all necessary quote identities, model/solver inputs, market-source generation and risk-relevant settings still equal those of the result; changed positions additionally require a **current reaggregation** even where cached IV is reusable.
- `MarketRecoveryReady(contract)`: after an eligible connection/session and Q40–Q44's same-day bounded-N recovery condition; its implementation and clock remain **blocked by Q45 and source-specific evidence**.
- `FeatureRiskReady(feature)`: the feature's declared dependency set has acceptable marks, current Greeks and necessary recovered feeds **plus the existing hard gates**. No one generic boolean substitutes for an explicit dependency map.
- `OrderSendAllowed`: **independent final check** at the concrete outbound path of feature readiness, current position/order state, verified trading window, direction, volume/tick-size, and **post-payup** limit/executability. Neither `MarketRecoveryReady` nor an IV cache hit grants this.

#### Small implementation slices (task-level granularity)

All IDs below are **proposed work packages**, not created Issues or completed code. Each package should fit in one reviewable change with a focused test seam; "blocked" means *do not guess the missing policy*. The dependency column is a prerequisite graph, not an arbitrary order.

| Work item | Scope and exact ownership boundary | Dependencies | Minimum acceptance / evidence |
|---|---|---|---|
| **OM-IV-P1-00** Baseline + flags | Pin baseline replay, active option-chain sizes, option/underlying Tick mix, actual IV vs Greek vs aggregate cost, event-to-usable-risk latency and current fallback behavior; fix a reproducible seed and machine profile. | — | Same offline feed can be replayed twice with counts, p50/p95/p99, max queue, CPU/memory and numeric-result statistics; no performance percentage claimed in advance. |
| **OM-IV-P1-01** Numerical contract | Introduce typed `IVResult`/failure taxonomy and Black-model safeguarded solver behind a **legacy float adapter**. Check no-arbitrage bounds, finite inputs, bracket, convergence cap, price residual and vega-sensitive error budget; keep binomial behavior separate. | P1-00 | `tests/test_pricing.py` and new solver-contract tests cover both CP directions, deep OTM, near expiry, invalid/no-root, low-vega and legacy rounding; native price/Greek backend selection unchanged. |
| **OM-IV-P1-02** Immutable request identity | Define primitive-only IPC payload and version identity: `vt_symbol`, side premiums, option/underlying accepted Tick keys, gateway/session generation, model/backend and solver revision, rate, TTE/expiry, strike/CP, adjusted underlying, pricing config. Keep live `OptionData`/`TickData` objects in owner process. | P1-01 | Same-price **new** Tick creates a new snapshot identity; changed rate, expiry, source, adjustment or backend invalidates old work. Numeric-key equivalence is distinct from risk-version equivalence. |
| **OM-IV-P1-03** One Worker lifecycle | Single isolated IV compute process; bounded request/response IPC, startup validation, explicit generation on restart, timeout/error paths, deterministic stop and conservative fallback/revoke behavior. No direct Worker state mutation. | P1-02 | Kill/restart/late-response tests demonstrate no old-generation commit, no stuck unbounded work, and no auto orders on uncertain risk. Preserve Python IV and Cython price/Greek split. |
| **OM-IV-P1-04** Scheduler | Owner-side Risk-First classification, pending-only Latest-Wins per contract/snapshot, bounded ordinary fairness, bounded pending work, queue overflow policy and observable counters. In-flight work may finish but obsolete responses cannot commit. | P1-02, P1-03 | Bursty active vs inactive contract replay proves finite queue, no unbounded starvation, no old risk publication and explicit overflow/fail-closed effects. Thresholds are frozen **before** performance sign-off. |
| **OM-IV-P1-05** Worker-local cache | Bounded LRU of **accepted IV numerics only**, keyed by every material scalar input and exact solver/model configuration; cache is cold after Worker restart. Cross-Tick hit is allowed only when numerical inputs match, not because timestamps match. | P1-01, P1-03 | Exact hit, altered premium/rate/TTE/model/solver, eviction, negative/NaN failure non-caching; a hit neither renews TTL nor sets `FeatureRiskReady`. Capacity/byte cap must be specified. |
| **OM-IV-P1-06** Main-owner commit + aggregation | Reconcile responses with current option/underlying generation and all risk-relevant settings, commit valid Bid/Ask/Mid independently, compute/refresh Greeks on current accepted inputs and recompute option→chain→portfolio exposure **coherently**. Invalidate risk immediately on new dependent Tick while old numerical request is pending. | P1-02–P1-05 | New option Tick, underlying fan-out, rate/expiry/adjustment change, fill/position change, partial IV failure, out-of-order response and replay all fail closed or yield a single current coherent aggregate; held Greeks remain display-only when invalid. |
| **OM-IV-P1-07** Real send-seam guard | Add/check feature-level dependency and current aggregate version at `OptionHedgeEngine.run` **all send branches** and `OptionAlgoEngine.send_order`; preserve existing session/quote/price/directional limits and cancel/reconciliation authority. | P1-06 | Extend `tests/test_hedge_orders.py`, `tests/test_algo_orders.py`, `tests/test_market_guards_regression.py`: alter dependent quote/position **after calculation but before send**; fake gateway observes **zero new orders**. Cancels and failed-close audit do not regress. |
| **OM-IV-P1-08** Reconnect/source adapter contract | Document concrete gateway event IDs, reconnect/disconnect/source-switch observability, verified session/trading-day identity, and whether source replay can be distinguished. Keep old chronology and source-switch rejection unless explicitly reviewed. | P1-00 | Fixture-level proof for each supported adapter; missing replay/uniqueness evidence is marked unavailable, not simulated as proven. `tests/test_tick_ingress.py` remains green. |
| **OM-IV-P1-09** Recovery counters and TTL | Add isolated operational arrival TTL and per-contract same-session/day `N`-within-`W` quarantine counters without replacing source timestamp freshness. Release remains disabled until Q45 clock, N/W/window algorithm and adapter evidence are frozen. | P1-08; **Q45 + recovery parameters BLOCK release** | Mock clock/session tests for midnight vs official trading day, break/reset/neutral events, sparse contracts, rapid replay, source switch and missing calendar; no auto release based only on N during unresolved policy. |
| **OM-IV-P1-10** Replay / rollout gate | Integrate numerical, scheduling, snapshot and both order seams in deterministic offline replay. Compare before/after **same workload** and define disable/rollback procedure with active-risk fail-closed semantics. | P1-00–P1-09 for full scope; P1-01–P1-07 for computation-only gate | No numerical safety regression, no unbounded memory/queue, no unauthorized orders; measured latency thresholds approved **after** baseline and before a performance Go. No production/CTP/Windows assertion. |

**Minimum review units:** A PR/ticket for P1-01 must not silently include P1-07 or P1-09. P1-06 is the only place that makes numerical results actionable in shared risk; Worker completion and cache insertion alone never do. P1-08/P1-09 can be deferred with the legacy stricter Tick ingress preserved and a documented, **disabled** reconnect-release path. If an item requires extending a common sender, exchange calendar or CTP reconciliation, separate that design from Phase 1.

#### Acceptance gates and explicit blockers

| Gate | Decision / evidence required | Fail-closed outcome |
|---|---|---|
| **G0: existing behavior** | Existing 189-test baseline and test fixture scope recorded; all original Tick, mark, expiry, backend, trading session, order, audit and wheel tests pass after any implementation. | No adoption if existing safety regression. |
| **G1: numerical** | Freeze model scope, residual/error/sensitivity tolerance, maximum solver steps and benchmark reference; compare high precision internal IV and legacy API independently. | Invalid/non-convergent output never becomes actionable Greeks. |
| **G2: async consistency** | Freeze request/response schema, source/model/position generation invalidation, lifecycle epoch, fairness and queue limits; prove cross-Tick cache reuse cannot resurrect prior risk. | Old or incomplete generation discarded; missing Worker disables new dependent orders. |
| **G3: recovery policy** | **Q45 W clock pending**; freeze positive integer N, finite W, sliding-vs-restart semantics, verified exchange trading day, gateway event identity and replay caveat. | Recovery release **off**, existing strict ingestion + operator reconciliation prevail; does **not** block isolated solver/Worker development. |
| **G4: real order boundary** | Verify both send paths under deterministically injected quote/position/session changes; preserve payup/tick-size/limit/volume, pending-close, cancel and durable audit rules. | Zero new dependent orders while any required dependency or risk aggregation is stale. |
| **G5: performance / release** | Approve beforehand p50/p95/p99, throughput, queue/memory ceilings and a defined representative workload; provide repeatable before/after measurements, rollback drill and explicit operational sign-off. | Mark performance **UNVERIFIED** if baseline/targets absent; offline green != CTP/Windows/live authorization. |

**Source/test traceability:** `tests/test_tick_ingress.py` (chronology and session), `tests/test_mark_quality.py` (mark and exact quote identity), `tests/test_risk_freshness.py` (held-option/underlying freshness), `tests/test_market_guards_regression.py` (order/book/sessions), `tests/test_order_terminal_states.py` and `tests/test_recovery_audit.py` (reconciliation/audit), `tests/test_pricing.py`, `tests/test_pricing_backend.py`, `tests/test_cython_pricing_consistency.py`, `tests/test_build_artifacts.py` (model and wheel). Proposed P1 worker/scheduler/cache/recovery tests do **not** yet exist. The repo has no checked-in `CONTEXT.md` or `docs/adr/` to which this spec could automatically inherit additional obligations; neither is invented.

### Testing Decisions

**Test philosophy:** Prefer observable, stable system boundaries over Worker internals. For the complete spec, test locally with mocked gateways, deterministic clocks and recorded/offline market-data replay; never connect to production CTP, send real orders, deploy, or enable live trading.

#### Primary high-value seams

1. **Event ingress → option/portfolio risk state:** Send controlled Tick events through the actual option event-ingress boundary and observe accepted/rejected updates, IV/Greeks validity, computation generations and feature readiness.
2. **Pricing/Worker request → result commit:** Use an isolated numerical model/reference and controllable Worker responses to verify numerical parity, cache reuse, scheduling, failure recovery, stale-result rejection and main-thread-only risk commit.
3. **Automated algorithm → mocked order gateway:** Run Delta Hedge / Electronic Eye via real algorithm entrypoints with a fake gateway to verify that only valid current risk, valid session and executable final order price can produce a new order.

Avoid hardcoding private implementation details in tests where behavior can be verified at these seams.

#### Numerical scenarios

- Independent reference solver for in-/at-/out-of-the-money, near-expiry, high/low volatility, both option directions, invalid/no-solution cases and tolerance-sensitive inputs.
- Black-76 and Black-Scholes pricing inversion; binomial-tree current behavior and legacy interfaces protected from regression.
- Partial Bid/Ask/Mid errors; absent bid/ask must not invent a midpoint.
- Numeric failure, NaN/Inf, convergence cap, residual failure and risk sensitivity budget.
- No native backend ABI regressions; verified Python fallback with IV inversion still Python.

#### Scheduling, cache and concurrency

- Bursty multi-contract events: risk priority, pending Latest-Wins and bounded ordinary fairness.
- Exact-key hit, near-miss, model/config mismatch, LRU eviction, invalid-result non-caching, Worker restart cold cache.
- Cache hit across Tick versions still requires current risk-version validation.
- Delayed Worker results, restart/crash, generation/epoch mismatch, concurrent inputs and queue saturation must not corrupt shared risk.
- Compare before/after p50/p95/p99 Tick-to-risk-commit latency, numerical compute time, cache hit rate, IPC overhead, queue depth, memory peak and failure recovery using equivalent workloads. No numeric targets were agreed.

#### Market-data ingress and reconnect

- Fresh vs stale/future source timestamps; missing session calendar/invalid official trading window; crossed/locked book; valid directional one-sided quotes.
- Identifiable duplicates, equal-timestamp conflict, out-of-order Tick, replay, source switch requiring reconciliation.
- Unchanged-price but genuinely new Tick; source identity absent and local-order fallback must not override existing timestamp/session/book gates.
- Per-contract recovery N, session resets, invalid-event reset/neutral events, late previous-session response, calendar trading-day boundary and finite window W.
- Night session crossing calendar midnight but not exchange trading-day boundary.
- Deliberate rapid replay of N Ticks that can cause false operational recovery: explicitly demonstrate and report known residual risk; do not claim strict live-sync safety.
- W/clock and N parameterization tests remain blocked until values/semantics are frozen.

#### Order and operational regressions

- Invalid or stale Greeks prevent all relevant normal auto orders at the actual outbound seam.
- Final post-payup limit price, tick size, directional bid/ask executability and verified continuous session constraints remain enforced.
- Cancel requested ≠ cancel confirmed; no releasing failed-close block without appropriate reconciliation and fsync-backed audit.
- Worker or market failure does not silently enable new orders; necessary cancels and reconciling responsibility remain intact.
- No real CTP/Windows/production claim from local test success.

#### Existing repository test prior art

The repository already contains tests targeting pricing backend selection and Cython numerical consistency, tick ingress, market guards and verified trading sessions. Extend those known tests rather than creating a second unrelated safety test stack. Run all relevant project-defined lint, type-check, dependency, build, and test checks only when implementation work is authorized, reporting actual outcomes.

**Spec validation:** Confirm all decided behaviors are represented, unknowns remain marked TBD, existing safety invariants are not silently weakened, and Phase 2 is excluded.

### Out of Scope

- Full rewrite or replacement of the order sender, submit/cancel/amend state machine or gateway ownership management.
- Implementing Fencing Epoch and confirmed external gateway fencing in this phase.
- Durable emergency authorization ledger, in-flight budget and crash-safe order intent processing.
- New live CTP trading, real order submission, deployment or activation of live auto hedging.
- Production-grade CTP race reconciliation, Windows production qualification or official calendar sourcing as automatically completed features.
- A multiple-Worker/distributed IV compute cluster or two-level IV cache.
- Unreviewed modification of established trading-session, quote quality, user audit or price-limit safety requirements.
- Auto-tuning N by liquidity, timeout-based release, using time elapsed to assert source live-sync, or treating replay counter as strict sync proof.
- Invented parameter targets and performance-improvement percentages.

### Further Notes

#### Decision ledger

- Q1=B Risk-First; Q2=B Latest-Wins; Q3=A Exact Input Cache; Q4=A Safeguarded Newton; Q5=B Numerical Acceptance; Q6=B Internal Full Precision; Q7=A Fail-Closed (supersedes original Q7=C); Q8=B Layered Tolerance; Q9=B IVResult + Adapter.
- Q10=A Single Writer; Q11=C Single Worker First; Q12=B Bounded Fairness; Q13=B Bounded LRU; Q14=A Bounded Recovery; Q15=A Isolated Process; Q16=B Per-Contract Snapshot Batch.
- Q17=B Field-Level Validity; Q18=B Common Hard Gates + Dependencies; Q19=B Feature-Level Risk-Ready; Q20=B Order-Time Revalidation; Q21=B Risk-Aware Cancellation; Q22=B Post-Recovery Release; Q23=B Exposure-Based Reconciliation.
- Q24=B Independent Emergency Risk Reduction; Q25=B Frozen Constraints; Q26=B Emergency Templates; Q27=B Atomic Reservation; Q28=B Durable Ledger; Q29=B Single-Writer Ownership + Fence; Q30=B Single Sender + Gateway Fence; Q31=B Unified Commands; Q32=B Cancellation Priority/Causality/Rate Limit; Q33=B Staged Backpressure. These are reserved for Phase 2 except necessary existing gate preservation.
- Q34=B Worker-Local Exact LRU; Q35=B Numerical/Risk Version Separation; Q36=A Tick Arrival Renewal; Q37=B Validated Tick; Q38=A Local Receive Order Fallback; Q39=B Reconnect Quarantine; Q40=A Consecutive N Tick Release supersedes strict sync-proof clause of Q39; Q41=B Per-Contract Counters; Q42=A Uniform N; Q43=B Session/Event Resets; Q44=B Official Trading Day + Bounded W; Q45=PENDING; **Q46=B Phase 1 IV Performance + Risk Snapshot Correctness.**
- Grill closed: no new interview rounds are required or authorized.

#### Blocking TBD parameters and evidence for implementation planning

| Priority | Item | Required before |
|---|---|---|
| P0 | Q45: W clock type (monotonic elapsed / trading-session elapsed / dual) | Final reconnect recovery acceptance |
| P0 | Uniform N, finite W, window algorithm (sliding/restart), day-boundary source | Enabling new reconnect release behavior |
| P0 | Exact IV pricing key, solver tolerances, convergence and risk sensitivity budget | Numerical acceptance |
| P0 | Real feature-level option/underlying risk dependency map and risk version identity | Automated risk gating |
| P0 | Actual order send entrypoints and preserved existing safety evidence | Any new auto-trading path |
| P1 | Worker IPC protocol, lifecycle epoch, timeout/retry budget and LRU size | Worker implementation |
| P1 | Deterministic baseline workload, p95/p99 latency and resource thresholds | Performance acceptance |
| P1 | Exact source/adaptor semantics for reconnect, trading day, session change and weak Tick identity | Market data recovery test coverage |
| P2 | Persistent Sender/Fencing/emergency authorization design details | Separate Phase 2 specification |

#### Existing implementation conflicts requiring an explicit resolution step

1. **Local-order fallback vs existing chronology rejection:** Current ingress rejects same-timestamp conflicting messages and source switches. No implicit override; preserve current hard gates until reviewed.
2. **Arrival TTL renewal vs exchange timestamp freshness:** A just-received old Tick must still fail existing timestamp freshness checks. Distinguish arrival-based operational TTL from exchange-source quote freshness.
3. **Tick-count release vs established failed-close recovery:** Q40's operational quarantine release does not replace existing human reconciliation, audited unlock, or verified trading session requirements.
4. **Model-specific solver work vs Cython boundary:** Current IV inversion is Python, even when native price/Greeks is selected. Keep this split unless separately authorized after parity verification.

#### Rollout and authority

This is a source-grounded planning artifact, not a completed implementation or a finding of production readiness. Code, commit, push, PR, deployment, production market-data connection and live order actions each require appropriate separate authorization under project instructions. The GitHub repository currently has Issues disabled; therefore this spec is saved as a local project Markdown artifact, **not** published as a GitHub issue. Existing unrelated untracked project docs must remain intact.

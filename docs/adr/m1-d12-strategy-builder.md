# ADR M1-D12 — T 型期权报价策略分析器

Status: **Accepted product decisions (Q1–Q15, 2026-11-25); proposed engineering implementation, not implemented or release-approved.**
Decision owner: OptionMaster operator (用户). Predecessors: M0 T05/T07/T08, M1-D01–D11. Companion: m1-d12-strategy-builder-spec.md and task DAG.

## Context

D09 catalog / React v3 and D11 research-only NumPy CRR are working on independently approved archived CU data. Current UI selects discrete contracts and requests pre-expiry pressure tests. Operator wants T-chain interactive strategy construction and expiry P&L, historical ClickHouse coverage and true risk metrics. Legacy combination requires 2–8 options, strict pre-expiry clock, IV eligibility and theoretical mark change; changing those in place would break validated contracts. D03 candidates are unverified cross-table reads and prior server caps failed; D11 archival performance is not Q17. Preserve all previous source pins and live trading paths.

## Decision

| ID | Choice | Accepted decision |
|---|---|---|
| Q1 | B | 同期限精确到期 P&L；跨期限最早到期日条件 P&L，已到期腿 intrinsic，剩余腿用已验证研究模型。 |
| Q2 | B | 共同百分比冲击、独立交割月调整；保留各真实标的价格，覆盖替换后网格增量相加。 |
| Q3 | B | T 型表直接编辑 signed quantity；加减、输入、选中同步；0 移除。 |
| Q4 | B | 默认准入结算价（仅在已批准整体收盘 fallback 时标注）；每腿 entry 可改，与 market reference/IV 独立。 |
| Q5 | B | ClickHouse 历史数据，最新完整日、历史回溯、多品种/月份；仅真实批准范围。 |
| Q6 | B | 当前理论、情景、终端三线；跨期限第三线标为条件 P&L。 |
| Q7 | B | 净权利金、最大盈利亏损、盈亏平衡点、收益风险比与 Greeks；精确/无界/条件区分。 |
| Q8 | B | Payoff 与 model 独立准入；IV 不可识别不阻塞精确 payoff；不允许缺失腿按零。 |
| Q9 | B | 自动联动、确定性优先、模型防抖异步、版本防陈旧；安全有限缓存。 |
| Q10 | B | 跨月保留，跨日选择重新构建或保留原成本重估，品种切换确认；单日行情。 |
| Q11 | B | 左 T 型链、右 P&L/风险/持仓、分栏可调；高级压力测试保留。 |
| Q12 | B | Sticky Strike 与绝对 IV 全局/分期限冲击，沿用覆盖语义。 |
| Q13 | B | 跨日重估止于最早到期前；只作最早到期假设分析，非生命周期。 |
| Q14 | C | 1..8 个期权腿及对应期货/现货混合持仓；固定线性标的 P&L，支持静态对冲。 |
| Q15 | C | UI、计算、ClickHouse 数据接入并行开发，共享契约，一次集成交付。 |

Implement additive research strategy contract (proposed v4), separated deterministic payoff, verified quote snapshot/admission and existing model valuation. Old v1/v2/v3 exact requests, model labels, clock/guard, grid/caps, archive and advanced pressure-test semantics remain valid; no retroactive rewrite of old ADRs. D07/D09 default workspace decision superseded only for the **new authorized release**, not currently running service. New payoff/horizon semantics do not relax the old M0 T05 pre-expiry guard: hypothetical expiry is a separate evaluator, earliest cutoff verified; date-only nominal far-leg clock separately approved, otherwise unavailable. True terminal payoff is conditional on holding until expiry, not settlement. Price-risk domain explicit; cross-month common path is conditional, nonlinear range extrema not global. Portfolio entry vs valuation snapshot distinct; no IV recalibration from entry price or quantity. D10 memo request-local, D11 128-step kernel unchanged, no live backend switch. Additional cross-request derived-value caching optional only with exact fresh identity/revision checks and bounded storage; no raw market snapshot reused as fresh. UI never silently fills missing prices or unsupported Greeks.

D03 history: exact-key readonly bounded transport and trusted registry/admission reused, not widened automatically to all chain rows. Read real schema/limits again under separately authorized scope, build coherent versioned provider, expected roster and independent trust; Q16 and data licensing/coverage remain hard gates. Incomplete or ambiguous source returns CANDIDATE/UNAVAILABLE, no new self-approval. D09 independent catalog pin retained; verified archive is a fixture, not the final real data release.

**Q17/Q18 reconciliation:** keep original M0 diagnostic workload 2/4/8 and 11x11,21x21,31x31 with fresh real read + admission/IV/pricing/serialization/render and cold/warm repeats, CPU/RSS/errors/phase p50/p95 and independent signed operational budgets. Preserve v1–v3 HTTP cap 11 curve/5x5; larger diagnostic runs do not change public cap. Add new actual strategy workload and browser response/rapid-edit evidence. Historic D11 improvements do not certify Q17; no final GO without real validated envelope. This is an engineering acceptance mechanism supporting confirmed Q15, not newly authorized database or cost scope.

## Alternatives considered

- In-place extension of v3 and expiry guard relaxation: rejected; changes validated DTO/semantics and cache/model failure behavior.
- Same-expiry only / full lifecycle: rejected Q1 B/Q13 B; separate horizon conditional result sufficient without settlement ledger.
- Unified absolute price across delivery months or mandatory 2D curve: rejected Q2 B; shared percent plus explicit override preserves differences.
- Manual picker/recalculate, fixed entry premium, strict IV-only admission: rejected Q3/Q4/Q8/Q9.
- Archive-only or realtime source, dynamic IV surfaces, multi-workspace persistence, probability/risk distribution: not selected Q5/Q7/Q10/Q12.
- Vertical phased product releases or data-first: rejected Q15 C. Contract-first dependency layers allow parallel branch work, no partial product publish; private tests are necessary, not separate releases.

## Consequences and boundaries

More UI state, distinct entry vs market accounting, analytic risk metrics and per-capability typed errors; shared schema/fixtures prevent independent branch drift. Cross-expiry and cross-month risk explicitly conditional. New spot/index/ETF source and terms must be verified; mixed future domain first real supported family, spot only after independent admission. Real DB role caps, version proof, rate/time/domain, model axes/debounce and resource budgets unresolved and must be signed/verified at dependent gates. No production writes/profile mutation, live engine/trading, Docker/public exposure, unapproved Git publication or service restart. No implied approval of new dates or actual data access. Documentation only now.

## Validation

The companion Spec gates G01–G07 and machine-readable tasks define observable numerical, data admission/Q16, compatibility, UI, performance/Q17 and authorized release checks. Preserve prior 198 record D09 scope, 482 discovery-only, exact D10/D11 evidence and current local service until cutover approval. No GO from compile/tests alone. Review staged candidate with actual Chromium on target scope, rehearsal rollback, independent authorization and verified post-start listener/health. Until implemented and verified, status: **new strategy release NO-GO; existing archived research unchanged**.

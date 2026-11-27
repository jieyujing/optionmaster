# ADR M1-D12 — T 型期权报价策略分析器

Status: **Accepted product decisions (Q1–Q15, 2026-11-25); proposed engineering implementation, not implemented or release-approved.**
Decision owner: OptionMaster operator (用户). Predecessors: M0 T05/T07/T08, M1-D01–D11. Companion: m1-d12-strategy-builder-spec.md and task DAG.

**2026-11-25 06:36:22Z amendment:** [SQLite-first / ClickHouse-later ADR](../evidence/m1-d12/sqlite-first-revision/ADR.md) is accepted for local research development sequencing only. It supersedes early isolated CH setup, not Q5 or final actual ClickHouse/Q16/Q17/G01–G07 and independent release gates. Existing completed work and historical approvals remain intact; no implementation or data writes authorized by this amendment.

> **2026-11-25 11:25 UTC / 北京时间19:25 — 用户确认只读消费者边界（当前有效，优先于此前 T04/A1/SQLite-first 文件中冲突的 ClickHouse 前置门禁）。** 复用已提供的既有 ClickHouse 目标与 quant 账号；用户确认账号权限足够。OptionMaster 只查询数据库内已存在的行情，不要求 QuantFeed 停写、全局 Writer/锁/INSERT/未决发布/待写入检查、生产者 manifest 或专用代理/角色/opaque Reader 回执作为读取前置条件。旧 CP68/A1/SQLite 证据保留为历史，不再作为 D12 读取阻塞。Reader 绑定仅指服务端既有目标+账号的安全凭证引用与实际 SELECT 可用；无需新建账号、改权限、专用角色或读取/披露密码。使用固定参数 SELECT、已授权表/列/键、分页及请求/扫描/超时/结果限制；优先查询级保护，不改持久 profile/settings；不得把 LIMIT 或客户端超时单独当作扫描上限。无法安全限制的查询应收窄或拒绝。账号权限由用户确认，连通性/schema/实际 SELECT 尚待 T04 验证，本文件不冒充运行证据。既有数据读取不需要逐日生产者静止证明；仅在数据完整性可证时称 latest complete，其他可见数据明确 PARTIAL/COVERAGE_UNKNOWN，缺失/冲突的选中腿不可估值。既有精确历史 pin 不改，保留 terms/mark/basis、Q16 消费者快照验证、Q17 实测预算、全量回归及独立发布授权。禁止任何生产写入、GM/API新采集、权限/服务修改、Git发布或实盘操作。本次仅修订文档与Goal，未执行数据库请求。

**T04 实施记录（2026-11-25 11:32 UTC）：** `quant` 账号权限为用户确认，未实测。既有 LAN 目标上的 `optionmaster_history` 是此前已获授权且已配置的只读账号，本次通过安全凭证引用认证、实际资源限制、表结构及代表性 EXPLAIN 核验；可复用这一现有通道，不要求专用账号、角色或新增绑定，也不改变 quant 可选地位。仅研究诊断，不冒充生产 TLS 或应用端连接验收。实际记录见 `docs/evidence/m1-d12/T04-consumer-read-verification-20261125.md`。

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

D03 exact-key SELECT transport and verified registry are reused, not widened automatically. Existing quant account is operator-confirmed sufficient; only actual connection/SELECT, needed schema/key/fields and query safety must be verified, not global Writer state or a separate Reader role. Consumer Q16 freezes actual consumed rows, checks exact identity, required fields, duplicate/conflict and selected snapshot coherence, reports incomplete/unknown coverage and observable versions; no producer manifest or global atomicity claim. Allow valid selected rows from partial days with explicit coverage status; missing/ambiguous selected data unavailable. D09 pin is retained; archive fixture does not replace actual CH verification.

**Q17/Q18 reconciliation:** keep original M0 diagnostic workload 2/4/8 and 11x11,21x21,31x31 with fresh real read + admission/IV/pricing/serialization/render and cold/warm repeats, CPU/RSS/errors/phase p50/p95 and independent signed operational budgets. Preserve v1–v3 HTTP cap 11 curve/5x5; larger diagnostic runs do not change public cap. Add new actual strategy workload and browser response/rapid-edit evidence. Historic D11 improvements do not certify Q17; no final GO without real validated envelope. This is an engineering acceptance mechanism supporting confirmed Q15, not newly authorized database or cost scope.

## Alternatives considered

- In-place extension of v3 and expiry guard relaxation: rejected; changes validated DTO/semantics and cache/model failure behavior.
- Same-expiry only / full lifecycle: rejected Q1 B/Q13 B; separate horizon conditional result sufficient without settlement ledger.
- Unified absolute price across delivery months or mandatory 2D curve: rejected Q2 B; shared percent plus explicit override preserves differences.
- Manual picker/recalculate, fixed entry premium, strict IV-only admission: rejected Q3/Q4/Q8/Q9.
- Archive-only or realtime source, dynamic IV surfaces, multi-workspace persistence, probability/risk distribution: not selected Q5/Q7/Q10/Q12.
- Vertical phased product releases or data-first: rejected Q15 C. Contract-first dependency layers allow parallel branch work, no partial product publish; private tests are necessary, not separate releases.

## Consequences and boundaries

More UI state, distinct entry vs market accounting, analytic risk metrics and per-capability typed errors; shared schema/fixtures prevent independent branch drift. Cross-expiry/month risk explicitly conditional. Spot/index/ETF only within verified coverage. Retain bounded read scope/actual schema, consumer data validation, rate/time/domain and model/performance budgets; user-confirmed existing account needs no new role/profile or producer pause. No production writes/profile mutation, live engine/trading, public exposure, Git publication or service restart. This revision changes documents only; actual reads are not yet verified.

## Validation

The companion Spec gates G01–G07 and machine-readable tasks define observable numerical, data admission/Q16, compatibility, UI, performance/Q17 and authorized release checks. Preserve prior 198 record D09 scope, 482 discovery-only, exact D10/D11 evidence and current local service until cutover approval. No GO from compile/tests alone. Review staged candidate with actual Chromium on target scope, rehearsal rollback, independent authorization and verified post-start listener/health. Until implemented and verified, status: **new strategy release NO-GO; existing archived research unchanged**.

### T06 当前历史研究范围（2026-11-25 用户决策）

T06 仅消费既有 ClickHouse 历史行情，不接入实时 GM/CTP/WebSocket，不采集、补录、同步行情，不连接交易或持仓。复用原 T04 已验证边界，不以 QuantFeed 状态为前置条件。收敛为四个内部交付切片，不新增 DAG 节点：
- T06-A 历史目录：显式有限 portfolio/date window，固定 SQL 与实测计划及预算，目录候选不冒充已验证报价。
- T06-B T 型报价：按已验证日期、合约身份/条款与标的读取，分批查询；缺失/冲突明确不可用。
- T06-C 历史分析快照：每次授权观察冻结已消费行、来源/版本/读时及口径；复用现有 payoff/model/Greeks，允许部分覆盖有效腿；不声称全局事务或原始日 PIT。
- T06-D 集成验收：离线 Provider/HTTP 模拟与既有 API/Archive 回归，另需已授权真实 CH 选中数据验证、跨范围 Q16 及后续 T09 UI 联动。

本轮离线实现已完成，实际 CH 新请求为零；原 12/12 额度耗尽，不自动启动连接或扩大范围。仅可在另获真实读取授权、明确范围及预算后执行真实观察。T06 仍 in_progress、G03/Q16 未通过、T09–T14 独立，生产 NO-GO。证据：docs/evidence/m1-d12/T06-historical-provider-offline-r66-20261125/REPORT.md。

## 2026-11-25 12:13:52 UTC — 用户授权修订（当前有效）

取消此前人为设定的 CH 累计次数限制及普通只读查询逐批审批。既有目标和已授权账号允许自主进行历史行情发现、分页、固定参数 SELECT/EXPLAIN、Q16 与现有资源范围内验收，不再以历史 12/12 流水为停止条件。原流水与历史证据保留不改。保留单请求 100000 扫描行/16 MiB、5 秒、64 MiB 内存、64 行/1 MiB 结果、throw、客户端 8 秒及串行、失败停止、不自动重试；每批仍有明确的有限计划及防重复执行记录，属于工程保护而非新增审批。禁止数据库写入、DDL、权限/profile/settings 变更、GM/CTP/实时/QuantFeed 探测、服务切换或 Git 发布。独立发布门禁及超出现有资源边界的操作保持原约束。

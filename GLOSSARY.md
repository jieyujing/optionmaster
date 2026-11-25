# OptionMaster glossary

Scope: M1-D02 historical contract validation. Existing M0 terms and units remain
defined in `docs/m0-m1-interface-handoff.md`; this is not a replacement for
that contract.

| Term | Definition |
|---|---|
| Source key | Exact identity within a named source namespace: namespace, exchange label and symbol. Case, separators and prefix are significant. |
| Exchange-native option identity | Explicit reviewed exchange and contract code of the actual option, distinct from its vendor/source key. |
| Canonical underlying identity | Explicit exchange, product family, native contract/spot ID, delivery month and option asset class. Futures have a specific year/month; index/ETF use an exact spot ID, no delivery month. |
| Alias attestation | Independently reviewed exact source-key-to-canonical-underlying correspondence for an explicitly bounded effective interval. A symbol-format rule is not an attestation. |
| Contract terms row | Raw source observation of option right, exercise style, strike, multiplier, expiry, underlying key and version on one trading date. Not authoritative by itself. |
| Verified terms | Reviewed exact option identity, underlying identity and dated terms for a unique version. Product-level rules alone cannot certify a particular contract version. |
| Contract version | Identifier scoped to one exact option source key and one effective interval; a repeated identifier or overlapping active versions are ambiguous. |
| Effective interval | Inclusive historical date range for which an attestation or terms record is applicable; not the date the data was ingested or reviewed. |
| Review receipt | Source publisher, precise record locator/ID/revision, artifact digest, reviewer and review timestamp for an independently reviewed record. A receipt's structural validity is not proof of authenticity. |
| Read time | Aware instant of the current materialized request; a source/receipt recorded after it is invalid. Does not establish historical publication-time availability. |
| Validated historical contract | Complete result of exact identity, date, version and terms agreement with a trusted registry, preserving raw terms and all reviewed records; only this can construct a D02 selection for D01. |
| UNAVAILABLE | Explicit refusal with reason code, no selected contract or usable partial terms. Never a zero, estimated, inferred, or forward-filled value. |
| Expiry date | Exchange contract expiry calendar date. A date-only terms record does not establish an intraday exercise/valuation cutoff; M0 T05 still enforces its separate time boundary. |

Boundary examples: two similar strings with no reviewed alias are not the same
contract; a reused short code may refer to different delivery months in
disjoint intervals; a product's historical American-exercise rule cannot
establish a missing exact option version.

## M1-D09 historical catalog terms

| Term | Definition |
|---|---|
| Contract-date record | Exact source option key qualified by one historical trading date; the same instrument on two dates is two records, not two instruments. |
| Dated chain scope | One trading date and one exact underlying delivery month, with explicit vendor identities, official artifact digests and bounded source observation. |
| Catalog candidate | Canonical aggregate of exact reviewed child packet digests; approval is an independent operator receipt, not a request field. |
| Admitted historical input | Approved dated terms and paired price marks; does not guarantee identifiable positive IV or production valuation. |
| Discovery-only record | Officially observed contract not admitted for this dated source scope; unavailable for portfolio selection. |
| Cross-expiry portfolio | 2–8 distinct exact option keys on a common trade date and supported family, possibly spanning delivery months. Each leg has its own maturity and underlying mark. |
| Earliest-expiry guard | Exclusive date-only limit: all selected legs must have expiry date strictly after the scenario date. |
| Underlying exposure group | Signed monetary Greeks aggregated only for a single exact deliverable underlying, never a fabricated cross-month Delta/Gamma total. |
| Configured scenario | Explicit common scenario clock and optional exact underlying/expiry overrides. Grid shocks are additional to this configured scenario; origin is its value change relative to the historical baseline. |

## M1-D12 strategy builder terms (accepted design; not implemented)

The definitions below extend, not replace, M0 and M1-D02/D09 contracts.
The legacy Cross-expiry portfolio and Earliest-expiry guard remain applicable
to v1–v3 and pre-expiry valuation. Strategy expiry payoff is a separate
hypothetical evaluation. For precise decisions and constraints see the
M1-D12 ADR/Spec.

| Term | Definition / invariant |
|---|---|
| Strategy Builder | T 型期权链为主要入口的策略工作台；不是实际交易终端。 |
| Advanced Analysis | 保留既有理论价值变化、价格/IV/时间压力测试；不冒充 entry P&L。 |
| Option Chain | 按已验证交割标的、行权价及 Call/Put 展示的期权链，缺失侧不是零。 |
| Signed Quantity | 正数多、负数空、零移除；同 exact identity 不重复。 |
| Selected Leg | 策略中非零净持仓的精确合约。 |
| Option Leg | 按权利金建仓、非线性收益的期权持仓；第一阶段 1–8 腿。 |
| Underlying Leg | 经过验证的对应期货合约或现货单位持仓；线性 P&L，期货名义金额不是权利金。 |
| Strategy Portfolio | 期权腿及可选对应标的腿，同族且使用一个估值快照；不表示实盘账户。 |
| Mixed Portfolio | 含期权和已验证对应标的腿的 Strategy Portfolio。 |
| Static Delta Hedge | 固定标的数量改变组合 Delta，不包含动态再平衡路径。 |
| Quote Snapshot | 单品种、单交易日、可验证版本/来源/时钟/口径的一次一致性行情物化。 |
| Latest Complete Trading Day | 在批准范围内满足 roster、数据完整、口径和版本一致的最近交易日；不是简单 MAX(date)。 |
| Market Data Provider | 受信任配置的只读行情/条款/覆盖提供者；CANDIDATE 不等于准入。 |
| Market Reference Price | 已准入估值参考行情，用于基准模型及 IV 校准；非成交价。 |
| Entry Premium / Entry Price | 单位假设/用户建仓价格；默认批准的行情口径，可改但不改变市场 IV。 |
| Net Premium | 期权 Σ qMe，正为净支付、负为净收取；不含期货名义本金。 |
| Entry Snapshot | 原始建仓日期、成本、合约身份与来源。 |
| Valuation Snapshot | 当前重估唯一一致 Quote Snapshot；不混合不同日行情。 |
| Revaluation | 保留 entry 成本及有效 exact 持仓，用新快照估值；缺失不静默删除。 |
| Revaluation Boundary | 最早到期前允许重估；不得将已到期原组合继续重估。 |
| Analysis Horizon | 假设计算时点；终端默认最早到期，非实际结算时刻。 |
| Expiry P&L | 同期限 terminal intrinsic 减入场成本与线性标的价值变化合计；gross theoretical。 |
| Horizon P&L | 在指定时点相对原始成本的组合盈亏，可含远月剩余价值。 |
| Conditional Horizon P&L | 依赖明确价格联动/IV/剩余时钟的最早到期或其他条件盈亏；非全路径保证。 |
| Hypothetical Settlement | 仅策略假设的 terminal intrinsic；不实际行权、结算、交割或生成持仓。 |
| Expired Portfolio | 至少一腿已越过原组合重估边界，须停止原组合重估。 |
| Current P&L Curve | 当前 snapshot 时钟与锁定 IV，随价格冲击变化的理论 entry P&L。 |
| Scenario P&L Curve | 指定时钟与价格/IV 条件下的理论 entry P&L。 |
| Terminal P&L Curve | 同期限精确 payoff，跨期限则明确标为 first-expiry conditional。 |
| Remaining Time Value | Horizon 时未到期腿的模型价值减其内在价值（模型/假设限定）。 |
| Reference Underlying | 曲线参考的 exact 标的；不把不同交割月强制相等。 |
| Parallel Price Shock | 对各自基准标的统一的 decimal return；非同绝对价。 |
| Underlying Override | 精确标的的独立配置冲击，替换全局配置后再加曲线冲击。 |
| Sticky Strike | 价格变化时保持各 exact option 基准 IV；并非动态 smile 拟合。 |
| Parallel IV Shock | 全局绝对 decimal σ 变化；UI pp。 |
| Expiry IV Override | 精确 expiry group 冲击替换全局配置；不是相对 IV 百分比。 |
| IV Term Structure | 不同 verified expiry 的 IV 结构。 |
| Payoff Eligibility | 已准入精确条款及有效成本满足确定性 payoff；不要求 IV。 |
| Model Eligibility | 另外满足模型、mark、IV、时间、利率和单位条件。 |
| Partial Availability | 仅独立能力可用；缺失腿所属的聚合结果 UNAVAILABLE/null 而非零或部分总和。 |
| Payoff Engine | 按内在价值和线性标的计算确定性 P&L、分段根/极值的纯领域引擎。 |
| Valuation Engine | 已验证研究定价模型、IV 和 Greeks，用于仍有期限的腿；不改实盘。 |
| Max Profit / Max Loss | 指定声明价格域中的最大理论盈利/亏损；亏损显示正 magnitude 或带清晰符号，不把区间极值当全局。 |
| Breakeven | P&L 为零的点或区间；含平段和多根。 |
| Unbounded Risk | 声明域内理论亏损无有限上界，使用 typed 状态，不用 JSON Infinity。 |
| Conditional Extremum | 假设/价格区间下的极值，不声明无条件全局边界。 |
| Portfolio Revision | 任一经济输入变化的单调版本；与已验证输入摘要/快照匹配。 |
| Stale Result | 非当前版本/输入的结果，不得作为最新值展示，即使数值碰巧相同。 |
| Unified Strategy Contract | 共享快照、持仓、计算能力状态/单位和版本契约。 |
| Parallel Delivery | 共享契约后并行任务依赖，不是多 Agent 权限或多次发布。 |
| Integrated Acceptance | 真实批准数据→领域→API→浏览器全链路验收。 |
| Release Gate | 数值、Q16 数据、Q17 性能、兼容/安全/运行、回滚与授权均通过后发布。 |

# OptionMaster M1-D03 — Registry admission and bounded historical repository

**Date:** 2026-11-24. **Scope:** offline, source-bound futures-option candidate
materialization, not a live ClickHouse transport or real-data valuation GO.
Goal `wc_goal_Cq0v47RcDiOqdSfW`, session `wc_sess_lYn0WFZVsgWXtY5n`.
D02 `wc_goal__6lvDtCobriCe1P0` verified completed revision 7.
ADR: `docs/adr/m1-d03-pinned-registry-and-read-candidate.md`.

## 1. Actual schema and read admission evidence

Connected quantfeed metadata confirmed these ReplacingMergeTree tables:

| Table | Actual sorting key | Partition key |
|---|---|---|
| option_contract_terms_daily | portfolio_code,trade_date,chain_id,symbol | toYYYYMM(trade_date) |
| option_daily | symbol,trade_date | toYYYYMM(trade_date) |
| futures_daily | product_symbol,trade_date,symbol | toYYYYMM(trade_date) |
| trading_calendar | market,trade_date | none observed |

D01/D02 documented the actual columns and exact 2022-09-07 A2211 samples;
D03 revalidated the fixed parameterized query shapes with the real server
using EXPLAIN ESTIMATE and server-bound parameters, without executing market
SELECTs.

| Query target | Bound key(s) | Estimated parts / rows / marks |
|---|---|---|
| terms | portfolio_code XDCE.A2211.XDCE, date 2022-09-07, symbol A2211-C-5000.XDCE | 1 / 8192 / 1 |
| option | date 2022-09-07, symbol A2211-C-5000.XDCE | 1 / 7513 / 1 |
| futures | product_symbol DCE.A, date 2022-09-07, symbol DCE.a2211 | 1 / 8421 / 1 |

These are **estimates, not actual scanned rows, elapsed time, consistency or
coverage evidence**. Queries filter the actual sorting-key prefixes; terms
checks chain_id after reading to detect conflicts within the chosen portfolio
scope rather than excluding alternative chain rows. All use LIMIT 2 to detect
visible duplicate rows; they cannot recover versions already removed by
background merges or establish uniqueness across other routing scopes.
No FINAL, latest-wins, broad JOIN, dynamic identifiers or interpolated values.
This follows the intent of query-join-filter-before and actual-key pruning.

### Real server profile vs proposed repository ceiling

| Setting | Observed | Required by D03 code |
|---|---:|---|
| readonly | 1 | exactly 1 |
| max_rows_to_read | 0 | positive <= 100000 |
| max_bytes_to_read | 0 | positive <= 64 MiB |
| max_execution_time | 0 | positive <= 5 seconds |
| max_result_rows | 0 | positive <= 64 |
| max_result_bytes | 0 | positive <= 1 MiB |
| read_overflow_mode | throw | throw |
| result_overflow_mode | throw | throw |
| timeout_overflow_mode | throw | throw |

**Actual role fails the resource gate.** Values of zero are unrestricted,
not zero cost. Server resource checks can occur at block/part granularity
and are not an absolute physical cap; the transport must also enforce a
client deadline. These ceilings are conservative code admission limits for
a narrow development slice, not observed production SLA or authorization to
change settings. The role previously refused ad hoc settings changes.
No setting mutations, data reads beyond metadata/EXPLAIN, database writes
or new connections in this task.

Official technical references, checked 2026-11-24:

- [ClickHouse SQL syntax / query parameters](https://clickhouse.com/docs/reference/syntax)
- [Restrictions on query complexity](https://clickhouse.com/docs/concepts/features/configuration/settings/query-complexity):
  zero means unrestricted, throw raises an error, break can return partial
  results, and query-limit checks may occur after processing parts.
- [ReplacingMergeTree best practices](https://clickhouse.com/blog/10-best-practice-tips):
  merges are asynchronous; a visible single row does not prove historical
  uniqueness. D03 deliberately rejects visible duplicates instead of hiding
  them using query-time deduplication.

## 2. Evidence admission contract

`vnpy_optionmaster/research_registry_admission.py`:
`registry_fingerprint(registry)`, `admit_registry(registry, approval, artifacts, read_at)`.
Versioned canonical JSON digest covers registry version, ordered records and
their values, including receipts. Externally configured RegistryApproval
must pin that digest and carry a reviewer identity and aware approval time.
Every referenced artifact must be supplied as nonempty actual bytes, its
SHA-256 must match the receipt, and the artifact digest set must match
exactly. Review <= approval <= read instant. Reject missing approval, changed
manifest, missing/extra/tampered bytes, invalid receipts, future dates or
oversized input. Processing ceilings: 2048 records and 32 MiB referenced
artifact bytes. D02 performs exact semantic validation of active records.

This checks **integrity relative to a separately trusted pin**, not authenticity
of a vendor document, issuer or reviewer. A self-generated pin does not turn
unreviewed data into evidence. Operator must verify source bytes, exact
contract identity, record/revision, dates, terms and adjustment/version
history, then independently approve the pin and settlement-provider policy.
No real pin, evidence corpus, registry or default approved source ships.

Additional public discovery: [Eastmoney Quant futures data documentation](https://emquant.18.cn/help/doc/data/%E6%9C%9F%E8%B4%A7.html)
describes the DCE market and exact symbol metadata query interface; a
[contemporary broker launch notice](https://www.cafut.cn/default/view/34924a60bABCD.html)
corroborates A2211 among the launch months. These are indexed public
documentation/notice results, not a retrieved exact historical A2211
record or versioned cross-provider correspondence. Prior D02 evidence
ledger remains controlling. No paid/vendor account query was run.
The real A2211 terms row still has UNKNOWN exercise style and blank version;
no real registry or verified alias has been invented. The real-shaped
negative fixture explicitly returns MISSING_CONTRACT_VERSION.

## 3. Implemented repository contract

`vnpy_optionmaster/research_history_repository.py`:
`HistoricalRepository.materialize(HistoricalReadRequest)`.
Constructor receives an operator-supplied ReadOnlySelect transport,
candidate registry, external approval and artifact bytes, explicit allowed
settlement sources (empty by default), and a clock. No arbitrary SQL,
registry pins, provider allowlist or consistency waiver in a read request.

- 2–8 distinct option legs, one date, exact portfolio/chain/option and
  underlying lookup keys. Namespace convention is quantfeed.option/<source>
  for terms and option quotes, quantfeed.futures/<price_source> for underlying
  quotes. Returned provider, exchange, symbol, date and product/chain must
  match exact keys. The futures option terms schema lacks a separate
  underlying-exchange column; use the same source/exchange namespace only
  for this futures-option contract and require D02 alias verification.
  No case/prefix/month inference. Index/ETF not enabled here.
- Before any transport call: validate input, verify registry integrity,
  require known option and underlying quote keys and explicitly approved
  settlement-provider label. D02 later resolves date/terms/version.
- Before any market query: SELECT exact settings on same operator-configured
  server/session/profile. Check resource ceilings and overflow throw.
  Fixed SELECTs with typed {symbol:String}, {trade_date:Date}, and bound
  portfolio/product parameters. No server settings changes. Maximum 25
  SELECT calls (1 settings + 3*8), at most 2 result rows per market query.
  Deadline 5 s per call / 20 s read sequence, passed to transport; late
  returns rejected and no further reads. A real transport must actually
  enforce the deadline, preserve the trusted same server/profile and
  supply complete results; code cannot interrupt a blocking arbitrary
  external implementation. No retries or hidden scans.
- Fetch raw terms once per leg, D02 verifies each before quote reads.
  Reject native option identity collision and canonical underlying
  ambiguity. One quote read per option and one per unique underlying.
  Preserve source provider, settlement provider, timestamps, registry
  digest, full D02 lineage, portfolio/chain and raw materialized quotes.
  Use D01 unchanged to choose whole-combination settlement or close fallback.
  Missing column/invalid price differs from NULL settlement; no partial
  results, no forward fill or per-leg mixed price basis.
- `CANDIDATE` means successful diagnostic materialization only.
  `HistoricalCandidate.valuation_ready` is always False and
  `consistency` is UNVERIFIED_CROSS_TABLE. D01's internal OK is not a
  production authorization. All failures return UNAVAILABLE with a reason
  and no candidate. No pricing/risk-grid bridge or actual client connection.

Current project venv has no clickhouse_connect or clickhouse_driver.
D03 adds no dependencies and no concrete live transport. The fake transport
exercises the actual SQL/parameter dispatch and actual D02/D01 logic; the
real server EXPLAIN independently confirms SQL shape. It does not prove
runtime live connection, actual resource enforcement or cross-table consistency.

## 4. Verification and worktree protection

New tests:
- `tests/test_research_registry_admission_m1_d03.py` (8).
- `tests/test_research_history_repository_m1_d03.py` (44).
All positive registries and raw rows are test-only synthetic. Existing
D01/D02 tests unchanged. Initial REDs: missing new module (exit 2).
Registry GREEN: 7 passed, mypy mixed-tuple error corrected, then passed.
Repository GREEN: 43 combined D03 tests initially passed. Adversarial RED:
three failures (unknown option/underlying lookup dispatched, unapproved
settlement provider self-selected); fixed lookup and provider admission.
Deadline RED observed a further query attempt after late transport return;
fixed elapsed per-call gate. Focused final: **165/165 tests passed**
(52 D03 + 113 D01/D02), focused Ruff/mypy passed.

Actual full Runner Job `wc_job_aHc0KCcOtpIRN8ed`, terminal exit code 0:
**896/896 pytest passed** in 28.95 s, full Ruff PASS, mypy 35 source files
PASS, 67 dependencies compatible, offline sdist/wheel built, artifact tests
3/3, git diff --check passed. Four existing pandas deprecation warnings.
Commands:
```sh
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/mypy vnpy_optionmaster
uv pip check --python .venv/bin/python
uv build --offline
.venv/bin/python -m pytest tests/test_build_artifacts.py -q
git diff --check
```
Reviewed actual new code diff; corrected trailing blank lines in two new
tests before full regression. Original worktree: main, HEAD
`0a7b3f5269ba7696db0a2a5cf2301d5ee3d10d8c`, 25 untracked status paths
(27 individual files). Baseline aggregate SHA-256
`069355f2ecd2439bcff89de17b39b0bc84c4ac5d1a5f6704d617451fcc62acc6`
matched after code implementation. Final closeout verifies baseline and
all six new paths; no pre-existing files modified. No Git commit/staging,
push/merge/deploy, DB writes, CTP, orders or paid service.

## 5. Go/No-Go and next dependency

| Gate | Status |
|---|---|
| D01 and D02 offline boundaries | GO; unchanged |
| D03 pinned admission and candidate materialization seam | Scoped offline GO (real SELECT shapes verified, execution fixtures only) |
| Exact A2211 alias / versioned contract source record and approved actual registry | NO-GO |
| Server profile / concrete client transport / real scoped SELECT | NO-GO |
| Q16 coherent cross-table revision, duplicate coverage beyond selected keys, official trading calendar and intraday expiry cutoff | NO-GO |
| Real-data coverage ledger, IV/risk-free inputs, pricing bridge, Q17 performance, API/UI and production | NO-GO |

Next M1 task: secure independently reviewed exact source records and
operator-approved artifact pins; obtain separately authorized bounded
read-only server profile and actual driver transport; validate a narrow
real slice with official calendar and a defensible revision/consistency
boundary before promoting candidates to valuation inputs. Do not bypass
the profile or forge registry entries to unblock tests. No D04 or
production rollout is started by closing this task.

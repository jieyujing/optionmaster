# OptionMaster M1-D04 — Real-data admission evidence and HTTPS transport

Date: 2026-11-24.
Goal: `wc_goal_m0Img7vMXPHJYba6`; Session: `wc_sess_dRBaxqPrZr9zNFeq`.
D03 `wc_goal_Cq0v47RcDiOqdSfW` verified completed revision 7.
D04 remains **active / real-data NO-GO** until independent evidence, safe
connection and coherent real slice are verified. This is not a deployment.

## 1. Actual ClickHouse metadata and consistency gate

Observed via the existing read-only quantfeed connector; no new remote
connection or market table SELECT was made.

- Server version 26.5.1.882, timezone UTC.
- `enable_shared_storage_snapshot_in_query=1`. Shared storage snapshots
  improve single-query table/reference consistency, not business-atomic
  publication across independently updated tables.
- Actual option/futures/terms ingestion timestamps have type
  `DateTime64(3, 'Asia/Shanghai')`, not naive UTC. Output default is
  `date_time_output_format=simple`; server timezone is not the column timezone.
- Actual engines and sorting keys:

| Table | Engine version | Sort key |
|---|---|---|
| option_contract_terms_daily | ReplacingMergeTree(ingested_at) | portfolio_code,trade_date,chain_id,symbol |
| option_daily | ReplacingMergeTree(ingested_at) | symbol,trade_date |
| futures_daily | ReplacingMergeTree(ingested_at) | product_symbol,trade_date,symbol |
| trading_calendar | ReplacingMergeTree(updated_at) | market,trade_date |

All three market tables partition monthly; calendar has no observed
partition key. Full relevant column metadata was inspected; no common
publication/revision field is present. A bounded metadata name search
found coverage tables for stock/index 1m, not a shared publication
manifest for these three target tables. This is not an exhaustive claim
about all external producer systems.

Current role settings:

| Setting | Observed | Required by D03/D04 |
|---|---:|---:|
| readonly | 1 | 1 |
| max_rows_to_read | 0 | positive <= 100000 |
| max_bytes_to_read | 0 | positive <= 67108864 |
| max_execution_time | 0 | positive <= 5 s |
| max_result_rows | 0 | positive <= 64 |
| max_result_bytes | 0 | positive <= 1048576 |
| read_overflow_mode | throw | throw |
| result_overflow_mode | throw | throw |
| timeout_overflow_mode | throw | throw |

max_memory_usage also 0. Zero means unrestricted in these observed caps,
not zero resource consumption. Role fails preflight; do not run market
queries under it. EXPLAIN and metadata only. Resource checks can occur at
part/block granularity; client timeout/response budget are defense in depth.

**Q16:** An ingested_at timestamp is a per-row ingestion/version marker, not
an independently attested common release or historical publication time.
Do not use same-day filters, FINAL, latest-wins, a shared SELECT snapshot,
or even two matching observations alone to assert business consistency.
Recommended next producer contract (requires separate authorization):
an independently published release manifest for the requested date/scope
with exact contract/price/terms/calendar keys, version/digests and completion
marker. Publish only after the intended versions are complete, and verify
the rows against the approved manifest. If ReplacingMergeTree has discarded
a referenced version, reject rather than silently substituting latest.
Alternatively obtain explicit operator-controlled stable-export evidence
for an authorized narrow validation slice, including the producing source
and coherence guarantees; that does not establish all-history coverage.
No writer or schema changes here.

References:
- https://clickhouse.com/blog/clickhouse-release-25-06 — shared storage snapshot
- https://clickhouse.com/docs/reference/data-types/datetime64 — epoch/column timezone
- https://clickhouse.com/docs/concepts/features/interfaces/http — HTTP sessions,
  headers, response buffering and 200-with-exception caveat
- https://github.com/ClickHouse/clickhouse-docs/blob/main/docs/integrations/interfaces/http.md
  — HTTP typed param_name binding and Escaped parameter semantics

## 2. Contract and calendar evidence ledger (not a registry)

| Source, date and retrieval | Actual finding | Admission limit |
|---|---|---|
| [CSRC, 2022-07-29](https://www.csrc.gov.cn/csrc/c100028/c4915225/content.shtml), directly retrieved | A/B/Y option products listed from 2022-08-08 | Official launch only, not particular option ID/version |
| [Futures Daily via CFA, 2022-08-08](https://www.cfachina.org/industrydynamics/mediaviewoffuturesmarket/202208/t20220808_29386.html), indexed content | A2211 included at launch; American style; named initial examples A2211-C-5100 and A2211-P-6600 | Contemporary corroboration, not an exact A2211-C-5000 source row; initial strike examples do NOT prove later 5000 invalid |
| [DCE notice 2022-420 reproduced by broker](https://www.fnqh.com.cn/newsInfo/11550.html), indexed content | A2211 among initial delivery months | Not independent provider alias or version |
| [DCE notice 2022-496 reproduced by Sina, 2022-09-27](https://finance.sina.com.cn/money/future/fmnews/2022-09-27/doc-imqmmtha8948688.shtml), indexed content | October 1–9 closed; October 10 resumes; Sept 30 no night session | Supports holiday-window inference; no approved complete calendar/cutoff artifact |
| [DCE public daily data page](https://www.dce.com.cn/dalianshangpin/xqsj/tjsj26/rtj/rxq/index.html), attempted direct retrieval | 502 Bad Gateway in web retrieval | No exact raw data bytes; do not bypass access controls |
| [AKShare option documentation](https://github.com/akfamily/akshare/blob/main/docs/data/option/option.md), indexed | Documents DCE historical option endpoint and HTTP 412 limitation | Corroborates acquisition limitation; not exchange data |
| [SHFE historical copper option contract, dated 2020-08-03](https://www.shfe.com.cn/products/option/nonferrousmetal/cu_o/standard_cu_o/202401/t20240103_331328.html), directly retrieved | Revised American-exercise rule applies from underlying delivery month CU2211; prior months use prior rules | Primary evidence of delivery-month-dependent historical rule; not a DCE alias or per-contract record |
| [SHFE later copper contract, 2022-12-02](https://www.shfe.com.cn/products/option/nonferrousmetal/cu_o/standard_cu_o/202401/t20240103_331329.html), indexed | Later rule text, with strike-coverage change effective 2022-12-12 | Current/late rule cannot be applied retrospectively |

SHFE public candidate ContractBaseInfo URLs for 2022-09-07 and 2022-11-15
were not accessible through web retrieval. No authenticated paid/vendor
queries, no scraping workaround, no actual artifact bytes admitted. D02
A2211 sample still contains UNKNOWN style and blank contract_version; actual
source mapping `A2211.XDCE` to `DCE.a2211` remains unverified. Evidence
strength and byte integrity differ: a search extract and its hash are not
an authentic exchange artifact. Do not self-approve or synthesize a registry.

Next evidence packet, one narrow trading-date slice first:
- authoritative dated exact call and put instrument records, native code,
  underlying identity, source/exchange/vendor crosswalk, strike, multiplier,
  expiry, exercise style and actual version/adjustment history;
- source-native rows and actual evidence artifact bytes, URLs/record IDs,
  digest, historical effective dates, independent reviewer and approval;
- dated official trading-day/expiry rule and actual close/exercise cutoff
  (do not conflate market close and exercise-request cutoff);
- explicit settlement provider policy and independently approved registry
  fingerprint per D03. If raw contract_version remains absent, it remains
  UNAVAILABLE; approved upstream repair or reviewed raw-source contract
  revision policy is a separate task, not an invented version.

## 3. Concrete transport implementation

`vnpy_optionmaster/research_clickhouse_http.py`:
`ClickHouseHttpSelect` implements D03 ReadOnlySelect with stdlib
HTTPSConnection. No new dependency. No default host/credential,
no network operation in constructor, no client activation. Requires
operator-supplied authorized TLS endpoint, hostname verification and trusted
expected server display name; credentials are provided in memory and never
included in URLs or errors. One transport instance / one materialization,
single connection and HTTP session; subsequent calls session_check=1,
no automatic retries or reconnection. No arbitrary SQL/role/settings
overrides. Trusted composition code must create/close one instance per
request and must not expose raw transport as a public API. Endpoint name,
TLS and session checks detect obvious drift, not proxy topology or
multi-table publication proof.

Only D03's four exact logical SELECT shapes are accepted. Reuse predicates,
sort-key prefixes, LIMIT 2 and bound {symbol:String}/{trade_date:Date} and
portfolio/product parameters (HTTP param_* URL encoding, no SQL interpolation;
reject backslashes/whitespace and malformed dates). Profile-first gate
independently checks the D03 ceilings, no market read on unsafe profile.
Max 25 total SELECTs, 5 s/call, 20 s/sequence, 2 MiB HTTP body bound.
Socket timeout and monotonic checks reject late returns, but stdlib socket
timeouts do not prove an absolute wall-clock bound on DNS/header handling
or server-side cancellation. Actual direct TLS endpoint/session/limits,
timeout behavior and no unintended retry must be verified before real use.

Wire projections use exact IEEE754 Float64 bit strings:
`toString(reinterpretAsUInt64(column))` plus `toTypeName(column)` tags;
Nullable(Float64) preserves SQL NULL while NaN/Infinity keep distinct bits.
Timestamp projection uses `toString(toUnixTimestamp64Milli(ingested_at))`,
source type guard and aware UTC conversion with exact integer milliseconds.
JSON strict complete body, response status/content type/format/server name,
exception code, content framing, size, duplicate keys, exact row count,
projected field/schema/actual source type guards. Unknown type or partial
response fails closed, not a fabricated row. NaN/Infinity passed as such
to real D02/D01, which refuse invalid price/terms. No exception detail or
partial candidate emitted. D03 CANDIDATE/valuation_ready=False unchanged.

Actual CH constant probe without market table reads:

| Probe | Actual observed value |
|---|---|
| toTypeName(toString(reinterpretAsUInt64(CAST(NULL,'Nullable(Float64)')))) | Nullable(String) |
| isNull(nullable projection) | 1 |
| Float64(772) bits | 4650001799632125952 |
| NaN bits | 9221120237041090560 |
| Infinity bits | 9218868437227405312 |
| 2026-10-11 21:38:54.395 Asia/Shanghai epoch ms | 1791725934395 |

The three exact final SQL shapes with projected fields/guards were checked
using real EXPLAIN ESTIMATE, bound keys and the D02 date sample:
terms 1 part/8192 rows/1 mark, option 1/7513/1, futures 1/8421/1.
No market SELECT executed. Estimates are not measured scan/latency.

## 4. Operator-only profile approval proposal (NOT EXECUTED)

Do not alter the existing shared account. A separately authorized admin
should provision a dedicated least-privilege read role/account and direct
TLS endpoint, restrict network access, choose secure secret distribution
outside Git/chat, confirm server identity and effective grants. Proposed
role/profile names are examples; check collisions and inherited settings
before any mutation. The only requested production grants are SELECT on
the exact D03 projected/filtered columns (three market tables) and
system.settings(name,value), no database-wide write or administrative
privilege. Read-only calendar columns market,trade_date,is_open,source,
updated_at may be separately authorized when calendar admission is ready.
No grant on secrets/system.users, broad data or other service.

Reference syntax:
https://clickhouse.com/docs/reference/statements/create/settings-profile
and https://clickhouse.com/docs/reference/statements/grant.

Proposed fixed setting constraints (admin review required; never executed
by the repository or current connector):

```sql
-- PROPOSAL ONLY. A separately approved dedicated role must already exist.
CREATE SETTINGS PROFILE optionmaster_history_bounds
SETTINGS
    readonly = 1 CONST,
    max_rows_to_read = 100000 CONST,
    max_bytes_to_read = 67108864 CONST,
    max_execution_time = 5 CONST,
    max_result_rows = 64 CONST,
    max_result_bytes = 1048576 CONST,
    read_overflow_mode = 'throw' CONST,
    result_overflow_mode = 'throw' CONST,
    timeout_overflow_mode = 'throw' CONST
TO optionmaster_history_reader;
```

Recommend independently evaluate bounded memory, concurrency and quotas,
not guessed production SLA. Confirm actual effective values, auth and
network/TLS path on the dedicated account. No altering existing users,
roles, tables, settings, or data in this task. Do not test write denial
by performing write attempts on production data. A separate approval must
authorize creation/configuration and actual small live read (one date,
two options and one exact underlying, within the above limits; at most
six normal materialization SELECTs plus separately approved diagnostic
metadata, zero paid calls), and the independent evidence packet must pass
before market SELECTs. No blind retries. Then compare real returned field
types/values, profile/session and read evidence, lineage/consistency and
calendar. Record GO only for observed verified slices; no real valuation,
Q17, API/UI or release approval follows from connection success alone.

## 5. Validation and preservation

New tests `tests/test_research_clickhouse_http_m1_d04.py`: 27 test cases.
RED missing module; first 19 tests pass, Ruff UP012 corrected. Expanded
RED two concrete failures (mutable TLS context disabled before connection,
huge timeout raised OverflowError); fixed TLS recheck and guard ordering.
Focused real .venv command: 192/192 passed (27 D04 + 165 D01–D03),
Ruff and mypy passed. Test transport replaced only the external HTTPS
boundary; full D03/D02/D01 fixture integration returned CANDIDATE with
settlement pairs (772,5772), (21,5772), six calls and one connection.
All test-only values, no live TLS or data/authority claim.

Full regression Runner `wc_job_yHQmULWDhXwqzaHW`: observed terminal exit 0;
**923/923 pytest passed** in 28.44 s; four existing pandas warnings, full
Ruff PASS, mypy 36 source files PASS, 67 packages compatible, offline wheel
and sdist built, artifact tests 3/3 PASS, tracked diff whitespace check PASS.
This validates the implemented code with offline fixtures, not a live
server TLS session, actual registry admission or cross-table consistency.

Original 33 individual untracked files, main HEAD
`0a7b3f5269ba7696db0a2a5cf2301d5ee3d10d8c`, aggregate SHA-256
`2e892eeea7cc19171e1af7d3e3b7d1c913a4fbdc252ab58d40cc1e23d7eb35b1`,
matched after implementation. No tracked/staged changes. Final verify
new-file whitespace and original digest before checkpoint.

## 6. Status

| Gate | Status |
|---|---|
| Concrete HTTPS transport and schema-specific wire boundary | scoped offline verification PASS; no live connection proof |
| Exact independently reviewed contract/version/alias + approved registry | BLOCKED: exact records/bytes/approval missing |
| Official complete trading-day/expiry boundary for real slice | BLOCKED |
| Dedicated bounded readonly server profile + actual live TLS session | BLOCKED: existing role has unbounded limits; independent authorization missing |
| Q16 business-coherent release and real coverage | BLOCKED: no common publication evidence |
| Real-data valuation/API/UI/deploy | NO-GO |

Goal remains active. Next safe action is acquire reviewed source records
and obtain explicit authorization for the dedicated read-only profile/
endpoint and exact small sample. Continue independent evidence acquisition
when available; do not add unnecessary abstractions or weaken validation.
No database mutation, Git stage/commit/push/merge, deploy, CTP, orders,
paid API or credentials exposed.

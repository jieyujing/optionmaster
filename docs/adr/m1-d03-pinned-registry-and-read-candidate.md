# ADR M1-D03 — Pinned registry admission and non-valuatable read candidates

**Status:** accepted for the scoped offline M1-D03 implementation, 2026-11-24.
**Predecessor:** `m1-d02-historical-contract-attestations.md`.
This does not approve a production registry, connection, or valuation.

## Decision

- Keep operator-supplied approval pins and settlement-provider policy outside
  historical read requests. Require canonical registry SHA-256 and the actual
  bytes of every referenced evidence artifact. No pin generated from an
  unreviewed candidate can establish trust; the external reviewer must first
  establish source authority, exact record applicability and historical version.
  Hashes prove integrity relative to an independently trusted pin, not authenticity.
- Repository accepts only fixed typed-parameter SELECT statements through an
  operator-injected ReadOnlySelect transport. The transport is a trust boundary:
  it must use genuine server parameters, same server/session/profile, full
  non-truncated rows and enforce client deadline without hidden retry. No
  driver, production credentials, live connection, profile mutation or
  production registry/allowlist is bundled. Profile settings are observed at
  request time and must satisfy explicit positive ceilings; overflow modes
  must throw. A read-only role alone is insufficient.
- Read at most two visible rows per exact physical key to detect ambiguity;
  do not conceal duplicates with FINAL, ANY JOIN, argMax or latest-wins.
  This cannot recover versions already merged away or detect duplicates
  outside the selected routing scope; Q16 remains a separate gate.
- Validate every leg's D02 terms and identity before price reads. Deduplicate
  identical underlying reads by full canonical identity and exact source
  binding; reject repeated native option identity and alias conflicts.
  Materialize once per request, pass one in-memory bundle to D01, and retain
  both price_source and settlement_source. No cross-request history cache.
- Define CANDIDATE, not OK, as successful diagnostic materialization:
  `valuation_ready=False`, `consistency=UNVERIFIED_CROSS_TABLE`. A candidate
  is not a certified coherent historical snapshot, IV input, or accepted
  valuation. Failure returns UNAVAILABLE with no partial candidate.
- Initial contract is futures options only, 2–8 distinct legs, exact date and
  namespaces quantfeed.option/<source> and quantfeed.futures/<price_source>.
  The terms and option quote tables must agree on exact provider, exchange,
  symbol and date; no symbol rewrite or inferred cross-provider identity.
  Product and chain lookup keys are explicit. Index/ETF require separately
  verified spot schema and source contract.

## Why not return OK?

ClickHouse ReplacingMergeTree merges can discard old versions and separate
SELECTs are not proof of a shared multi-table revision. Even a complete
D01 mark after valid D02 terms does not satisfy Q16 or C1–C6. The conservative
candidate boundary allows testing and inspection without equating simulated
or unverified real marks with valuation-ready data.

## Consequences

An actual live adapter requires independently approved exact registry
artifacts and settlement policy, restricted server-side caps, trusted
transport, official trading-day/cutoff and cross-table consistency proof.
Real coverage, valuation and performance gates remain NO-GO. See
`docs/m1-d03-historical-repository.md` for schema, limits and evidence.

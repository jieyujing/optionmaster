# ADR M1-D02 — Exact historical contract attestations before quote pairing

**Status:** accepted implementation within M1-D02 scope, 2026-11-24.
**Context:** M0 and D01 are local offline GO, M1 real-data remains NO-GO.
D01's `mapping_provenance` is an asserted string, not a verified registry.
The observed ClickHouse option terms reference `A2211.XDCE`, whereas the
futures table uses `DCE.a2211`; the raw terms have `UNKNOWN` exercise style
and an empty contract version.

## Decision

- Resolve each exact source key through dated, separately reviewed alias
  attestations. Compare full canonical identities, including exchange, family,
  native ID, year/month (or spot ID) and asset class. Require one active match;
  no case changes, prefix parsing, guessed calendar/month or nearest contract.
- Resolve an independently reviewed exact option record, including native option
  exchange/code, underlying identity, right, strike, multiplier, expiry,
  exercise style and contract version. Require one active version; repeated
  version IDs for the exact source option and active overlaps fail closed.
  Validity intervals are inclusive dates. Futures options' native exchange
  must agree with their underlying exchange.
- Compare the materialized raw daily terms exactly. Only a literal UNKNOWN
  style can be replaced by a reviewed style for the same verified version;
  a known disagreement or blank version is UNAVAILABLE. Date-only terms on
  expiry day do not authorize intraday valuation; retain M0 time policy.
- Keep the trusted registry separate from raw terms and consumer requests.
  Evidence must be exact-contract, with source locator, record/revision,
  artifact SHA-256, reviewer, timestamp. Runtime verifies receipt shape,
  applicability, and cross-record consistency, not remote authenticity or
  reviewer authority. Registry admission and evidence-byte/hash verification
  are separate real-data gates. No production registry is bundled from
  incomplete observations; no fixture is approved for production.
- Return the raw row, three reviewed records, registry version, date/read time,
  and D01 HistoricalLegSelection. The D02 SHA-256 provenance fingerprint binds
  the selected records; it is not a signature. Future adapter must bind
  actual quote reads to exact namespaces, preserve full D02 lineage and
  reject duplicate canonical options across legs; D01 APIs remain unchanged.

## Alternatives rejected

String normalization and broad product defaults silently join different
contracts/versions. Latest-ingested-wins or first-row-wins hides duplicates.
Retrofitting D01 or the M0 pricing models would mix identity authority with
quote basis and calculation; the new validator is the upstream seam instead.

## Consequences and gates

A missing reviewed alias or historical version produces UNAVAILABLE even if
a human judges the codes likely equivalent. This is deliberate. Actual source
admission, historical coverage, official trading calendar, snapshot/version
consistency, risk-free/IV provenance and live latency remain M1 work. See
`docs/m1-d02-historical-contract-terms.md` for observations, source grades and
acceptance evidence. No production or trading authority is granted by this ADR.

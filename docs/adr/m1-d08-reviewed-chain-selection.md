# ADR M1-D08 — Independently reviewed archived chain selection

Status: accepted and locally verified for the independently operator-approved
exact archived CU2211 chain, 2026-11-24. Non-production only.

## Decision

Separate discovery, source comparison, approved historical input and
model valuation. Do not require one approval per strike. One independent
content pin may cover all exact bindings in the observed CU2211 chain.
This is not a global missing-version repair or a license to extrapolate
to other dates/products/months. No source data or old D04 approval changed.

- Discover native copper option instruments by joining dated official
  contract-base and daily records on the exact official instrument ID;
  use explicitly recorded underlying, right, strike, listing, expiry,
  multiplier and prices. Exclude the official subtotal row.
- In this observed one-day scope, compare the complete CU2211 chain
  to the dedicated bounded read-only JQ data: exact terms, vendor
  catalog, option quote, underlying quote, calendar and raw provider
  provenance. Match using explicit identity fields and official records,
  not inferred symbol spelling. Preserve raw UNKNOWN exercise style and
  blank raw version. The official historical rule for CU2211 supplies
  the reviewed effective exercise style; never extrapolate that rule
  to the other discovered contracts.
- An exact canonical packet digest binds all 66 comparisons, actual
  observation and five original official artifacts. A separately
  supplied operator SliceApproval with matching digest and chronology
  is required. Rebuild candidate from original bytes on admission.
  Original D04 approval covers only its original pair/archive; it does
  not approve this new observation. The operator explicitly approved the
  D08 canonical pin at 2026-11-24T08:25:56Z; separate recorded approval
  is `operator_chain_approval.json`.
- Selection uses distinct explicit source symbols, 2..8 legs, same
  observed underlying, date and chain. Preserve request order; derive
  D02/D03 registry with effective content-addressed versions after
  approved packet verification; revalidate historical marks through
  D01. Missing/mismatched/unapproved evidence fails closed. Official
  discovery outside the approved scope has no vendor valuation identity.
- Reuse D05 archived rate, nominal clock, American binomial IV/Greek
  and scenario/grid logic. Small internal admission seam allows D08
  to use its own approval checker with max 8 legs; D05 public behavior
  stays exactly two legs and its original D04 checker. Neither path
  accepts a caller-provided pre-admitted result as authorization.
- Approval of input evidence is NOT proof all IVs are identifiable;
  two official observed puts have settlement equal to intrinsic.
  The numerical engine must fail closed on unavailable calibrations.

## Scope and integration

This implementation handles the pilot date 2022-09-07, official copper
discovery six maturities, CU2211 full-chain comparison. Other dates,
maturities, products and cross-month strategies are not yet admitted.
The exact new packet is
9216e5598c11ef2736c353dddd08bd7c2efedf742935a9a7ee9afd8383ee8846.
The exact approved packet is loaded by the trusted local composition.
All 66 inputs were actually re-admitted through D02/D03/D01. The
v2 coverage endpoint verifies the full chain in bounded batches; the
valuation endpoint accepts exactly 2–8 distinct approved source symbols
with strict signed quantities, in canonical packet order. Both use
an explicit v2 DTO, with the v1 DTO, route semantics and pinned OpenAPI
unchanged. The v2 coverage's evidence version is the chain packet and
artifact hashes; each selection's registry digest is specific to the
selected terms. The frontend compares each result's exact source key,
terms, official ID, prices, approval, packet/artifact lineage, axes and
complete contribution identities with the catalog and submitted request.

The default React workbench uses v2; `/?api=v1` preserves the original
pair workflow. Filter by right/strike, select 2–8 legs, disable duplicate
and at-intrinsic additions, and clear old results on edits/cancel/failure.
Selection and the disabled discovery months are not permission to price
new dates or cross-month combinations. The server independently rejects
at-or-below-intrinsic marks as IV_UNAVAILABLE. Valid input is not a promise
of successful calibration for every remaining strike.

Both API versions share one worker lock in the local launcher. Existing
CSP, bearer memory-only behavior, bounded parsing, and no production
listener/DB/CTP rules remain. Actual Chromium verified 2/4/8-leg
25-cell/9-point repricing, selection, v1 exact grid parity, unavailable
and stale state, responsive screenshots and loopback cleanup.
Q17, wider dates/months/products, fresh history, PIT, TLS and production
auth/model/valuation/deployment gates remain independent.
Q17, fresh live data, TLS, PIT, production readiness remain independent.

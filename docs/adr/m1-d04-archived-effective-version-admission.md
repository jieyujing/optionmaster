# ADR M1-D04 — Approved archived slice and effective terms version

Status: accepted within the operator-approved archived one-day slice, 2026-11-24.
Predecessors: M1-D02 historical contract attestations and M1-D03 pinned registry
admission. This is not live transport, a default missing-version policy, or a
valuation/trading authorization.

## Decision

The source vendor's raw contract_version and exercise_style are observations,
not necessarily the independently verified effective contract revision. Preserve
them exactly. A reviewed effective revision may be used only for a fixed
archived observation whose complete packet is independently operator-pinned.

The trusted composition layer supplies SliceApproval, separately from both the
candidate packet and HistoricalReadRequest. For the approved CU2211 slice,
operator_slice_approval.json records the actual conversation approval of the
canonical candidate digest, date, instruments and approval time. This is a
local operator trust root, not a digital signature or self-issued approval.

Verify the candidate's claimed and computed canonical SHA-256 against that
external pin; verify all eight referenced artifact byte digests and exact
file set, same-date official comparison, recorded bounded read profile,
unique rows, recorded resource statistics, exact provider/contract identity,
calendar and both legs' consumed values. The original archive is not rewritten.

The deterministic effective revision is content-addressed for each exact
dated binding. Derive a D02 registry only after the approved packet and all
artifact integrity checks pass. D03 validates the derived registry and its
referenced artifact bytes; this delegation is explicitly anchored in the
independently approved packet, not an arbitrary fingerprint generated from
unapproved data. D02 evaluates a separate effective projection, and D01
revalidates all observed marks using the original observation time.

## Result and limits

The immutable VerifiedHistoricalSlice records the raw terms, raw source JSON,
effective terms, exact packet bytes, all evidence digests, derived registry
digest, observed/approved/admitted times and D01 basis. ADMITTED_REFERENCE
means historical input admission for these exact observed values; it does not
certify a fresh query, an atomic producer snapshot, original publication-time
availability, other dates, or any unobserved row.

The ordinary HistoricalRepository, D02 and D03 remain unchanged. Unapproved
blank raw versions still fail closed. No new live query, DB write, credential
use, profile modification, Git action, deployment or CTP action is authorized
or performed here. Missing, changed or extra evidence and out-of-scope requests
return UNAVAILABLE without partial data. Production HTTPS remains independently
unverified; valuation_ready remains False pending the other M1 pricing inputs
and acceptance gates.

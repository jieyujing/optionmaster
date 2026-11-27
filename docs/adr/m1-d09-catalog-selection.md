# ADR M1-D09 — Dated chain catalog and multi-expiry research

Status: operator-approved historical research catalog, v3 API/React and
actual local Chromium browser acceptance passed, 2026-11-24.
D08 v1/v2 contracts and the original approved archive remain usable.

## Decision

- Contract-date identity is (trade date, exact source symbol). Chain scope
  is a dated underlying, explicit vendor portfolio/chain/key, official
  archive hashes/URIs and bounded observation schema. Generalize the
  D08 parser/comparator with trusted ChainArchiveScope, not a caller's
  paths, SQL, authority, or symbol-spelling inference.
- One canonical parent catalog pin binds all independently reproduced
  child packet hashes and exact artifact versions. Rebuild the children
  before admission. For multiple scopes on one trade date, require the
  same official archive digests, not just the same day or row counts.
  Compare every consumed term and mark against that archive; do not
  claim a fresh atomic database snapshot from separate observations.
- Keep original D08 canonical packet 9216e5598c11ef2736c353dddd08bd7c2efedf742935a9a7ee9afd8383ee8846
  and its separate operator approval intact. The D09 aggregate candidate
  does not retroactively approve additional records. No automatically
  manufactured approval or user-supplied self-approval.
- The 2022-09-07 CU2212 and 2022-09-08 CU2211 exact children add 132
  contract-date records (66 new instrument keys and 66 next-day records);
  the three children cover 198 records out of 680 discovered records.
  482 remain discovery only. The original archived SHFE amendment applies
  American exercise to futures months >= CU2211; do not extrapolate the
  rule to CU2210. Both historical dates and all chosen monthly identities
  are exact. Future dates require a new reviewed source scope, not a code
  fork or a new API version merely for another approved archive.
- A portfolio selects one common trade date and 2..8 distinct exact
  source symbols. May span multiple admitted delivery months of the same
  product family. Resolve D02 terms and D03 registry across all selected
  contracts; D01 chooses one whole-portfolio settlement basis. Preserve
  distinct option and futures marks and per-contract maturity. Each
  underlying has its own explicit exposure; never sum Delta/Gamma across
  different deliverables into one misleading exposure.
- Use D05 nominal 15:00 Beijing historical clock, ACT/365F, and the
  verified 3M CGB prior-trading-day proxy. Sept7 uses Sept6 1.4442%;
  Sept8 uses Sept7 1.4628% from the original pinned rate artifact.
  No future rate date and no holiday calendar guess for other dates.
  American binomial pricing with explicit steps, IV calibrated once
  per leg before shocks, raw missing version preserved, reject intrinsic-
  only or otherwise unavailable calibrations. Earliest selected expiry
  date is an exclusive guard for the whole portfolio.
- Reuse M0 scenario semantics: parallel price/IV plus exact underlying
  and expiry replacements, then additional grid shocks. Date-specific
  expiry IDs include exact underlying. Grid origin equals configured
  scenario; historical baseline cannot drift. Engine supports M0's
  bounded 121-point curve and 31x31 maximum grid offline. No large-grid
  API/interactive SLA claimed until performance evidence supports it.
- The actual independent catalog approval was recorded on 2026-11-24
  at 11:47:55 UTC. v3 coverage is date-qualified; v3 valuation accepts
  one date, exact positions, scenario clock, bounded axes and explicit
  underlying/expiry override maps. No paths, source credentials, model
  or trust root from HTTP. Re-admit the approved catalog before use.
  v3 returns per-leg/expiry maturity and separate underlying exposures,
  a shared immutable basis and all checked source child pins. The
  React selector clears old results on date/position/scenario changes.
  v3 is the local default; preserve v1/v2 via their paths and explicit
  browser query switches. One shared worker lock spans all versions.
  The interactive API uses at most 11 curve points and 5x5 cells (UI 9/5x5).
  Current trusted scope and frontend metadata are pilot-specific; new
  approved archives require updating trusted scope/pins, not a new wire version.

## Performance decision

Measured actual approved D08 pair with 128-step model and identical
9-point curve: 5x5 compute 3462.319 ms, 11x11 compute 15102.252 ms.
Local archive admission 35.317 ms; IV calibration 119.590 ms.
One sample each, no database re-read/API/browser/p50/p95. Existing
Q17 real-target requirements remain independent. Optimize redundant
baseline and scenario pricing only after measuring numerical parity and
realistic workloads, then choose an operational budget. No assertion of
a complete Q17 performance gate or production readiness.

## Independent approval recorded

The exact canonical D09 parent candidate:
2c57d7df89e87db37749d4217c68117bd1d3ccc4c332bf1cfa41b4fb28264fea
in docs/evidence/m1-d09/catalog_candidate.json was explicitly approved
by the operator at 2026-11-24T11:47:55Z. The independent receipt is
docs/evidence/m1-d09/operator_catalog_approval.json.
Approval applies only to these historical inputs. No database mutation,
Git publication, deployment, live trading, TLS or production approval.

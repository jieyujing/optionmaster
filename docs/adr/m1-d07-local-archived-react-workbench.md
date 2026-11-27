# ADR M1-D07 — Local React archived research desk

Status: accepted for the D06 exact archived slice in local research,
2026-11-24. No production release or general market data authority.

## Contract and composition

Use the actual pinned D06 OpenAPI 3.1 artifact (SHA-256
1d8aca0aa045b5a731c25aa46b61d60dab68c8714088877f2e105a300ae8fbc6).
Generate TypeScript DTO types and AJV standalone validators from it
at build time, requiring fields that D06 always serializes, including
defaulted scope/readiness/units. No runtime eval, hand-written pricing
formulas, or mock fallback. Frontend additionally checks the response
envelope, exact admitted slice, source versions and artifact digests,
positions and order, scenario time, axes, complete valid cell
contributions, unavailable null semantics, and origin consistency.
The only server-side source is the existing D06 reviewed archive.

Separate webui/ package with pinned npm lockfile; core Python project
dependencies and D06 optional extras are unchanged. React 19.2.8,
TypeScript 5.9.3, esbuild 0.28.2 and AJV 8.20.0 were actually exercised.
Static assets are local; no external font, chart CDN, or remote API.
The existing Python wheel does not include frontend/evidence files.

## Interaction and presentation

Use a dense, dark financial research desk with accessible labels,
visible keyboard focus, non-color-only sign/availability and responsive
contained tables. User explicitly controls signed hypothetical
quantities, Beijing-time pre-expiry scenario and price/absolute-IV
half-widths. Both legs required; no expansion outside the D06 archive.
9 price-curve points and 5x5 heatmap, including zero. Display actual
CNY values, annualized IV, model Greeks, daily theta (annual/365) and
vega per volatility percentage point, grouped by exact underlying.
Full server repricing is used, not client-side Greek approximation.
Curve gaps and hatched invalid heatmap cells remain unavailable.
Interactive cell/curve selection displays per-leg contribution and time.
A zero-shock origin may have nonzero value change after time advances.

Edits invalidate old result immediately. Explicit compute only;
disable concurrent local controls, permit cancellation of waiting,
discard late responses by operation identity, use a bounded 30s
browser wait. Server may still be busy after cancellation; retry is
manual, not a promise to cancel the underlying worker. Errors do not
show stale/partial valuation, and schema/provenance mismatches reject
the response. Readiness false and historical research scope are always
visible. No claim of verified publication-time PIT, production interest
curve, expiry cutoff, model resolution or trading.

## Local security boundary

Explicit launcher only, host fixed to 127.0.0.1 and TrustedHost check.
No public binding, CORS relaxation, service secret in bundle or
browser storage, or external request. Local operator enters an
ephemeral research token in terminal and browser; app holds it in
memory only and clears on disconnect/refresh. Static route allowlist,
same-origin CSP without unsafe-eval, no-store, no-referrer,
frame denial. This is a local HTTP research composition, not the
production authentication/TLS/CSRF/rate limiting design.
No database or CTP connection in D07. No automatic service startup.
Production gates, broad coverage and Q17 remain independent.

# ADR M1-D05 — Archived historical research valuation

Status: accepted as a narrow local research integration, 2026-11-24.
Depends on the independently approved M1-D04 archived CU2211 input.
This is not a production rate, cutoff, model-validation or release approval.

## Decision

- The D05 entry point receives the original evidence packet and all eight
  artifacts plus the independently approved D04 pin. Re-run D04 admission;
  never trust a caller-provided status flag or replace unknown raw terms.
- Explicit model mapping uses the reviewed futures-option asset class and
  AMERICAN exercise style. Reject unsupported products/styles. Preserve
  native option identity, exact underlying, historical strike/multiplier,
  settlement/settlement pairing and signed quantities.
- For this specific 2022-09-07 research slice, use the retrieved public
  ChinaBond government 3-month YTM dated 2022-09-06, 1.4442%, as a short
  rate proxy. It is not the exact 48-day zero rate. The declared approximation
  treats the quote as simple interest over 0.25 years and maps it to flat
  continuous r = log(1 + 0.014442 * 0.25) / 0.25 = 0.014415991163968755.
  No hidden zero/default rate, extrapolated series or rate-source fallback.
  Prior-day selection avoids the usual same-day 17:30 curve publication
  boundary, but does not prove the original historical publication instant.
  The raw HTML, exact source URI/date/maturity column and externally supplied
  expected digest are verified. Public data research use is not a license
  for production derived products.
- Nominal valuation clock is 15:00 Asia/Shanghai on the trade date and
  nominal horizon end 15:00 on the verified expiry date (48 calendar days).
  ACT/365F elapsed UTC time. These are explicit research timing conventions,
  NOT asserted actual settlement publication/cutoff timestamps. Expiry
  boundary passed to M0 is date-only; no expiry-day valuation, exercise or
  settlement simulation. No true PIT backtest data claim.
- Use the existing M0 American binomial model with explicitly configured
  128 steps for this small research demonstration; retain IV residual and
  compare discretization against 512 steps. Calibrate once per leg from the
  reference mark and freeze that IV for all time/price/absolute-IV scenarios.
  Reuse ResearchRiskEngine and ResearchRiskGridEngine. No pricing kernel
  changes and no data rereads inside the grid.
- Limit the demonstration to 2 legs, at most 11 curve points and 5x5
  heatmap. Preserve M0 failure semantics and independent unavailable cells.
  The M0 engine's wider limits and original Q17 performance gate are unchanged.

## Consequences

An OK archived research valuation is not the general M1 valuation_ready
authorization. Result carries source packet and registry digest, explicit
policy, rate evidence, raw and effective terms, nominal time, model, IV
calibrations, full grid and signed monetary sensitivity. The sample holdings are explicit illustrative long one call and one put,
not actual user positions. The theoretical value change is not a fill or realized P&L.
Future production use needs a separately reviewed rate/cutoff/model
convention, data-use rights, fresh source admission, official coverage,
PIT (if required), TLS, API/UI and real-target performance acceptance.

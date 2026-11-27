# OptionMaster Research WebUI — M1-D09

Local-only React research workbench for the operator-approved SHFE copper
option archive. D09 v3 is the default, with 198 approved contract-date
records: CU2211/CU2212 on 2022-09-07 (132), CU2211 on 2022-09-08 (66).
The existing v1 and v2 contracts remain available. This is neither live
trading nor an unrestricted historical data browser.

## Start locally

From the repository root with the configured Python environment, locked Web
dependencies, and locally supplied reviewed archives (not included in a public
checkout):

```sh
cd webui
npm run build
cd ..
.venv/bin/python -m webui.serve_local --port 8765
```

If required, restore dependencies with the existing lockfile (`npm ci`
or `npm ci --offline` with a populated cache) and optional Python extras:
```sh
uv pip install --python .venv/bin/python -e '.[research-web,research-web-test]'
```

The launcher prompts for an operator-chosen ephemeral local bearer
(32–256 printable nonwhitespace ASCII characters). Enter the same token
at http://127.0.0.1:8765/. Do not reuse production/service credentials.
Stop the service with Ctrl-C. No automatic startup or deployment.

It binds only 127.0.0.1, checks the Host header, serves the built assets
plus authenticated v1/v2/v3 APIs with a single shared worker lock, and
applies restrictive same-origin CSP. The token is held in page memory;
refresh/disconnect clears it. Browser/password-manager behavior outside
application memory is not controlled. Never expose this local HTTP
composition to a network or treat it as production security architecture.

## Use

- Connect and select a supported historical trading date. Switching the
  date clears results, resets the scenario time and positions to the
  approved CU2211 C/P 61000 straddle for that date, and clears overrides.
- Select delivery month, Call/Put and strike; add/remove 2–8 distinct
  contracts across admitted months on the SAME date. Other months are
  discovery-only. Intrinsic-only marks are data but cannot be selected
  for positive-IV valuation.
- Enter signed nonzero integer quantities (long positive, short negative,
  absolute value at most 1000). Any change invalidates the result.
- Select Beijing-time scenario clock, strictly before the earliest
  selected expiry date. Choose the price and absolute IV half-widths.
  Optional per-month price (%) and expiry IV (percentage points)
  overrides define the configured scenario; curve and matrix shocks
  are additive to that scenario, with origin equal to its value change.
- Click 重新估值. Current Web request cap: 11 curve points and 5x5 cells;
  the UI uses 9 and 5x5. Each leg retains its distinct futures mark,
  expiry, locked historical IV, and underlying-group Greeks.
- Click/keyboard-activate a matrix cell or curve point for leg
  contributions. Unavailable cells are not zero. Expand evidence for
  source revisions, per-month expiries, dated rate and clock/model
  assumptions.
- Compatibility entry points: /?api=v1 and /?api=v2. v2 remains the
  original CU2211 single-date chain.

## Verification

```sh
cd webui
npm run build
npm test
cd ..
.venv/bin/python -m webui.tests.run_browser --catalog --evidence-dir docs/evidence/m1-d09/browser-rerun
```

`npm test` always runs the synthetic v4 strategy contract, portfolio state,
and asynchronous request-lane tests. The v1–v3 replay tests also run when
their reviewed local API captures are present, otherwise they are reported as
skipped. To run just v4, use `node scripts/test-strategy.mjs` from `webui/`.
Neither command needs ClickHouse or a running service.

The D09 browser harness uses existing local Chromium (or
OPTIONMASTER_BROWSER_EXECUTABLE); no download, external network or live
database is needed. It starts a temporary loopback-only server and
checks its cleanup. D09 browser evidence is saved to
`docs/evidence/m1-d09/browser/`; original evidence is not overwritten.
The one-off accepted evidence files use exclusive creation: for a new
evidence run, preserve the old files and use an explicitly new evidence
directory with --evidence-dir as shown above. --chain exercises the original v2 suite; no flag uses v1.
The D04/D05/D08/D09 archives and independent approvals are required
local inputs and are deliberately not packaged in the universal wheel.

## Limits

198 approved contract-date records only; 482 other discovered records
are not admitted. Adding a new date/month requires new reviewed source
scope and approval, not arbitrary API parameters. Rates are prior-day
3M government YTM proxies, nominal 15:00 clocks are not historical
publication-time PIT proof. No fresh market data, live CTP, trading,
production TLS or production valuation is certified. Q17 real-target
performance remains separately open; current full-grid calculations
may take seconds, and the UI's 30-second wait timeout is not an SLA.
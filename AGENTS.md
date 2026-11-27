# OptionMaster — agent instructions

Applies to this repository. Use the current implementation, tests, and relevant ADRs as evidence; distinguish worktree-only changes from committed or deployed code. Local test or build success does not imply production readiness.

## Before changing code

1. Inspect the current Git branch, staged/unstaged/untracked files, and relevant call sites. Preserve unrelated user work.
2. Read `README.md` and the tests around the change. If `CONTEXT.md` or `docs/adr/` exists, read relevant domain terms and decisions; do not assume these files exist or invent domain rules.
3. For bugs, reproduce the behavior with a focused failing test at the real calling boundary, then fix and rerun it.
4. **Preserve the worktree and acceptance evidence.** An untracked file is not disposable. Do not reset, clean, overwrite, or stage work you did not own. Use a new output directory for replay, browser, and benchmark runs; do not overwrite accepted evidence, input pins, or served assets. If a document and a newer result disagree, cite both and report the discrepancy rather than silently closing the gate.

## Trading and risk invariants

- **No real trading in development verification.** Use mocked gateways and offline replay. A code change, passing tests, or Cython parity is **not** authorization to connect CTP, transmit orders, deploy, or enable live hedging.
- **Fail closed on session uncertainty.** Automated Delta Hedge and Electronic Eye orders require a verified, per-contract continuous-trading window and valid final execution price. `OPTIONMASTER_VERIFIED_SESSIONS_FILE` points to dated, operator-reviewed trading sessions; no trustworthy configuration means no new automatic orders. Never substitute the synthetic policy in `tests/_session_test_support.py` for production exchange calendars.
- **Check market data before risk mutation and again before orders.** Keep `OptionEngine.process_tick_event()` rejection of stale, invalid, crossed/locked, duplicate, and out-of-order quotes before `portfolio.update_tick()`. Preserve directional behavior for legitimate one-sided books. Quote quality lives in `vnpy_optionmaster/market_data_quality.py`; session policy in `trading_sessions.py`; freshness and recovery controls in `risk_safety.py`.
- **Keep invalid risk invalid.** A missing bid/ask must not fabricate a midpoint. A stale mark or unmatched Greek quote version must not drive a new order. Enforce price tick size, daily limits, and bid/ask executability **after** payup. Preserve operator reconciliation and durable audit before releasing failed-close risk blocks.
- Treat real CTP order/trade/cancel races, reconnect/restart reconciliation, official trading-calendar data, and Windows acceptance as **separate production gates** until specifically verified.

## Pricing backend

- Preserve the existing three model labels and saved `model_name` settings. `vnpy_optionmaster/engine.py` creates the backend registry from `vnpy_optionmaster/pricing/backend.py`.
- Prefer only an **ABI-compatible, startup-validated Cython** extension for `calculate_price` and `calculate_greeks`; fall back to the audited Python model if absent, incompatible, or numerically inconsistent. **Implied volatility inversion remains Python.** The chosen functions must not silently hot-switch during active trading.
- Universal `py3-none-any` wheels deliberately contain **no platform binaries**. Do not package stale `.so`/`.pyd` files or assume a compiled local extension ships with the wheel. Check the actual selected backend rather than inferring it from file presence.

## Pricing and option-chain reuse (mandatory)

- **Single pricing source of truth.** For every Qt/Web/API/local-cache feature that calculates IV, price, or Greeks, first trace `vnpy_optionmaster/base.py` (`OptionData.set_pricing_model`, `calculate_option_impv`, `calculate_theo_greeks`, `ChainData`) and the existing registry in `engine.py` / `pricing/backend.py`. Reuse the existing `pricing/black_76.py`, `pricing/black_scholes.py`, and `pricing/binomial_tree.py` functions or the already-approved historical dispatcher in `research_pricing.py` (including its D11-equivalent research CRR). No parallel pricing kernels, custom IV solvers, or independent frontend calculations.
- **Preserve the chain's established operation.** Use the existing contract/expiry grouping, ATM selection (`ChainData.calculate_atm_price`), underlying adjustment (`calculate_underlying_adjustment`) and synthetic-price handling (`update_synthetic_price`). The existing chain selects the eligible call/put pair with the smallest price difference and uses `call_price - put_price + strike`; options price against the chain-adjusted underlying. Do not replace this with a new forward estimator, carry/dividend assumption, multi-strike parity fit, or Greek transformation. Preserve the existing maturity policy for the relevant live or approved historical path, call/put direction, multiplier, and Greek display units (Delta/Gamma × size, Theta × size / 240, Vega × size / 100).
- **Adapters only.** Historical data, SQLite, and Web UI may adapt identities, timestamps, and actual marks to the existing framework. Share/refactor existing logic rather than duplicating formulas. Preserve the distinction between historical close/settlement and live bid/ask/mid; never manufacture ticks, books, valid marks or IV to pass live quote guards. If the existing framework cannot represent a needed historical input, expose the missing capability and obtain explicit approval before changing its pricing or chain semantics.
- **Local model settings.** Retain the operator-approved 2% default rate, CFFEX European Black-76, and American binomial-tree selection; UNKNOWN defaults to American outside explicit exchange policy. Preserve other authorized per-chain preferences and raw source terms. Model selection does not authorize a new model implementation or a change to the existing chain operation. Research settings must not mutate live trading configuration.
- **Acceptance.** Before claiming parity, run a deterministic comparison against the existing framework for the same contract, chain adjustment, model, price/IV inputs, reference time, and units; include missing/invalid inputs and model-switch regressions. Record the actual shared call sites and outputs. Existing approved research ADRs remain scoped to their original evidence. A new numerical method, underlying-price convention, maturity policy, or model substitution requires explicit user approval and a documented decision; do not silently introduce one to fill empty cells.

## Historical research and Web API invariants

- **Replay first; real reads stay bounded and authorized.** Default to frozen receipts or fixtures. New ClickHouse reads must stay within the authorized target, account, data scope and resource budget. Use fixed parameterized `SELECT`/`EXPLAIN`, allowlisted tables/columns/keys, and enforce server-side scan, execution and result limits plus a client deadline. `LIMIT` or a client timeout alone is not a scan bound. Do not auto-retry a failed read or expand scope to make a test pass. Never write data, change permissions/settings, or expose credentials from research code or test tooling.
- **Freeze inputs; never invent coverage or values.** Bind payoff and valuation to the exact snapshot, trade date, contract/underlying identity, terms, mark, and price basis. Keep entry cost separate from market marks when revaluing. Missing, conflicting, or detectably changed selected inputs are unavailable, never zero or silently substituted from another date or provider. Partial coverage may still permit analysis of verified legs; do not label a date complete without evidence. A frozen consumer snapshot is not proof of a global database snapshot or historical publication time.
- **Keep contracts compatible and latest results authoritative.** Preserve v1–v3 schemas, semantics and public limits; make incompatible changes through an explicit new version. Generate Web types and validators from the corresponding API contract. In v4, display results only for the current portfolio revision and snapshot; a delayed or cancelled response must not overwrite newer state. Test rapid edits and date/contract changes at the request/response boundary.

## Verification

Run the checks relevant to the change in the project's configured Python environment; on the current macOS/uv runner:

```sh
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/mypy vnpy_optionmaster
uv pip check --python .venv/bin/python
uv build
.venv/bin/python -m pytest tests/test_build_artifacts.py -q
git diff --check
```

For market-data and hedging changes, include `tests/test_market_guards_regression.py`, `tests/test_tick_ingress.py`, and `tests/test_trading_sessions.py`. For pricing changes, include `tests/test_pricing_backend.py`, `tests/test_cython_pricing_consistency.py`, and wheel-isolation coverage. Report exactly what ran, what passed/failed, and what remains unverified.

- **Test the path the user exercises.** For Web/API changes, run `npm test` and `npm run typecheck` from `webui/`; the default suite runs synthetic v4 tests and runs v1–v3 replay tests when local reviewed captures are available. For browser-visible behavior, also run the relevant loopback browser test against a fixture or reviewed replay, without a production connection. A type check, build, or mocked pass is not browser or real-target acceptance.
- **Do not move the acceptance bar.** Keep workload, dataset, metric, and thresholds fixed when comparing performance. Reproduce failures offline before consuming another real-target benchmark budget. Preserve failed samples and report skipped, replayed, and actual checks separately; do not turn a failing gate into a pass by changing the metric or dropping samples. Q16 data acceptance, Q17 performance, and release approval are separate gates.

## Issues, commits, and publication

- GitHub Issues is the issue tracker; use `gh` only where authenticated and authorized. Existing triage vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`; confirm remote labels before mutation.
- Stage **only task-owned paths**; inspect the staged diff, check for secrets/artifacts, and preserve unrelated untracked or staged files. Use focused Conventional Commits.
- **Commit, push, PR creation, merge, deployment, and live CTP actions require their own user authorization.** Never force-push by default. Verify the remote SHA after an authorized push.

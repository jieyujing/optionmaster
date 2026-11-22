# OptionMaster — agent instructions

Applies to this repository. Use the checked-in implementation, tests, and `README.md` as evidence; do not infer production readiness from local tests or a successful build.

## Before changing code

1. Inspect the current Git branch, staged/unstaged/untracked files, and relevant call sites. Preserve unrelated user work.
2. Read `README.md` and the tests around the change. If `CONTEXT.md` or `docs/adr/` exists, read relevant domain terms and decisions; do not assume these files exist or invent domain rules.
3. For bugs, reproduce the behavior with a focused failing test at the real calling boundary, then fix and rerun it.

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

## Issues, commits, and publication

- GitHub Issues is the issue tracker; use `gh` only where authenticated and authorized. Existing triage vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`; confirm remote labels before mutation.
- Stage **only task-owned paths**; inspect the staged diff, check for secrets/artifacts, and preserve unrelated untracked or staged files. Use focused Conventional Commits.
- **Commit, push, PR creation, merge, deployment, and live CTP actions require their own user authorization.** Never force-push by default. Verify the remote SHA after an authorized push.

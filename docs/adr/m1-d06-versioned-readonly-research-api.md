# ADR M1-D06 — Versioned read-only archived research API

Status: accepted for local archived research integration, 2026-11-24.
Depends on completed M1-D05 and the exact D04 independently approved
CU2211 archive. This is NOT approval of production transport, live data,
browser authentication, rate/cutoff policy, deployment, or trading.

## 1. Application boundary

Implement an optional FastAPI app factory, not a daemon or an automatically
started listener. The trusted composition layer loads the explicit D04 and
D05 local artifact roots using load_reviewed_archive, validates the approved
packet pin and the evaluated D05 policy pin, freezes the evidence bytes,
and supplies a separately managed bearer token to create_research_app.
There is no module-level app, default token, HTTP file selector, dynamic
database source or pricing-policy override. Original source bytes are not
packaged in the wheel. The loader's local paths and token are trusted
operator configuration, never request parameters.

The single known slice is shfe-cu2211-2022-09-07-v1, 2022-09-07,
CU2211C61000.XSGE and CU2211P61000.XSGE. Scope is explicit; do not imply
all historical contracts/dates are supported. GET coverage re-admits D04
input evidence, not the rate/model/valuation gates. POST valuation
re-admits and evaluates D05, solving IV from the approved marks. It reuses
the existing model, time and grid engines without changing any of them.

## 2. Versioned JSON DTO

Paths:
- GET /api/v1/research/coverage
- POST /api/v1/research/valuations

Both require bearer authentication. No query parameters. Schema version
"1.0" and independently generated 32-hex calculation_id identify each
response. The version refers to the wire contract, not a Git release.
The model_version identifies the M0 American binomial + D05 research
composition; tree_steps records the actual configured 128-step policy.

Example POST request (signed hypothetical positions, not real holdings):
```json
{
  "schema_version": "1.0",
  "slice_id": "shfe-cu2211-2022-09-07-v1",
  "positions": {
    "CU2211C61000.XSGE": 1,
    "CU2211P61000.XSGE": 1
  },
  "scenario_at": "2022-09-07T15:00:00+08:00",
  "curve_price_shocks": [-0.05, 0, 0.05],
  "heatmap_price_shocks": [-0.05, 0, 0.05],
  "heatmap_iv_shocks": [-0.03, 0, 0.03]
}
```

Require exactly these two source symbols; output in approved archive
order irrespective of request object order. Quantities must be strict
nonzero integers within [-1000,1000]. ISO-8601 scenario timestamp has
explicit +08:00 market offset. Axes are finite, distinct, include zero,
bounded [-1,1]; up to 11 curve points and 5 points per heatmap axis.
The original D05/M0 time, expiry, model and per-point guards still apply.
Unknown keys, duplicate JSON keys at any nesting level, nonfinite
constants, implicit numeric coercion, wrong types and malformed dates
are rejected. No client-supplied model/rate, SQL, URI, evidence path or
credentials. Content type application/json (optional UTF-8 charset),
identity encoding; body limit 8192 bytes including streamed body.

Use the OpenAPI 3.1 document in docs/evidence/m1-d06/openapi_v1.json.
It contains request/response schemas and bearer scheme; all local
references were resolved in the actual replay. No public docs or
openapi endpoint is enabled. The schema is exported by the trusted
local composition layer. Generate frontend DTO types from it rather
than serializing internal Python dataclasses.

Envelope:
- OK: reason_code=null, exactly one of coverage/valuation is present;
  the other is null.
- UNAVAILABLE: stable v1 ReasonCode, both result fields null.
- HTTP 200 is a processed domain request; check status even when 200.
  Domain failure (missing archive, rate evidence, expiry, IV etc.) is
  not successful valuation.
- 401 missing/wrong/duplicate bearer; 413 body too large; 415 wrong
  media/encoding; 422 invalid JSON/request/scope; 429 busy; 500 sanitized
  internal error. 404/405 nonexistent routes/disallowed methods.
  No traceback, raw validation payload or token in error responses.
  All responses constructed as DTOs with Cache-Control: no-store.

Valuation includes explicit units, observed/admitted/approved/reference/
scenario/expiry times, exact price basis and source versions (packet,
registry, artifact hashes), prior-day rate proxy with source digest,
model version, raw and effective terms, source marks, IV residuals,
signed per-leg values/Greeks and exact-underlying grouped Greeks.
Price-axis values are decimal returns; IV-axis values are absolute
decimal annual-volatility changes; theta is CNY per calendar year,
vega CNY per 1.0 sigma; prices CNY/ton, values CNY. Never sum unrelated
underlying-month Greeks. Heatmap rows correspond to IV-axis order,
columns to price-axis order. A valid grid may contain individual
UNAVAILABLE cells: all numerical risk values null, no contributions;
do not turn null into zero or interpolate it as ground truth. Origin
(0,0) equals configured scenario, not necessarily zero change when
scenario time advances. All values theoretical, not executable P&L.

## 3. Local security and bounded work

Require explicit 32..256 printable nonwhitespace ASCII bearer token;
constant-time comparison and exactly one Authorization header.
No public CORS policy, credentials in source, or trade/order endpoints.
Use one worker lock per app instance for coverage/valuation (no waiting
for another calculation; 429). Lock is held in the worker until its
actual completion, not tied to client response lifecycle. Body and
axis bounds are local resource guardrails, not a production rate limit
or hard compute deadline. No new live database access or external calls
during app execution. Do not embed service credentials in a future
browser bundle; production identity/session/authorization/TLS/CSRF/
proxy/rate-limit policies require independent review.

## 4. Dependencies and remaining gates

Optional extras: research-web (FastAPI, Pydantic v2, Uvicorn) and
research-web-test (HTTPX). The original four project dependencies are
unchanged. Tested with project Python 3.13, FastAPI 0.165.0,
Pydantic 2.14.2, Starlette 1.10.0, HTTPX 0.28.1, Uvicorn 0.54.0.
The installed FastAPI/Starlette versions require Python >=3.11;
version ranges permit a compatible resolver choice on older Python,
but the Python 3.10 Web-extra matrix is not verified here.
Core installations may omit Web extras; Web tests explicitly require
the optional packages (otherwise skipped). All D06 verification below
ran with the extras installed, not skipped.

D06 acceptance is only the exact archived research request through
ASGI and its DTO contract. No real network listener, production
exposure, live historical adapter or API/UI release. Fresh data and
coverage, PIT, production rate/cutoff/model/tolerance/data licensing,
verified TLS, browser integration and Q17 remain independent NO-GO.

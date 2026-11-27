# ADR M1-D11 — Research-only NumPy CRR lattice

Status: implemented; final historical acceptance is recorded separately in
`docs/evidence/m1-d11/acceptance.md` only after the validation gates pass.
Scope: approved historical research; never a change to live model selection
or authorization for Q17/production.

## Decision

D10 retains its exact request-local 4096-entry Greek and IV memos. D11
changes only the remaining American futures-option pricing kernel used by
the research dispatcher. Use existing NumPy, without new dependencies, JIT
or compiled extensions. Black-76 and Black-Scholes remain unchanged.
There is no HTTP model/implementation toggle or runtime hot switching.
The scalar live module is the unchanged independent comparison path.
The private research dispatcher module binding can be temporarily replaced
by the scalar module in serial local tests and paired diagnostic scripts;
normal runtime binds the NumPy module once.

The time-major underlying lattice stores only active triangular slices.
Each node follows the original sequence of up multiplies then down
multiplies, never powers or reassociated products. A rolling option vector
uses the same separately evaluated weighted continuation, discount and
American exercise comparison, preserving the ordered max semantics
(including ties). Store the first three layers for the original root price,
Delta, Gamma and annual Theta. Vega uses the same relative 1.001 volatility
bump and 0.001 * sigma divisor. The asymptotic time is still O(n^2);
the underlying lattice remains O(n^2), while option storage is O(n).
All admitted step policies remain unchanged, including the historical
128-step default and existing bounds. The same Brent bracket, stopping
criteria, residual, Vega checks and failure statuses are retained.

D10 cache implementation and grid logic are byte-identical. The existing
D10 cache tests' spies now target the actual research kernel rather than
the live scalar module; assertions, synthetic reference marks and test
coverage are retained. Historical D10 benchmark scripts/results are kept
as evidence of their original kernel revision, not presented as current
D11 measurements. D11 has its own paired script and immutable new outputs.

## Acceptance and limitations

Test-first dispatch isolation; compare the original scalar price, Greeks,
IV solver output, failure statuses, independent T07 numerical references,
and exact full approved v1/v2/v3 payloads except request identifiers and
admission clocks. Compare larger local full domain results with the same
admission time. Both paired arms must use the same approved archive,
model, step count and D10 cache; alternate arm order, record all call
counts, latency, CPU and phase timings. Peak process RSS is cumulative
and does not measure isolated per-arm memory allocation. Repeated 5x5
measurements are local ASGI observations, not independent cold-process
tests or production tail-latency estimates. Large-grid runs are bounded
local engine diagnostics, not changes to HTTP caps or rendering policy.

Preserve historical approvals and source evidence. No new DB reads or
writes, credentials, production service changes, live pricing changes,
CTP, Git publication or deployment. Full Q17 fresh-data operational
envelope, data licensing and all other production gates remain separate.

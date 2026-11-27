# ADR M1-D10 — Exact request-scoped research pricing reuse

Status: accepted for local historical research optimization, 2026-11-24.
D09 actual approved catalog and API/browser acceptance are prerequisites.
This is not Q17 real-target performance acceptance or production authorization.

## Evidence and decision

Actual approved 5x5 D09 ASGI profiling showed 468/936/1872 calls to the
unchanged American binomial price/Greek kernel for the 2/4/8-leg cases;
only 58/236/232 distinct numeric inputs. T06 calls T05 per coordinate,
T05 calls T04 for original and shifted maturity, and T04 calls T03/T01
for baseline and scenario. Even the same unshocked reference Greeks
were recomputed. This was demonstrated by instrumenting the real kernel,
not inferred from the nested call graph alone.

Introduce a new pricing memo **only** within ResearchRiskGridEngine.evaluate.
Every invocation allocates a new ContextVar-scoped memo with a finally
reset. Two separate bounded LRU dictionaries (4096 entries each) hold
only finite complete model price/Greek tuples or successful structured
IV results. The keys include model, exact lossless float hex encodings
of all five numeric pricing/inversion arguments, right, and tree steps.
Safe int/float/NumPy float64 representations coalesce only for exactly
representable numeric values; signed zero is retained. Other types
and unsupported/nonfinite key values use the original calculation.
Exceptions and unusable results are not memoized.

Neither a quote, a portfolio result, a data source, a source version,
an approval or a mutable request is cached. No cross-request cache,
no change to live pricing/backend, no new compiled extension, no
algorithm/tolerance/tree-resolution changes. The original numerical
functions and failure semantics remain intact. Each different scenario
still invokes full original pricing; all original T01–T06 validations,
per-leg contributions and baseline/expiry/invalid-cell guards remain.
Nested scope restoration and thread isolation come from ContextVar;
the engine is synchronous and does not spawn tasks within the scope.

## Actual paired results

Same process, same archived data, same 128-step model and identical
9-point curve / 5x5 risk grid. Disable only the new memo using a test
nullcontext; alternate arm order by case. Both arms compare all
valuation payload fields against the preserved D09 actual API output,
excluding the per-request admission timestamp. Canonical digests match.

| Legs | Before (ms) | After (ms) | Kernel calls before/after |
|---:|---:|---:|---:|
| 2 | 3819.445875 | 627.370833 | 468 / 62 |
| 4 | 7599.865042 | 2350.633041 | 936 / 244 |
| 8 | 15466.692542 | 2438.075500 | 1872 / 248 |

The remaining duplicate calls outside the grid are the original
per-leg calibration, intentionally unchanged. The 4-leg case has
independent per-underlying and expiry shocks and elapsed time, and
therefore more unique states than a same-time 4-leg case. It is not
comparable to the 8-leg case solely by leg count.

## Remaining limits

No API axis-cap increase: v1/v2/v3 remain as before. Distinct points
still incur the original O(n^2) Python scalar lattice work. Larger-grid
diagnostics use the same actual approved archived catalog through the
local engine, without network, fresh ClickHouse, serialization or
browser rendering. Single measurements and process RSS are diagnostic
only, not p50/p95 or an approved operational budget. Q17 still requires
fresh read + admission + IV + full reprice + serialization/render on
the intended target, with repeated cold/warm workloads and independent
approval of the measured operational envelope. Any subsequent
vectorized/compiled research kernel is a separate numerical-parity
optimization; do not silently replace live pricing or relax model steps.

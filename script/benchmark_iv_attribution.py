"""Offline IV solver vs one-process worker latency attribution; no gateway.

Examples:
    .venv/bin/python script/benchmark_iv_attribution.py --requests 24
    .venv/bin/python script/benchmark_iv_attribution.py --requests 12 --output docs/your-file.json

Measures three *different* boundaries:
1) Original rounded Brent inversion, in process.
2) Full-precision structured safeguarded solver, in process.
3) The exact same structured solver plus one spawned Worker, IPC and polling.

Values are synthetic; no exchange feed, orders, trading-session assertions or
production capacity/SLO claims. Worker startup and first-request cold cost are
reported separately.
"""
from __future__ import annotations

import argparse
import json
import platform
from dataclasses import replace
from pathlib import Path
from statistics import median
from time import monotonic, perf_counter_ns, sleep
from typing import Any

from vnpy_optionmaster.pricing import black_76
from vnpy_optionmaster.pricing.iv_worker import (
    IVWorkRequest, IVWorkResult, SingleIVWorker, WorkerCache, calculate_batch,
)


def quantile(values: list[float], fraction: float) -> float:
    sorted_values = sorted(values)
    position = (len(sorted_values) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return round(sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low), 3)


def requests_for(count: int) -> list[IVWorkRequest]:
    result: list[IVWorkRequest] = []
    for index in range(count):
        cp = 1 if index % 2 else -1
        spot = 100.0
        strike = 96.0 + 2.0 * (index % 5)
        rate = 0.02
        expiry = 0.5
        volatility = 0.24 + 0.003 * index
        mid = black_76.calculate_price(spot, strike, rate, expiry, volatility, cp)
        result.append(IVWorkRequest(
            symbol=f"SYNTH-{index:03d}", generation=1, sequence=index + 1,
            model="black_76", spot=spot, strike=strike, rate=rate,
            expiry=expiry, cp=cp, bid=mid - 0.05, ask=mid + 0.05, mid=mid,
            quote_identity=(f"option-{index}", "underlying-synthetic"), model_version=1,
        ))
    return result


def sample_stats(latencies: list[float]) -> dict[str, Any]:
    return {
        "requests": len(latencies),
        "median_ms": round(median(latencies), 3),
        "p95_ms": quantile(latencies, 0.95),
        "p99_ms": quantile(latencies, 0.99),
        "total_ms": round(sum(latencies), 3),
    }


def local_solver(requests: list[IVWorkRequest], *, legacy: bool) -> dict[str, Any]:
    cache = WorkerCache(max_entries=max(4, len(requests) * 3 + 1))
    cold: list[float] = []
    warm: list[float] = []
    cache_hits = 0
    def one_pass(times: list[float]) -> None:
        nonlocal cache_hits
        for request in requests:
            start = perf_counter_ns()
            if legacy:
                vals = [
                    black_76.calculate_impv(
                        p, request.spot, request.strike, request.rate, request.expiry, request.cp
                    )
                    for p in (request.bid, request.ask, request.mid)
                ]
                if not all(value > 0 for value in vals):
                    raise AssertionError("Legacy numerical result invalid for synthetic input")
            else:
                outcome = calculate_batch(request, cache)
                if not (outcome.bid.valid and outcome.ask.valid and outcome.mid.valid):
                    raise AssertionError("Structured numerical result invalid for synthetic input")
                cache_hits += outcome.cache_hits
            times.append((perf_counter_ns() - start) / 1e6)

    one_pass(cold)
    one_pass(warm)
    return {
        "mode": "sync_legacy_brent_rounded" if legacy else "inprocess_structured_with_lru",
        "cold": sample_stats(cold), "warm": sample_stats(warm),
        "cache_hits": cache_hits if not legacy else None,
    }


def worker_solver(
    requests: list[IVWorkRequest], *, polling_sleep_s: float,
) -> dict[str, Any]:
    # Calculate numerical references before timing any Worker round trip.
    # Recomputing them inside the timed block would inflate measured IPC cost.
    references = {request.symbol: calculate_batch(request, WorkerCache(4)) for request in requests}
    worker = SingleIVWorker(cache_entries=max(4, len(requests) * 3 + 1), queue_capacity=2)
    start = perf_counter_ns()
    if not worker.start():
        raise RuntimeError("Offline Worker startup selftest failed")
    startup_ms = (perf_counter_ns() - start) / 1e6
    cold: list[float] = []
    warm: list[float] = []
    polls = 0
    cache_hits = 0
    try:
        def round_trip(times: list[float]) -> None:
            nonlocal polls, cache_hits
            for request in requests:
                current = replace(request, generation=worker.generation)
                start_ns = perf_counter_ns()
                if not worker.submit(current):
                    raise RuntimeError("Could not submit offline Worker request")
                deadline = monotonic() + 5
                result: IVWorkResult | None = None
                while result is None and monotonic() < deadline:
                    polls += 1
                    result = worker.poll()
                    if result is None:
                        sleep(polling_sleep_s)
                if result is None:
                    raise TimeoutError("Offline Worker request deadline expired")
                if result.symbol != request.symbol or not all((
                    result.bid.valid, result.ask.valid, result.mid.valid,
                )):
                    raise AssertionError("Worker returned unexpected or invalid numeric result")
                elapsed_ms = (perf_counter_ns() - start_ns) / 1e6
                expected = references[request.symbol]
                for field in ("bid", "ask", "mid"):
                    got = getattr(result, field).volatility
                    want = getattr(expected, field).volatility
                    if got is None or want is None or abs(got - want) > 1e-12:
                        raise AssertionError(f"Numerical mismatch for {field}: {got} vs {want}")
                cache_hits += result.cache_hits
                times.append(elapsed_ms)
        round_trip(cold)
        round_trip(warm)
        return {
            "mode": f"worker_structured_poll_sleep_{polling_sleep_s:g}_seconds",
            "worker_startup_ms": round(startup_ms, 3),
            "cold": sample_stats(cold),
            "warm": sample_stats(warm),
            "cache_hits": cache_hits,
            "poll_count": polls,
            "per_request_worker_inflight_limit": 1,
        }
    finally:
        worker.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=24)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 4 <= args.requests <= 48:
        parser.error("--requests must be between 4 and 48")
    reqs = requests_for(args.requests)
    results = [
        local_solver(reqs, legacy=True),
        local_solver(reqs, legacy=False),
        worker_solver(reqs, polling_sleep_s=0.0005),
        worker_solver(reqs, polling_sleep_s=0),
    ]
    payload = {
        "kind": "offline_iv_solver_worker_attribution",
        "no_production_approval": True,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "synthetic_requests": args.requests,
        "numeric_operations_per_request": 3,
        "bounds": [
            "Two local paths use different solvers; Worker and in-process structured paths use exactly the same solver.",
            "The legacy API rounds IV and does not have a structured-result LRU.",
            "One request at a time; no full chain aggregation, market-data admission or order submission.",
            "Worker startup/selftest is excluded from request latency and reported independently.",
            "Same-host short runs are subject to OS scheduling and cache temperature; do not infer production p95 SLO.",
            "Each worker round-trip includes pickle queues, process scheduling, result transport and client polling; reference recomputation/parity verification is outside timed intervals.",
        ],
        "results": results,
    }
    output = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output is not None:
        args.output.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

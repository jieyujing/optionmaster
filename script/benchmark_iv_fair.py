"""Same-solver in-process versus isolated IV process: offline-only fair benchmark.

Run locally:
  .venv/bin/python script/benchmark_iv_fair.py --options 6 --events 36 --seed 20261123 --micro

The *only* execution-path replacement is an in-process worker transport. Both
modes use the identical IVDispatcher, LatestWinsScheduler, WorkerCache,
calculate_batch, IVResult, capture_option_iv and commit_option_iv contracts.
This neither changes the production runtime nor grants live trading authority.
"""
from __future__ import annotations

import argparse
import json
import platform
from collections import deque
from dataclasses import replace
from pathlib import Path
from time import monotonic, perf_counter, sleep
from typing import Any
from unittest.mock import patch

from vnpy_optionmaster.pricing.iv_runtime import OptionIVRuntime
from vnpy_optionmaster.pricing.iv_worker import (
    IVDispatcher, IVWorkRequest, IVWorkResult, LatestWinsScheduler,
    SingleIVWorker, WorkerCache, calculate_batch,
)

if __package__:
    from . import benchmark_iv_attribution as attribution
    from . import benchmark_iv_chain as chain
else:
    import benchmark_iv_attribution as attribution
    import benchmark_iv_chain as chain


class InProcessIVWorker:
    """Test-only scalar transport, with the same 1-in-flight dispatch contract."""

    def __init__(self, *, cache_entries: int, queue_capacity: int) -> None:
        if cache_entries <= 0 or queue_capacity <= 0:
            raise ValueError("Capacity must be positive")
        self.cache_entries = cache_entries
        self.queue_capacity = queue_capacity
        self.generation = 0
        self._active = False
        self._cache = WorkerCache(cache_entries)
        self._inbox: deque[IVWorkRequest] = deque()

    @property
    def alive(self) -> bool:
        return self._active

    def start(self, *, ready_timeout: float = 5.0) -> bool:
        if not self._active:
            self.generation += 1
            self._cache = WorkerCache(self.cache_entries)
            self._inbox.clear()
            self._active = True
        return True

    def submit(self, request: IVWorkRequest) -> bool:
        if (not self.alive or request.generation != self.generation
                or len(self._inbox) >= self.queue_capacity):
            return False
        self._inbox.append(request)
        return True

    def poll(self) -> IVWorkResult | None:
        if not self.alive or not self._inbox:
            return None
        return calculate_batch(self._inbox.popleft(), self._cache)

    def close(self) -> None:
        self._active = False
        self._inbox.clear()


class InProcessIVRuntime(OptionIVRuntime):
    """Use unchanged owner lifecycle, risk capture/commit and scheduling logic."""

    def __init__(
        self, *, cache_entries: int, queue_capacity: int,
        max_pending: int, max_priority_streak: int,
        inflight_timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(
            cache_entries=cache_entries, queue_capacity=queue_capacity,
            max_pending=max_pending, max_priority_streak=max_priority_streak,
            inflight_timeout_seconds=inflight_timeout_seconds,
        )
        # Local benchmark replaces only the Worker IPC endpoint; the exact
        # dispatcher, queue policy and owner-side commit are otherwise reused.
        self.worker = InProcessIVWorker(
            cache_entries=cache_entries, queue_capacity=queue_capacity,
        )
        self.dispatcher.worker = self.worker


def microbenchmark(
    *, requests_count: int = 24, wait_seconds: float = 5.0,
) -> dict[str, Any]:
    """One logical contract, cold unique inputs and warm exact-key reuse."""
    if not 2 <= requests_count <= 48 or not 0.5 <= wait_seconds <= 10:
        raise ValueError("Unsafe experimental bounds")
    requests = attribution.requests_for(requests_count)
    references = {
        index: calculate_batch(request, WorkerCache(6))
        for index, request in enumerate(requests)
    }
    output: dict[str, Any] = {}
    for mode in ("inprocess", "process"):
        process = (
            InProcessIVWorker(cache_entries=requests_count * 3 + 6, queue_capacity=2)
            if mode == "inprocess" else
            SingleIVWorker(cache_entries=requests_count * 3 + 6, queue_capacity=2)
        )
        startup = perf_counter()
        if not process.start():
            raise RuntimeError("Could not start offline numeric transport")
        startup_ms = (perf_counter() - startup) * 1000
        dispatch = IVDispatcher(
            process, LatestWinsScheduler(2, 2),
            inflight_timeout_seconds=wait_seconds,
        )
        cold: list[float] = []
        warm: list[float] = []
        valid_outputs = 0
        warm_hits = 0
        polls = 0
        try:
            for phase, timings in enumerate((cold, warm)):
                for index, original in enumerate(requests):
                    number = phase * requests_count + index + 1
                    request = replace(
                        original,
                        symbol="SYNTH-ONE-CONTRACT",
                        generation=process.generation,
                        sequence=number,
                        quote_identity=(f"synthetic-event-{number}", "SYNTH-UNDERLYING"),
                    )
                    started = perf_counter()
                    if not dispatch.offer(request, active_risk=True):
                        raise AssertionError("Unexpected bounded scheduler rejection")
                    result: IVWorkResult | None = None
                    deadline = monotonic() + wait_seconds
                    while result is None and monotonic() < deadline:
                        polls += 1
                        result = dispatch.pump()
                        if result is None:
                            sleep(0.0005)
                    duration_ms = (perf_counter() - started) * 1000
                    if result is None:
                        raise TimeoutError("Single-contract risk did not become ready")
                    reference = references[index]
                    if not all((
                        result.bid == reference.bid,
                        result.ask == reference.ask,
                        result.mid == reference.mid,
                    )):
                        raise AssertionError("IVResult differs across execution boundary")
                    if not all((
                        result.bid.valid, result.ask.valid, result.mid.valid,
                    )):
                        raise AssertionError("Unexpected invalid pricing result")
                    timings.append(duration_ms)
                    valid_outputs += 1
                    if phase == 1:
                        warm_hits += result.cache_hits
            output[mode] = {
                "solver": "calculate_batch structured guarded Newton",
                "one_logical_contract": True,
                "dispatcher": "IVDispatcher single in-flight LatestWins",
                "startup_ms": round(startup_ms, 3),
                "cold": attribution.sample_stats(cold),
                "warm": attribution.sample_stats(warm),
                "p95_warm_ms": attribution.quantile(warm, 0.95),
                "warm_cache_hits": warm_hits,
                "valid_outputs": valid_outputs,
                "poll_count": polls,
                "scheduler_overflows": dispatch.scheduler.overflows,
                "stale_results": dispatch.stale_results,
            }
        finally:
            process.close()
    return output


def fair_chain_pair(
    *, options_count: int, events: int, seed: int,
    burst: bool, wait_seconds: float = 5.0,
) -> dict[str, dict[str, Any]]:
    """Exact same full-chain capture, risk versions and portfolio ready contract."""
    if not 2 <= options_count <= 24 or options_count % 2 or not 8 <= events <= 1000:
        raise ValueError("Unsupported synthetic chain shape")
    output: dict[str, dict[str, Any]] = {}
    for mode in ("inprocess", "process"):
        backend = InProcessIVRuntime if mode == "inprocess" else OptionIVRuntime
        with patch.object(chain, "OptionIVRuntime", backend):
            row = chain.replay(
                options_count=options_count, count=events, seed=seed,
                worker=True, burst=burst, wait_seconds=wait_seconds,
                readiness_scope="portfolio",
            )
        row["transport"] = mode
        row["solver"] = "calculate_batch structured guarded Newton"
        if not (row["all_risk_ready_end"] and row["portfolio_actionable_ready_end"]):
            raise AssertionError("Full portfolio never became actionable")
        if not burst and row["paced_event_failures"]:
            raise AssertionError("Portfolio readiness exceeded test waiting bound")
        if row["scheduler_overflows"]:
            raise AssertionError("Unexpected bounded scheduler overflow")
        output[mode] = row
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", type=int, default=6)
    parser.add_argument("--events", type=int, default=36)
    parser.add_argument("--seed", type=int, default=20261123)
    parser.add_argument("--wait-seconds", type=float, default=5)
    parser.add_argument("--micro", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.options < 2 or args.options > 24 or args.options % 2:
        parser.error("--options must be an even number between 2 and 24")
    if args.events < 8 or args.events > 1000:
        parser.error("--events must be between 8 and 1000")
    if not 0.5 <= args.wait_seconds <= 10:
        parser.error("--wait-seconds must be 0.5..10")

    micro = microbenchmark(
        requests_count=24, wait_seconds=args.wait_seconds,
    ) if args.micro else None
    cases = {
        "paced": fair_chain_pair(
            options_count=args.options, events=args.events, seed=args.seed,
            burst=False, wait_seconds=args.wait_seconds,
        ),
        "burst": fair_chain_pair(
            options_count=args.options, events=args.events, seed=args.seed,
            burst=True, wait_seconds=args.wait_seconds,
        ),
    }
    payload = {
        "kind": "optionmaster_identical_structured_solver_transport_comparison",
        "not_a_production_approval": True,
        "environment": {
            "platform": platform.platform(), "python": platform.python_version(),
            "options": args.options, "events": args.events, "seed": args.seed,
            "source": "synthetic_tick_fixture_no_ctp",
        },
        "methods": [
            "Both paths use OptionIVRuntime owner code, IVDispatcher single-in-flight LatestWins, exact-input WorkerCache, calculate_batch and identical IVResult acceptance.",
            "In-process transport performs identical numerical work in main process during poll; process transport uses the spawned worker and IPC.",
            "The complete actionable portfolio snapshot, not only last changed option Greek, determines paced risk readiness.",
            "The inline test double does not model process start, IPC serialization, independent CPU execution or OS worker scheduling.",
            "An all-open synthetic session does not demonstrate approved CTP calendar or production gateway safety.",
            "Polling uses 0.5-ms sleeps and run order is fixed; samples do not establish approved p95/p99 or CPU/RSS SLOs.",
        ],
        "micro": micro,
        "chain": cases,
    }
    rendered = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()

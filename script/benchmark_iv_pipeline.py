"""Reproducible *synthetic* offline OptionMaster IV event-to-risk benchmark.

Run from repository root: .venv/bin/python script/benchmark_iv_pipeline.py --events 40
This is not a production replay, capacity benchmark, or live-trading validation.
It uses the same synthetic OptionEngine ingress and quote series in both paths.
No CTP, network, trading order or application settings are touched.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import sys
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter_ns, sleep
from types import SimpleNamespace
from typing import Any

from vnpy.event import Event
from vnpy.trader.event import EVENT_TICK
from vnpy.trader.object import TickData
from vnpy_optionmaster.engine import OptionEngine
from vnpy_optionmaster.pricing.iv_runtime import OptionIVRuntime
from vnpy_optionmaster.risk_safety import risk_snapshot_is_fresh
from vnpy_optionmaster.trading_sessions import CHINA_TZ

# Test fixtures are used deliberately: this benchmark is an offline development
# harness, not a production data adapter or trading session configuration.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from _session_test_support import synthetic_session_policy  # noqa: E402
from test_mark_quality import _real_option_pair  # noqa: E402


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    left = int(pos)
    right = min(len(ordered) - 1, left + 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (pos - left)


def fixed_quotes(count: int, *, scenario: str) -> list[tuple[float, float]]:
    if scenario == "same-price":
        return [(2.0, 2.5) for _ in range(count)]
    if scenario == "cycling":
        return [
            (2.0 + 0.5 * (i % 6), 2.5 + 0.5 * (i % 6))
            for i in range(count)
        ]
    raise ValueError(f"Unknown scenario: {scenario}")


def make_engine(option: Any, runtime: OptionIVRuntime | None) -> OptionEngine:
    engine: OptionEngine = object.__new__(OptionEngine)
    # Mirror the synchronous portfolio-aggregation call after each ingress.
    exposure = {"delta": 0.0}

    def aggregate() -> None:
        exposure["delta"] = option.pos_delta

    def receive(tick: TickData) -> None:
        option.update_tick(tick)
        aggregate()

    option.portfolio = SimpleNamespace(
        update_tick=receive, calculate_pos_greeks=aggregate,
    )
    engine.instruments = {option.vt_symbol: option}
    engine.session_policy = synthetic_session_policy(option.vt_symbol)
    engine.last_accepted_ticks = {}
    engine.tick_rejections = {}
    engine.iv_runtime = runtime
    return engine


def run_replay(
    *, scenario: str, events: int, async_worker: bool, max_wait_ms: float,
) -> dict[str, Any]:
    underlying, option, initial_tick = _real_option_pair()
    # Use one concrete fixed strike, expiry, source and underlying for both
    # paths. Preserve the same data and chronological sequence.
    runtime: OptionIVRuntime | None = None
    if async_worker:
        runtime = OptionIVRuntime(
            cache_entries=128, queue_capacity=2, max_pending=16,
            max_priority_streak=2, inflight_timeout_seconds=max_wait_ms / 1000,
        )
    engine = make_engine(option, runtime)
    startup_ms: float | None = None
    if runtime is not None:
        start_ns = perf_counter_ns()
        if not runtime.start():
            raise RuntimeError("Offline Worker numerical startup selftest failed")
        startup_ms = (perf_counter_ns() - start_ns) / 1e6
        if not runtime.attach(option):
            runtime.close()
            raise RuntimeError("Worker refused synthetic Black-76 option fixture")

    latencies: list[float] = []
    rejected = 0
    queue_max = 0
    event_start = perf_counter_ns()
    try:
        base_time = datetime.now(CHINA_TZ)
        for i, (bid, ask) in enumerate(fixed_quotes(events, scenario=scenario)):
            tick = TickData(
                gateway_name=initial_tick.gateway_name,
                symbol=initial_tick.symbol, exchange=initial_tick.exchange,
                datetime=base_time + timedelta(microseconds=(i + 1) * 100),
                bid_price_1=bid, ask_price_1=ask,
                bid_volume_1=10, ask_volume_1=10,
            )
            before = perf_counter_ns()
            engine.process_tick_event(Event(EVENT_TICK, tick))
            if runtime is not None:
                deadline = before + int(max_wait_ms * 1e6)
                while not risk_snapshot_is_fresh(option):
                    if perf_counter_ns() >= deadline:
                        break
                    runtime.pump()
                    sleep(0.0005)
                queue_max = max(
                    queue_max, runtime.dispatcher.scheduler.pending_count,
                )
            if risk_snapshot_is_fresh(option):
                latencies.append((perf_counter_ns() - before) / 1e6)
            else:
                rejected += 1
        elapsed_ms = (perf_counter_ns() - event_start) / 1e6
        return {
            "path": "worker" if async_worker else "sync",
            "scenario": scenario,
            "events": events,
            "accepted": len(latencies),
            "not_risk_ready": rejected,
            "p50_ms": round(percentile(latencies, 0.5), 3),
            "p95_ms": round(percentile(latencies, 0.95), 3),
            "p99_ms": round(percentile(latencies, 0.99), 3),
            "max_ms": round(max(latencies, default=float("nan")), 3),
            "throughput_events_per_second": round(events * 1000 / elapsed_ms, 1),
            "observed_pending_max": queue_max,
            "worker_startup_ms": round(startup_ms, 3) if startup_ms is not None else None,
            "rejected_ingress": sum(engine.tick_rejections.values()),
            "worker_accepted": runtime.accepted if runtime is not None else None,
            "worker_rejected": runtime.rejected if runtime is not None else None,
            "worker_generation": runtime.worker.generation if runtime is not None else None,
            "sync_backend": getattr(option.calculate_impv, "__module__", "<unknown>"),
            "underlying_vt_symbol": underlying.vt_symbol,
        }
    finally:
        if runtime is not None:
            runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=40)
    parser.add_argument("--max-wait-ms", type=float, default=4000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.events < 5 or args.events > 1000:
        parser.error("--events must be in [5, 1000]")
    if not math.isfinite(args.max_wait_ms) or args.max_wait_ms <= 0:
        parser.error("--max-wait-ms must be finite and positive")

    results = []
    for scenario in ("same-price", "cycling"):
        for async_worker in (False, True):
            results.append(run_replay(
                scenario=scenario, events=args.events, async_worker=async_worker,
                max_wait_ms=args.max_wait_ms,
            ))
    summary = {
        "kind": "synthetic_offline_option_event_to_risk_benchmark",
        "purpose": "relative comparison, NOT production G5 performance acceptance",
        "profile": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "events_per_run": args.events,
            "case_count": len(results),
            "model": "Black-76",
            "test_session_policy": "SYNTHETIC; forbidden for production",
            "order_gateway": "NONE",
            "max_wait_ms": args.max_wait_ms,
        },
        "measurement_limits": [
            "Single option with fixed underlying and fixed expiry; not full-chain or mixed-feed.",
            "Sequential replay waits for risk after each event; no burst/backpressure stress.",
            "Process startup excluded from per-event latency; shown separately.",
            "No independent CPU or worker RSS telemetry; not a resource capacity test.",
            "Quantiles depend on macOS scheduling and run environment; no acceptance limits frozen.",
        ],
        "runs": results,
    }
    data = json.dumps(summary, ensure_ascii=False, indent=2)
    print(data)
    if args.output is not None:
        args.output.write_text(data + "\n", encoding="utf-8")
    if any(item["not_risk_ready"] or item["rejected_ingress"] for item in results):
        raise SystemExit("Benchmark failed: one or more synthetic Tick inputs did not reach risk readiness")


if __name__ == "__main__":
    main()

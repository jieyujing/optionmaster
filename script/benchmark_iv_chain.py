"""Deterministic-value synthetic full-chain IV workload; never a live gateway.

Run:
  .venv/bin/python script/benchmark_iv_chain.py --options 12 --events 72

All four runs use one seeded option/underlying quote sequence and the actual
OptionEngine -> PortfolioData -> ChainData -> OptionData path. An in-memory
synthetic session class in tests is deliberately forbidden for production use.
CPU and RSS are observational. No claims about external replay, actual CTP,
exchange sessions, or production performance acceptance are made.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import random
import resource
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic, perf_counter, process_time, sleep
from typing import Any

from vnpy.event import Event
from vnpy.trader.event import EVENT_TICK
from vnpy.trader.object import TickData
from vnpy_optionmaster.pricing.iv_runtime import OptionIVRuntime
from vnpy_optionmaster.pricing import black_76
from vnpy_optionmaster.risk_safety import risk_snapshot_is_fresh
from vnpy_optionmaster.trading_sessions import CHINA_TZ

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_iv_chain_burst import _real_chain_fixture  # noqa: E402


def quantile(samples: list[float], q: float) -> float | None:
    if not samples:
        return None
    values = sorted(samples)
    f = (len(values) - 1) * q
    low = int(f)
    high = min(low + 1, len(values) - 1)
    return round(values[low] + (values[high] - values[low]) * (f - low), 3)


def rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def worker_rss_kib(runtime: OptionIVRuntime | None) -> int | None:
    proc = getattr(runtime.worker, "_process", None) if runtime is not None else None
    if proc is None or not proc.is_alive():
        return None
    try:
        output = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(proc.pid)], capture_output=True,
            text=True, timeout=1, check=False,
        )
        return int(output.stdout.strip()) if output.returncode == 0 else None
    except (FileNotFoundError, OSError, ValueError, subprocess.TimeoutExpired):
        return None


def make_descriptors(options: list[Any], count: int, seed: int) -> list[tuple[int, float]]:
    rng = random.Random(seed)
    return [
        (-1, (i // 4 % 5 - 2) * 0.1)
        if i % 4 == 0 else
        (rng.randrange(len(options)), (rng.randrange(5) - 2) * 0.01)
        for i in range(count)
    ]


def make_tick(
    symbol: Any, at: datetime, *, underlying_mid: float,
    iv_delta: float, is_underlying: bool,
) -> TickData:
    if is_underlying:
        center = underlying_mid
        bid, ask = center - 0.1, center + 0.1
    else:
        fair = black_76.calculate_price(
            underlying_mid, symbol.strike_price, symbol.interest_rate,
            symbol.time_to_expiry, 0.32 + iv_delta, symbol.option_type,
        )
        bid = max(0.1, round(fair - 0.1, 1))
        ask = bid + 0.2
    return TickData(
        gateway_name="OFFLINE", symbol=symbol.symbol, exchange=symbol.exchange,
        datetime=at, bid_price_1=round(bid, 1), ask_price_1=round(ask, 1),
        bid_volume_1=30, ask_volume_1=30, last_price=round((bid + ask) / 2, 1),
    )


def replay(*, options_count: int, count: int, seed: int,
           worker: bool, burst: bool, wait_seconds: float,
           readiness_scope: str = "affected") -> dict[str, Any]:
    if readiness_scope not in ("affected", "portfolio"):
        raise ValueError("readiness_scope must be affected or portfolio")
    engine, portfolio, underlying, options = _real_chain_fixture(options_count)
    descriptors = make_descriptors(options, count, seed)
    iv_runtime: OptionIVRuntime | None = None
    if worker:
        iv_runtime = OptionIVRuntime(
            cache_entries=128, queue_capacity=2,
            max_pending=max(16, options_count + 2),
            max_priority_streak=2, inflight_timeout_seconds=wait_seconds,
        )
    startup_ms: float | None = None
    if iv_runtime is not None:
        started = perf_counter()
        if not iv_runtime.start():
            raise RuntimeError("Worker selftest failed")
        startup_ms = (perf_counter() - started) * 1000
        if not all(iv_runtime.attach(x) for x in options):
            iv_runtime.close()
            raise RuntimeError("Worker refused a seeded option")
        engine.iv_runtime = iv_runtime

    # A deterministic *value* sequence is replayed against fresh per-run time
    # anchors. This is not historical source-time or exchange-calendar replay.
    begin_time = datetime.now(CHINA_TZ) - timedelta(milliseconds=500)
    quote_center = 100.0
    latencies_ms: list[float] = []
    ingress_ms: list[float] = []
    not_ready_events = 0
    queue_peak = 0
    unready_during_ingress = 0
    parent_rss_before = rss_bytes()
    cpu_start = process_time()
    child_cpu_start = resource.getrusage(resource.RUSAGE_CHILDREN)
    worker_rss_start = worker_rss_kib(iv_runtime)
    worker_rss_end = None

    def risk_ready(affected: list[Any]) -> bool:
        if readiness_scope == "portfolio":
            return portfolio.actionable_risk_snapshot is not None
        return all(risk_snapshot_is_fresh(x) for x in affected)

    started_wall = perf_counter()
    try:
        for i, (index, change) in enumerate(descriptors):
            is_underlying = index == -1
            if is_underlying:
                quote_center = 100.0 + change
                item = underlying
                affected = options
            else:
                item = options[index]
                affected = [item]
            tick = make_tick(
                item, begin_time + timedelta(milliseconds=i + 1),
                underlying_mid=quote_center, iv_delta=change,
                is_underlying=is_underlying,
            )
            started = perf_counter()
            engine.process_tick_event(Event(EVENT_TICK, tick))
            cost_ms = (perf_counter() - started) * 1000
            ingress_ms.append(cost_ms)
            if iv_runtime is not None:
                queue_peak = max(
                    queue_peak, iv_runtime.dispatcher.scheduler.pending_count,
                )
            ready = risk_ready(affected)
            if not ready:
                unready_during_ingress += 1
            if not burst:
                deadline = monotonic() + wait_seconds
                while not ready and iv_runtime is not None and monotonic() < deadline:
                    iv_runtime.pump()
                    sleep(0.0005)
                    ready = risk_ready(affected)
                    queue_peak = max(
                        queue_peak, iv_runtime.dispatcher.scheduler.pending_count,
                    )
                if ready:
                    latencies_ms.append((perf_counter() - started) * 1000)
                else:
                    not_ready_events += 1
        feed_wall_s = perf_counter() - started_wall

        drain_start = monotonic()
        while iv_runtime is not None and monotonic() - drain_start < wait_seconds:
            iv_runtime.pump()
            if (
                iv_runtime.dispatcher.scheduler.pending_count == 0
                and iv_runtime.dispatcher._in_flight is None
            ):
                break
            sleep(0.0005)
        drain_ms = (monotonic() - drain_start) * 1000 if iv_runtime else 0.0
        worker_rss_end = worker_rss_kib(iv_runtime)

        fresh = sum(risk_snapshot_is_fresh(o) for o in options)
        # Treat a mismatched, spuriously "ready" IV as benchmark failure,
        # independently of the number of dropped pending tasks.
        for option in options:
            if risk_snapshot_is_fresh(option):
                if option.greeks_quote_version != (option.tick, underlying.tick):
                    raise AssertionError("Actionable risk uses old option/underlying Tick")
                if iv_runtime is not None and (
                    option.greeks_worker_generation != iv_runtime.worker.generation
                ):
                    raise AssertionError("Actionable risk uses old Worker generation")
        if engine.tick_rejections:
            raise AssertionError(f"Generated synthetic Tick rejected: {engine.tick_rejections}")
        expected_delta = underlying.pos_delta + sum(o.pos_delta for o in options)
        if not math.isclose(portfolio.pos_delta, expected_delta, rel_tol=0, abs_tol=1e-9):
            raise AssertionError("Portfolio Greek aggregation incoherent")

        data: dict[str, Any] = {
            "path": "worker" if worker else "sync",
            "schedule": "burst" if burst else "paced",
            "readiness_scope": readiness_scope,
            "portfolio_actionable_ready_end": portfolio.actionable_risk_snapshot is not None,
            "options": options_count,
            "events": count,
            "underlying_events": (count + 3) // 4,
            "option_events": count - (count + 3) // 4,
            "held_options": sum(bool(o.net_pos) for o in options),
            "risk_ready_options_end": fresh,
            "all_risk_ready_end": fresh == len(options),
            "not_risk_ready_after_event": unready_during_ingress,
            "paced_event_failures": not_ready_events if not burst else None,
            "paced_event_to_ready_p50_ms": quantile(latencies_ms, 0.5),
            "paced_event_to_ready_p95_ms": quantile(latencies_ms, 0.95),
            "paced_event_to_ready_p99_ms": quantile(latencies_ms, 0.99),
            "ingress_p50_ms": quantile(ingress_ms, 0.5),
            "ingress_p95_ms": quantile(ingress_ms, 0.95),
            "ingress_p99_ms": quantile(ingress_ms, 0.99),
            "feed_wall_ms": round(feed_wall_s * 1000, 2),
            "feed_throughput_events_per_s": round(count / feed_wall_s, 1),
            "drain_ms": round(drain_ms, 2),
            "parent_cpu_s": round(process_time() - cpu_start, 4),
            "parent_peak_rss_bytes": rss_bytes(),
            "parent_peak_rss_before_bytes": parent_rss_before,
            "worker_rss_kib_sampled": max(
                [x for x in (worker_rss_start, worker_rss_end) if x is not None],
                default=None,
            ),
            "pending_peak_observed": queue_peak,
            "worker_accepted": iv_runtime.accepted if iv_runtime else None,
            "worker_rejected": iv_runtime.rejected if iv_runtime else None,
            "scheduler_overflows": iv_runtime.dispatcher.scheduler.overflows if iv_runtime else None,
            "scheduler_superseded": iv_runtime.dispatcher.scheduler.superseded if iv_runtime else None,
            "stale_responses": iv_runtime.dispatcher.stale_results if iv_runtime else None,
            "worker_startup_ms": round(startup_ms, 2) if startup_ms else None,
        }
        return data
    finally:
        if iv_runtime is not None:
            iv_runtime.close()
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        child_cpu_delta = (after.ru_utime - child_cpu_start.ru_utime
                           + after.ru_stime - child_cpu_start.ru_stime)
        # Reaped child CPU includes startup/selftest and computations; do not
        # treat it as per-event CPU time or an independent capacity metric.
        if iv_runtime is not None and "data" in locals():
            data["worker_cpu_s_after_reap"] = round(child_cpu_delta, 4)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", type=int, default=12)
    parser.add_argument("--events", type=int, default=72)
    parser.add_argument("--seed", type=int, default=20261123)
    parser.add_argument("--wait-seconds", type=float, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.options < 2 or args.options > 24 or args.options % 2:
        parser.error("--options must be an even number between 2 and 24")
    if args.events < 8 or args.events > 1000:
        parser.error("--events must be between 8 and 1000")
    if not math.isfinite(args.wait_seconds) or not (0.5 <= args.wait_seconds <= 10):
        parser.error("--wait-seconds must be finite and 0.5..10")
    runs = []
    for schedule in (False, True):
        for worker in (False, True):
            runs.append(replay(
                options_count=args.options, count=args.events, seed=args.seed,
                worker=worker, burst=schedule, wait_seconds=args.wait_seconds,
            ))
    report = {
        "kind": "synthetic_offline_real_chain_fanout_benchmark",
        "not_a_production_approval": True,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "arch": platform.machine(),
            "options": args.options,
            "events": args.events,
            "seed": args.seed,
            "session": "test_only_in_memory_all_open",
            "gateway": "NONE",
            "strategy": "NO_TRADING",
            "underlying": "IF2612.CFFEX (synthetic prices)",
            "pricing_model": "Python Black-76",
            "machine": "current authenticated local Runner",
        },
        "method_limits": [
            "Chronological synthetic quotes, not an actual CTP capture or exchange replay.",
            "Paced waits for every affected option risk; burst measures ingress and final readiness separately.",
            "No numerical acceptance, trading authority or production thresholds are inferred.",
            "Parent RSS is process lifetime peak; child RSS is ps-sampled, not true high-water.",
            "Worker child CPU is post-reap total including process startup, selftest and workload; startup excluded only from per-event latency.",
            "Loads/values and startup timings are affected by host scheduling; comparisons need repetitions.",
        ],
        "runs": runs,
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output is not None:
        args.output.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()

"""OFFLINE urgent held-position HOL timing with real OptionIVRuntime owner commits.

The subprocess adds an EXPLICIT artificial per-request service delay to expose
the non-preemptible batch boundary. It is neither market-data replay nor a
measurement of the production Worker; no live gateway/orders are opened.
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from queue import Empty
from statistics import median
from time import monotonic, sleep
from typing import Any
from unittest.mock import patch

from vnpy.event import Event
from vnpy.trader.event import EVENT_TICK
from vnpy.trader.object import TickData

from vnpy_optionmaster.pricing.iv_worker import WorkerCache
from vnpy_optionmaster.risk_safety import risk_snapshot_is_fresh
from vnpy_optionmaster.trading_sessions import CHINA_TZ

from script.benchmark_iv_batch_prototype import (
    BatchEnvelope, BatchReply, PrototypeBatchWorker, calculate_group,
)
from script.benchmark_iv_microbatch_integration import MicrobatchIVRuntime
from script.benchmark_iv_chain import _real_chain_fixture


def _timed_worker_main(
    inbox: Any, outbox: Any, ready: Any, timeline: Any,
    cache_entries: int, delay_per_member_s: float,
) -> None:
    """Artificial wait happens AFTER a batch is dequeued; no preemption."""
    cache = WorkerCache(cache_entries)
    ready.set()
    while True:
        batch: BatchEnvelope | None = inbox.get()
        if batch is None:
            return
        started = monotonic()
        timeline.put(("start", batch.batch_id, started))
        try:
            sleep(delay_per_member_s * len(batch.requests))
            result = calculate_group(batch, cache)
        except Exception:
            result = BatchReply(batch.batch_id, batch.generation, (), 0, failed=True)
        timeline.put(("finish", batch.batch_id, monotonic()))
        outbox.put(result)


class TracedBatchWorker(PrototypeBatchWorker):
    """Test-only observed process, keeping the base IPC/validation semantics."""

    def __init__(
        self, *, cache_entries: int, queue_capacity: int,
        max_batch: int, delay_per_member_s: float,
    ) -> None:
        super().__init__(
            cache_entries=cache_entries, queue_capacity=queue_capacity,
            max_batch=max_batch,
        )
        if not isfinite(delay_per_member_s) or not 0 < delay_per_member_s <= 0.02:
            raise ValueError("Only explicit bounded artificial delays are supported")
        self.delay_per_member_s = delay_per_member_s
        self.timeline: Any = None
        self.submissions: dict[int, tuple[float, tuple[str, ...]]] = {}
        self.receipts: dict[int, float] = {}

    def start(self, *, ready_timeout: float = 5.0) -> bool:
        if self.alive:
            return True
        self.close()
        self.generation += 1
        self._inbox = self._ctx.Queue(maxsize=self.queue_capacity)
        self._outbox = self._ctx.Queue(maxsize=self.queue_capacity)
        self.timeline = self._ctx.Queue(maxsize=32)
        ready = self._ctx.Event()
        self._process = self._ctx.Process(
            target=_timed_worker_main,
            args=(
                self._inbox, self._outbox, ready, self.timeline,
                self.cache_entries, self.delay_per_member_s,
            ),
            daemon=True,
        )
        self._process.start()
        if not ready.wait(timeout=ready_timeout) or not self.alive:
            self.close()
            return False
        return True

    def submit(self, batch: BatchEnvelope) -> bool:
        at = monotonic()
        accepted = super().submit(batch)
        if accepted:
            self.submissions[batch.batch_id] = (
                at, tuple(r.symbol for r in batch.requests),
            )
        return accepted

    def poll(self) -> BatchReply | None:
        result = super().poll()
        if result is not None:
            self.receipts[result.batch_id] = monotonic()
        return result

    def close(self) -> None:
        timeline = self.timeline
        super().close()
        if timeline is not None:
            timeline.close()
            timeline.cancel_join_thread()
        self.timeline = None


class TracedMicrobatchRuntime(MicrobatchIVRuntime):
    """Retain actual capture/commit/portfolio readiness; replace only test IPC."""

    def __init__(self, *, batch_size: int, delay_per_member_s: float, **kwargs: Any) -> None:
        super().__init__(batch_size=batch_size, **kwargs)
        self.worker = TracedBatchWorker(
            cache_entries=kwargs["cache_entries"],
            queue_capacity=kwargs["queue_capacity"],
            max_batch=batch_size, delay_per_member_s=delay_per_member_s,
        )
        self.dispatcher.worker = self.worker


def _wait_trace(
    timeline: Any, history: list[tuple[str, int, float]],
    *, phase: str, batch_id: int, timeout_s: float = 3.0,
) -> float:
    deadline = monotonic() + timeout_s
    while monotonic() < deadline:
        try:
            item = timeline.get(timeout=min(0.1, max(0.001, deadline - monotonic())))
        except Empty:
            continue
        history.append(item)
        if item[0] == phase and item[1] == batch_id:
            return item[2]
    raise AssertionError(f"Timed Worker did not report {phase} for batch {batch_id}")


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 3)


def measure_urgent(
    *, batch_size: int, seed: int, arrival_fraction: float,
    delay_per_member_s: float = 0.006, option_count: int = 12,
) -> dict[str, Any]:
    """Force a fresh held Tick to arrive while a non-risk batch is executing."""
    if batch_size not in (1, 4, 8) or arrival_fraction not in (0.0, 0.5):
        raise ValueError("Only cap 1/4/8 and arrival offsets 0/0.5 supported")
    if option_count != 12 or not 0 < delay_per_member_s <= 0.02:
        raise ValueError("Only bounded 12-option synthetic chains and service delay")
    engine, portfolio, _, options = _real_chain_fixture(option_count)
    rng = random.Random(seed)
    held = options[rng.randrange(len(options))]
    for option in options:
        option.net_pos = 0
        option.long_pos = 0
        option.short_pos = 0
    held.long_pos = held.net_pos = 2
    portfolio.calculate_pos_greeks()
    ordinary = [option for option in options if option is not held]
    rng.shuffle(ordinary)
    runtime = TracedMicrobatchRuntime(
        batch_size=batch_size, delay_per_member_s=delay_per_member_s,
        cache_entries=64, queue_capacity=2, max_pending=24,
        max_priority_streak=2, inflight_timeout_seconds=4.0,
    )
    history: list[tuple[str, int, float]] = []
    try:
        if not runtime.start() or not all(runtime.attach(o) for o in options):
            raise AssertionError("Test-only Worker failed to start/attach")
        engine.iv_runtime = runtime
        runtime.submit(held)
        deadline = monotonic() + 3.0
        while portfolio.actionable_risk_snapshot is None and monotonic() < deadline:
            runtime.pump()
            sleep(0.0004)
        if portfolio.actionable_risk_snapshot is None:
            raise AssertionError("Initial held-only portfolio never became actionable")

        # Bounded test-only accumulation, preserving the real owner submit/capture.
        with patch.object(runtime, "pump", return_value=False):
            for option in ordinary[:batch_size]:
                runtime.submit(option)
        if runtime.dispatcher.scheduler.pending_count != batch_size:
            raise AssertionError("Ordinary batch could not be filled")
        runtime.pump()
        group = runtime.dispatcher._in_flight
        if group is None or len(group.requests) != batch_size:
            raise AssertionError("Exact ordinary group was not dispatched")
        ordinary_id = group.batch_id
        if any(r.symbol == held.vt_symbol for r in group.requests):
            raise AssertionError("Held work must not be in ordinary batch")
        ordinary_started = _wait_trace(
            runtime.worker.timeline, history, phase="start", batch_id=ordinary_id,
        )
        sleep(delay_per_member_s * batch_size * arrival_fraction)
        previous = held.tick
        if previous is None:
            raise AssertionError("No synthetic held Tick")
        bid = round(previous.bid_price_1 + 0.1, 2)
        ask = round(previous.ask_price_1 + 0.1, 2)
        changed_tick = TickData(
            gateway_name=previous.gateway_name, symbol=previous.symbol,
            exchange=previous.exchange, datetime=datetime.now(CHINA_TZ),
            bid_price_1=bid, ask_price_1=ask,
            bid_volume_1=30, ask_volume_1=30,
            last_price=round((bid + ask) / 2, 2),
        )
        arrived = monotonic()
        engine.process_tick_event(Event(EVENT_TICK, changed_tick))
        if engine.tick_rejections:
            raise AssertionError(f"Synthetic held Tick rejected: {engine.tick_rejections}")
        if portfolio.actionable_risk_snapshot is not None:
            raise AssertionError("New held Tick must revoke old portfolio risk")
        if held.vt_symbol not in runtime.pending:
            raise AssertionError("New held IV request not pending")

        ready_at: float | None = None
        option_ready_at: float | None = None
        deadline = monotonic() + 4.0
        while monotonic() < deadline:
            runtime.pump()
            now = monotonic()
            if option_ready_at is None and risk_snapshot_is_fresh(held):
                option_ready_at = now
            if portfolio.actionable_risk_snapshot is not None:
                ready_at = now
                break
            sleep(0.0004)
        if ready_at is None or option_ready_at is None:
            raise AssertionError("Urgent held risk never returned to actionable state")
        urgent_batches = [
            key for key, (_, symbols) in runtime.worker.submissions.items()
            if held.vt_symbol in symbols and key != ordinary_id
        ]
        if len(urgent_batches) != 2:
            # First held-only warmup, then the refreshed urgent held request.
            raise AssertionError(f"Unexpected urgent batch mapping: {urgent_batches}")
        urgent_id = max(urgent_batches)
        ordinary_finished = _wait_trace(
            runtime.worker.timeline, history, phase="finish", batch_id=ordinary_id,
        )
        urgent_started = _wait_trace(
            runtime.worker.timeline, history, phase="start", batch_id=urgent_id,
        )
        urgent_finished = _wait_trace(
            runtime.worker.timeline, history, phase="finish", batch_id=urgent_id,
        )
        urgent_submitted = runtime.worker.submissions[urgent_id][0]
        if not (
            ordinary_started <= arrived <= ordinary_finished
            <= urgent_submitted <= urgent_started <= urgent_finished
            <= option_ready_at <= ready_at
        ):
            raise AssertionError("Trace invalid: urgent arrival not during ordinary batch")
        if runtime.dispatcher.scheduler.overflows or runtime.dispatcher.timed_out:
            raise AssertionError("Queue failure or timeout")
        return {
            "batch_size": batch_size, "seed": seed, "arrival_fraction": arrival_fraction,
            "delay_per_member_ms": _ms(delay_per_member_s),
            "held_vt_symbol": held.vt_symbol,
            "ordinary_batch_size": batch_size,
            "ordinary_service_ms": _ms(ordinary_finished - ordinary_started),
            "remaining_nonpreemptible_ms": _ms(ordinary_finished - arrived),
            "urgent_queue_to_dispatch_ms": _ms(urgent_submitted - arrived),
            "urgent_dispatch_to_worker_ms": _ms(urgent_started - urgent_submitted),
            "urgent_worker_service_ms": _ms(urgent_finished - urgent_started),
            "urgent_worker_to_owner_ready_ms": _ms(option_ready_at - urgent_finished),
            "urgent_quote_to_option_ready_ms": _ms(option_ready_at - arrived),
            "urgent_quote_to_portfolio_ready_ms": _ms(ready_at - arrived),
            "portfolio_risk_actionable": True,
            "urgent_next_batch": urgent_id == ordinary_id + 1,
            "scheduler_overflows": runtime.dispatcher.scheduler.overflows,
            "stale_responses": runtime.dispatcher.stale_results,
            "test_only": True,
        }
    finally:
        runtime.close()


def run_study(
    *, seeds: tuple[int, ...] = (20261123, 20261124, 20261125),
    delay_per_member_s: float = 0.006,
) -> dict[str, Any]:
    if not 1 <= len(seeds) <= 3:
        raise ValueError("At most three test seeds")
    records = []
    # Rotate cap order per seed; no meaningful statistical CI at this sample size.
    orders = ((1, 4, 8), (8, 1, 4), (4, 8, 1))
    for i, seed in enumerate(seeds):
        for arrival_fraction in (0.0, 0.5):
            for batch_size in orders[i]:
                records.append(measure_urgent(
                    batch_size=batch_size, seed=seed,
                    arrival_fraction=arrival_fraction,
                    delay_per_member_s=delay_per_member_s,
                ))
    medians = [{
        "batch_size": batch_size, "arrival_fraction": frac,
        "n_seeds": len(seeds),
        "median_urgent_quote_to_portfolio_ready_ms": median(
            item["urgent_quote_to_portfolio_ready_ms"] for item in records
            if item["batch_size"] == batch_size and item["arrival_fraction"] == frac
        ),
        "median_urgent_queue_to_dispatch_ms": median(
            item["urgent_queue_to_dispatch_ms"] for item in records
            if item["batch_size"] == batch_size and item["arrival_fraction"] == frac
        ),
        "median_remaining_nonpreemptible_ms": median(
            item["remaining_nonpreemptible_ms"] for item in records
            if item["batch_size"] == batch_size and item["arrival_fraction"] == frac
        ),
    } for frac in (0.0, 0.5) for batch_size in (1, 4, 8)]
    return {
        "kind": "optionmaster_urgent_held_risk_hol_offline",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "synthetic": True, "production_approved": False,
        "real_market_data": False, "real_orders": False,
        "options": 12, "held_positions": 1,
        "seeds": list(seeds),
        "artificial_service_ms_per_request": _ms(delay_per_member_s),
        "batch1_is_test_prototype": True,
        "n_records": len(records),
        "summary": medians, "records": records,
        "limitations": [
            "Injected per-member sleeps are controlled HOL stress, not real Worker compute time",
            "One held contract only; not representative held-portfolio fanout",
            "Sequential rotated experiments; three seeds cannot establish p95/p99 tail SLO",
            "Only synthetic all-open sessions; no source chronology, CTP, Windows or orders",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--delay-ms", type=float, default=6.0)
    args = parser.parse_args()
    if not isfinite(args.delay_ms) or not 0 < args.delay_ms <= 20:
        parser.error("--delay-ms must be within (0,20]")
    report = run_study(delay_per_member_s=args.delay_ms / 1000)
    rendered = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()

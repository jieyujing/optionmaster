"""Non-injected numeric service / held-risk HOL, entirely synthetic and OFFLINE.

A test-only event gate guarantees urgent arrival before ordinary numerical work
without adding sleep to calculate_group. Gate/scheduling overhead is reported
separately; NOT a production latency/SLO benchmark.
"""
from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable
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

from vnpy_optionmaster.pricing.iv_worker import LatestWinsScheduler, WorkerCache
from vnpy_optionmaster.risk_safety import risk_snapshot_is_fresh
from vnpy_optionmaster.trading_sessions import CHINA_TZ

from script.benchmark_iv_batch_prototype import (
    BatchEnvelope, BatchReply, PrototypeBatchWorker, calculate_group,
)
from script.benchmark_iv_chain import _real_chain_fixture
from script.benchmark_iv_microbatch_integration import (
    MicrobatchDispatcher, MicrobatchIVRuntime,
)


def _numerical_worker_main(
    inbox: Any, outbox: Any, ready: Any, timeline: Any,
    gate_batch_id: Any, at_gate: Any, release_gate: Any,
    cache_entries: int,
) -> None:
    """True calculate_group service: no injected sleep, with an ordinary-batch gate."""
    cache = WorkerCache(cache_entries)
    ready.set()
    while True:
        batch: BatchEnvelope | None = inbox.get()
        if batch is None:
            return
        is_gated = batch.batch_id == gate_batch_id.value
        if is_gated:
            timeline.put(("at_gate", batch.batch_id, monotonic()))
            at_gate.set()
            if not release_gate.wait(timeout=3.0):
                outbox.put(BatchReply(
                    batch.batch_id, batch.generation, (), 0, failed=True,
                ))
                continue
        # Capture timestamps on either side of numerical work, BEFORE writing
        # either trace record: IPC trace overhead is not numerical service.
        started = monotonic()
        try:
            result = calculate_group(batch, cache)
        except Exception:
            result = BatchReply(batch.batch_id, batch.generation, (), 0, failed=True)
        finished = monotonic()
        timeline.put(("compute_start", batch.batch_id, started))
        timeline.put(("compute_end", batch.batch_id, finished))
        outbox.put(result)


class NumericalTracedWorker(PrototypeBatchWorker):
    """Test-only subprocess with one externally synchronized ordinary batch."""

    def __init__(self, *, cache_entries: int, queue_capacity: int, max_batch: int):
        super().__init__(
            cache_entries=cache_entries, queue_capacity=queue_capacity,
            max_batch=max_batch,
        )
        self.timeline: Any = None
        self.gate_batch_id: Any = None
        self.at_gate: Any = None
        self.release_gate: Any = None
        self.submissions: dict[int, tuple[float, tuple[str, ...]]] = {}

    def start(self, *, ready_timeout: float = 5.0) -> bool:
        if self.alive:
            return True
        self.close()
        self.generation += 1
        self._inbox = self._ctx.Queue(maxsize=self.queue_capacity)
        self._outbox = self._ctx.Queue(maxsize=self.queue_capacity)
        self.timeline = self._ctx.Queue(maxsize=64)
        self.gate_batch_id = self._ctx.Value("i", 0)
        self.at_gate = self._ctx.Event()
        self.release_gate = self._ctx.Event()
        ready = self._ctx.Event()
        self._process = self._ctx.Process(
            target=_numerical_worker_main,
            args=(
                self._inbox, self._outbox, ready, self.timeline,
                self.gate_batch_id, self.at_gate, self.release_gate,
                self.cache_entries,
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
        ok = super().submit(batch)
        if ok:
            self.submissions[batch.batch_id] = (
                at, tuple(req.symbol for req in batch.requests),
            )
        return ok

    def close(self) -> None:
        # Always release a potential blocked child before termination.
        if self.release_gate is not None:
            self.release_gate.set()
        trace = self.timeline
        super().close()
        if trace is not None:
            trace.close()
            trace.cancel_join_thread()
        self.timeline = None
        self.at_gate = None
        self.release_gate = None
        self.gate_batch_id = None


class NumericalTracedRuntime(MicrobatchIVRuntime):
    """Only experimental IPC is swapped; real owner capture/commit is inherited."""

    def __init__(self, *, batch_size: int, **kwargs: Any) -> None:
        super().__init__(batch_size=batch_size, **kwargs)
        self.worker = NumericalTracedWorker(
            cache_entries=kwargs["cache_entries"],
            queue_capacity=kwargs["queue_capacity"], max_batch=batch_size,
        )
        self.dispatcher.worker = self.worker

def candidate_batch_cap(*, held_exposure_count: int, pending_held_count: int) -> int:
    """Illustrative, NOT approved: cap ordinary work under risk exposure."""
    if (held_exposure_count < 0 or pending_held_count < 0
            or pending_held_count > held_exposure_count):
        raise ValueError("Held pending count must be bounded by held exposure")
    if held_exposure_count == 0:
        return 8
    if pending_held_count >= 2:
        return 4
    return 1


class CandidateAdaptiveDispatcher(MicrobatchDispatcher):
    """Test-only pending-batch cap; cannot interrupt an already sent batch."""

    def __init__(
        self, *args: Any, held_exposure: Callable[[], int], **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.held_exposure = held_exposure
        self.cap_history: list[tuple[int, int, int]] = []

    def pump(self) -> Any:
        if (self._in_flight is None and not self._ready
                and self.scheduler.pending_count):
            pending_held = sum(
                int(priority) for _, priority in self.scheduler._pending.values()
            )
            exposure = self.held_exposure()
            cap = candidate_batch_cap(
                held_exposure_count=exposure, pending_held_count=pending_held,
            )
            self.max_batch = cap
            self.cap_history.append((exposure, pending_held, cap))
        return super().pump()


class CandidateAdaptiveRuntime(NumericalTracedRuntime):
    """One numerical Worker, an experimental cap selector at dispatch only."""

    def __init__(self, *, batch_size: int, **kwargs: Any) -> None:
        if batch_size != 8:
            raise ValueError("Candidate maximum batch cap must be 8")
        super().__init__(batch_size=batch_size, **kwargs)
        self.dispatcher = CandidateAdaptiveDispatcher(
            self.worker,
            LatestWinsScheduler(
                kwargs["max_pending"], kwargs["max_priority_streak"],
            ),
            max_batch=8,
            inflight_timeout_seconds=kwargs["inflight_timeout_seconds"],
            held_exposure=lambda: sum(bool(o.net_pos) for o in self.options.values()),
        )


def _trace_events(timeline: Any, batch_ids: set[int], timeout_s: float = 3.0):
    traces: dict[int, dict[str, float]] = {batch: {} for batch in batch_ids}
    deadline = monotonic() + timeout_s
    while monotonic() < deadline:
        if all(
            "compute_start" in traces[batch] and "compute_end" in traces[batch]
            for batch in batch_ids
        ):
            return traces
        try:
            phase, batch_id, at = timeline.get(
                timeout=min(0.05, max(0.001, deadline - monotonic())),
            )
        except Empty:
            continue
        if batch_id in traces:
            traces[batch_id][phase] = at
    raise AssertionError("Missing subprocess numerical-service trace events")


def _ms(seconds: float) -> float:
    return round(1000 * seconds, 3)


def _new_tick(original: TickData, *, underlying: bool) -> TickData:
    bid = round(original.bid_price_1 + (0.1 if underlying else 0.2), 2)
    ask = round(original.ask_price_1 + (0.1 if underlying else 0.2), 2)
    return TickData(
        gateway_name=original.gateway_name,
        symbol=original.symbol, exchange=original.exchange,
        datetime=datetime.now(CHINA_TZ),
        bid_price_1=bid, ask_price_1=ask,
        bid_volume_1=30, ask_volume_1=30,
        last_price=round((bid + ask) / 2, 2),
    )


def measure_numerical_hol(
    *, batch_size: int, seed: int, event_kind: str = "option",
    cache_state: str = "cold", option_count: int = 12,
    adaptive: bool = False, matched_backlog: bool = False,
) -> dict[str, Any]:
    """One urgent event arrives during an ordinary batch held at a test-only gate."""
    if batch_size not in (1, 4, 8):
        raise ValueError("Experimental batch size must be 1/4/8")
    if event_kind not in ("option", "underlying"):
        raise ValueError("event_kind must be option or underlying")
    if cache_state not in ("cold", "warm") or option_count != 12:
        raise ValueError("Only cold/warm 12-option fixture supported")
    if not isinstance(adaptive, bool) or not isinstance(matched_backlog, bool):
        raise ValueError("Policy and backlog flags must be explicit booleans")
    engine, portfolio, underlying, options = _real_chain_fixture(option_count)
    rng = random.Random(seed)
    rng.shuffle(options)
    held_count = 1 if event_kind == "option" else 3
    held, ordinary = options[:held_count], options[held_count:]
    for option in options:
        option.long_pos = option.net_pos = option.short_pos = 0
    for option in held:
        option.long_pos = option.net_pos = 2
    portfolio.calculate_pos_greeks()

    runtime_type = CandidateAdaptiveRuntime if adaptive else NumericalTracedRuntime
    runtime = runtime_type(
        batch_size=batch_size, cache_entries=128, queue_capacity=2,
        max_pending=24, max_priority_streak=2, inflight_timeout_seconds=4.0,
    )
    try:
        if not runtime.start() or not all(runtime.attach(x) for x in options):
            raise AssertionError("Test-only Worker could not attach")
        engine.iv_runtime = runtime

        # Prepare a fully actionable held-position baseline.
        for option in held:
            runtime.submit(option)
        deadline = monotonic() + 4
        while portfolio.actionable_risk_snapshot is None and monotonic() < deadline:
            runtime.pump()
            sleep(0.0004)
        if portfolio.actionable_risk_snapshot is None:
            raise AssertionError("Bootstrap held portfolio was never actionable")

        staged_count = 8 if (adaptive or matched_backlog) else batch_size
        if cache_state == "warm":
            for option in ordinary[:staged_count]:
                runtime.submit(option)
            deadline = monotonic() + 4
            while monotonic() < deadline:
                runtime.pump()
                done = (
                    runtime.dispatcher.scheduler.pending_count == 0
                    and runtime.dispatcher._in_flight is None
                    and not runtime.dispatcher._ready
                    and all(
                        risk_snapshot_is_fresh(x) for x in ordinary[:staged_count]
                    )
                )
                if done:
                    break
                sleep(0.0004)
            else:
                raise AssertionError("Warm numerical cache could not initialize")

        # Queue exactly batch_size ordinary requests without changing production.
        with patch.object(runtime, "pump", return_value=False):
            for option in ordinary[:staged_count]:
                runtime.submit(option)
        if runtime.dispatcher.scheduler.pending_count != staged_count:
            raise AssertionError("Failed to accumulate ordinary batch")
        gated_id = runtime.dispatcher._batch_id + 1
        runtime.worker.gate_batch_id.value = gated_id
        runtime.pump()
        outgoing = runtime.dispatcher._in_flight
        if outgoing is None or outgoing.batch_id != gated_id:
            raise AssertionError("Ordinary gate batch not dispatched")
        if len(outgoing.requests) != (1 if adaptive else batch_size):
            raise AssertionError("Wrong ordinary dispatch cap for tested policy")
        if any(x.symbol in {h.vt_symbol for h in held} for x in outgoing.requests):
            raise AssertionError("Held request unexpectedly in ordinary group")
        if not runtime.worker.at_gate.wait(timeout=3):
            raise AssertionError("Numerical Worker never reached ordinary gate")
        if portfolio.actionable_risk_snapshot is None:
            raise AssertionError("Ordinary submissions must not revoke held risk")

        subject = held[0] if event_kind == "option" else underlying
        if subject.tick is None:
            raise AssertionError("Synthetic target missing Tick")
        tick = _new_tick(subject.tick, underlying=event_kind == "underlying")
        arrived = monotonic()
        engine.process_tick_event(Event(EVENT_TICK, tick))
        released = monotonic()
        if engine.tick_rejections:
            raise AssertionError(f"Synthetic Tick rejected: {engine.tick_rejections}")
        if portfolio.actionable_risk_snapshot is not None:
            raise AssertionError("New quote must immediately revoke actionable risk")
        if not all(x.vt_symbol in runtime.pending for x in held[:1]):
            raise AssertionError("At least one held instrument must be recomputed")

        # A real Worker runs only numerical work after release; no injected sleep.
        runtime.worker.release_gate.set()
        deadline = monotonic() + 4
        ready_at = None
        while monotonic() < deadline:
            runtime.pump()
            if portfolio.actionable_risk_snapshot is not None:
                ready_at = monotonic()
                break
            sleep(0.0004)
        if ready_at is None:
            raise AssertionError("Held portfolio did not return to actionable state")
        if any(not risk_snapshot_is_fresh(x) for x in held):
            raise AssertionError("Stale held Greek after portfolio readied")
        if runtime.dispatcher.scheduler.overflows or runtime.dispatcher.timed_out:
            raise AssertionError("Fail-closed scheduler overflow/timeout")

        target_symbols = {x.vt_symbol for x in held}
        all_subs = runtime.worker.submissions
        urgent_ids = sorted(
            i for i, (_, symbols) in all_subs.items()
            if i > gated_id and target_symbols.intersection(symbols)
        )
        if not urgent_ids or min(urgent_ids) != gated_id + 1:
            raise AssertionError("Held work did not enter next dispatched batch")
        target_ids = {gated_id, *urgent_ids}
        traces = _trace_events(runtime.worker.timeline, target_ids)
        ordinary_start = traces[gated_id]["compute_start"]
        ordinary_finish = traces[gated_id]["compute_end"]
        earliest_held_submit = all_subs[min(urgent_ids)][0]
        latest_held_finish = max(traces[i]["compute_end"] for i in urgent_ids)
        if not (
            arrived <= released <= ordinary_start <= ordinary_finish
            <= earliest_held_submit <= latest_held_finish <= ready_at
        ):
            raise AssertionError("Subprocess trace order invalid")
        if not isfinite(ready_at - arrived):
            raise AssertionError("Nonfinite risk-ready delay")
        return {
            "seed": seed, "batch_size": batch_size, "event_kind": event_kind,
            "policy": "risk_aware_candidate" if adaptive else "fixed",
            "matched_backlog": matched_backlog,
            "ordinary_staged_count": staged_count,
            "urgent_batch_sizes": [len(all_subs[i][1]) for i in urgent_ids],
            "cache_state": cache_state, "held_count": held_count,
            "ordinary_batch_size": len(outgoing.requests),
            "gate_hold_excluded_from_service": True,
            "test_only_barrier": True,
            "injected_worker_sleep_ms": 0.0,
            "ordinary_numerical_service_ms": _ms(ordinary_finish - ordinary_start),
            "ordinary_compute_start_after_release_ms": _ms(
                ordinary_start - released
            ),
            "ordinary_finish_to_first_held_dispatch_ms": _ms(
                earliest_held_submit - ordinary_finish
            ),
            "first_held_dispatch_after_release_ms": _ms(
                earliest_held_submit - released
            ),
            "last_held_result_to_portfolio_ready_ms": _ms(
                ready_at - latest_held_finish
            ),
            "tick_to_portfolio_ready_ms": _ms(ready_at - arrived),
            "first_held_next_batch": min(urgent_ids) == gated_id + 1,
            "held_batch_count": len(urgent_ids),
            "portfolio_risk_actionable": True,
            "worker_generation": runtime.worker.generation,
            "scheduler_overflows": runtime.dispatcher.scheduler.overflows,
            "stale_responses": runtime.dispatcher.stale_results,
            "experimental_only": True,
        }
    finally:
        runtime.close()


def run_study(*, seeds: tuple[int, ...] = (
    20261123, 20261124, 20261125,
), cache_states: tuple[str, ...] = ("cold", "warm")) -> dict[str, Any]:
    if not (1 <= len(seeds) <= 3) or not cache_states or any(
        state not in ("cold", "warm") for state in cache_states
    ):
        raise ValueError("At most three seeds and cold/warm cache states only")
    records = []
    orders = ((1, 4, 8), (8, 1, 4), (4, 8, 1))
    for idx, seed in enumerate(seeds):
        for cache_state in cache_states:
            for event_kind in ("option", "underlying"):
                for batch_size in orders[idx]:
                    records.append(measure_numerical_hol(
                        batch_size=batch_size, seed=seed,
                        event_kind=event_kind, cache_state=cache_state,
                    ))
    summary = [
        {
            "cache_state": state, "event_kind": kind, "batch_size": cap,
            "n_seeds": len(seeds),
            "median_numerical_service_ms": median(
                row["ordinary_numerical_service_ms"] for row in records
                if row["cache_state"] == state
                and row["event_kind"] == kind and row["batch_size"] == cap
            ),
            "median_tick_to_actionable_ms": median(
                row["tick_to_portfolio_ready_ms"] for row in records
                if row["cache_state"] == state
                and row["event_kind"] == kind and row["batch_size"] == cap
            ),
            "median_first_held_dispatch_after_release_ms": median(
                row["first_held_dispatch_after_release_ms"] for row in records
                if row["cache_state"] == state
                and row["event_kind"] == kind and row["batch_size"] == cap
            ),
        }
        for state in cache_states
        for kind in ("option", "underlying")
        for cap in (1, 4, 8)
    ]
    return {
        "kind": "optionmaster_noninjected_numerical_held_hol",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "synthetic": True, "production_approved": False,
        "live_market_source": False, "real_orders": False,
        "options": 12, "seeds": list(seeds),
        "n_records": len(records), "summary": summary, "records": records,
        "test_only_gate_used": True, "worker_sleep_injected": False,
        "batch1_not_production_worker": True,
        "limitations": [
            "A test synchronization barrier forces urgent arrival prior to ordinary numerical work",
            "Subprocess service excludes the barrier, but end-to-end timing includes release/scheduling overhead",
            "Three seeds per condition are insufficient to estimate production p95/p99",
            "Synthetic marks/trading sessions; no real CTP, Windows, order or source-switch acceptance",
            "One held or three held contracts only; not full portfolio distribution",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_study()
    rendered = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()

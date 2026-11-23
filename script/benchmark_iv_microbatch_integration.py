"""Opt-in OFFLINE owner-integrated bounded IV microbatch experiment.

No production IV worker, order gateway, risk gate or default path is modified.
Uses real OptionIVRuntime capture/commit, LatestWinsScheduler, and
PortfolioData.actionable_risk_snapshot while swapping only its IPC dispatcher.
"""
from __future__ import annotations

import argparse
import json
import platform
from collections import deque
from math import isfinite
from pathlib import Path
from time import monotonic
from typing import Any
from unittest.mock import patch

from vnpy_optionmaster.pricing.iv_runtime import OptionIVRuntime
from vnpy_optionmaster.pricing.iv_worker import (
    IVWorkRequest, IVWorkResult, LatestWinsScheduler,
)

if __package__:
    from .benchmark_iv_batch_prototype import (
        BatchEnvelope, BatchReply, PrototypeBatchWorker,
    )
    from . import benchmark_iv_chain as chain
else:
    from benchmark_iv_batch_prototype import (
        BatchEnvelope, BatchReply, PrototypeBatchWorker,
    )
    import benchmark_iv_chain as chain


class MicrobatchDispatcher:
    """Test-only risk-first Latest-Wins coordinator for one in-flight microbatch.

    Owner holds every result until the generation, model and quote identity
    are checked at consumption, not only when the batch arrives. A malformed
    group is fatal to this Worker generation; stale individual members are
    discarded while independently current siblings may still be committed.
    """

    def __init__(
        self, worker: Any, scheduler: LatestWinsScheduler, *,
        max_batch: int, inflight_timeout_seconds: float | None = None,
    ) -> None:
        if not 1 <= max_batch <= 8:
            raise ValueError("The offline microbatch cap must be between 1 and 8")
        if inflight_timeout_seconds is not None and (
            not isfinite(inflight_timeout_seconds) or inflight_timeout_seconds <= 0
        ):
            raise ValueError("Timeout must be explicitly positive and finite")
        self.worker = worker
        self.scheduler = scheduler
        self.max_batch = max_batch
        self.inflight_timeout_seconds = inflight_timeout_seconds
        self._in_flight: BatchEnvelope | None = None
        self._inflight_since: float | None = None
        self._ready: deque[tuple[IVWorkRequest, IVWorkResult]] = deque()
        self._latest: dict[str, IVWorkRequest] = {}
        self._batch_id = 0
        self.stale_results = 0
        self.timed_out = False

    def offer(self, request: IVWorkRequest, *, active_risk: bool) -> bool:
        if self.timed_out or not self.worker.alive or request.generation != self.worker.generation:
            return False
        old = self._latest.get(request.symbol)
        if old is not None and (
            request.generation, request.sequence
        ) <= (old.generation, old.sequence):
            return False
        if not self.scheduler.offer(request, active_risk=active_risk):
            return False
        self._latest[request.symbol] = request
        return True

    def _fail_closed(self) -> None:
        self.timed_out = True
        self._in_flight = None
        self._inflight_since = None
        self._ready.clear()
        self._latest.clear()
        self.worker.close()

    def _accept_reply(self, reply: BatchReply, sent: BatchEnvelope) -> bool:
        if (
            reply.failed or reply.batch_id != sent.batch_id
            or reply.generation != sent.generation
            or len(reply.results) != len(sent.requests)
        ):
            return False
        for request, result in zip(sent.requests, reply.results, strict=True):
            if (
                result.symbol != request.symbol
                or result.generation != request.generation
                or result.sequence != request.sequence
                or result.quote_identity != request.quote_identity
                or result.model_version != request.model_version
            ):
                return False
        return True

    def _consume_ready(self) -> IVWorkResult | None:
        while self._ready:
            request, result = self._ready.popleft()
            if self._latest.get(request.symbol) == request:
                self._latest.pop(request.symbol, None)
                return result
            self.stale_results += 1
        return None

    def pump(self) -> IVWorkResult | None:
        if self.timed_out or not self.worker.alive:
            self._ready.clear()
            self._latest.clear()
            self._in_flight = None
            self._inflight_since = None
            return None

        if (
            self._in_flight is not None
            and self._inflight_since is not None
            and self.inflight_timeout_seconds is not None
            and monotonic() - self._inflight_since >= self.inflight_timeout_seconds
        ):
            self._fail_closed()
            return None

        if self._ready:
            return self._consume_ready()

        if self._in_flight is not None:
            reply = self.worker.poll()
            if reply is not None:
                sent = self._in_flight
                if not self._accept_reply(reply, sent):
                    self._fail_closed()
                    return None
                self._in_flight = None
                self._inflight_since = None
                self._ready.extend(zip(sent.requests, reply.results, strict=True))
                if self._ready:
                    return self._consume_ready()

        if self._in_flight is None and not self._ready:
            requests: list[IVWorkRequest] = []
            for _ in range(self.max_batch):
                request = self.scheduler.take()
                if request is None:
                    break
                requests.append(request)
            if requests:
                self._batch_id += 1
                outgoing = BatchEnvelope(
                    self._batch_id, self.worker.generation, tuple(requests),
                )
                if not self.worker.submit(outgoing):
                    self._fail_closed()
                    return None
                self._in_flight = outgoing
                self._inflight_since = monotonic()
        return None


class MicrobatchIVRuntime(OptionIVRuntime):
    """Real OptionIVRuntime owner/portfolio commit with an experimental IPC seam."""

    def __init__(
        self, *, batch_size: int, cache_entries: int, queue_capacity: int,
        max_pending: int, max_priority_streak: int,
        inflight_timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(
            cache_entries=cache_entries, queue_capacity=queue_capacity,
            max_pending=max_pending, max_priority_streak=max_priority_streak,
            inflight_timeout_seconds=inflight_timeout_seconds,
        )
        self.worker = PrototypeBatchWorker(
            cache_entries=cache_entries, queue_capacity=queue_capacity,
            max_batch=batch_size,
        )
        self.dispatcher = MicrobatchDispatcher(
            self.worker, LatestWinsScheduler(max_pending, max_priority_streak),
            max_batch=batch_size,
            inflight_timeout_seconds=inflight_timeout_seconds,
        )
        self.batch_size = batch_size

    def pump(self) -> bool:
        """Commit all validated members in one owner event, without IPC work here."""
        accepted = False
        for _ in range(self.batch_size):
            accepted = super().pump() or accepted
            if self.dispatcher.timed_out or not self.dispatcher._ready:
                break
        return accepted


def replay_microbatch(
    *, options_count: int, events: int, seed: int, batch_size: int,
    burst: bool, wait_seconds: float = 5.0,
) -> dict[str, Any]:
    if not 1 <= batch_size <= 8:
        raise ValueError("Only 1-8 requests per experimental microbatch")
    with patch.object(
        chain, "OptionIVRuntime",
        lambda **kwargs: MicrobatchIVRuntime(batch_size=batch_size, **kwargs),
    ):
        row = chain.replay(
            options_count=options_count, count=events, seed=seed,
            worker=True, burst=burst, wait_seconds=wait_seconds,
            readiness_scope="portfolio",
        )
    row["batch_size"] = batch_size
    row["risk_implementation"] = "real_owner_capture_commit"
    row["experimental_only"] = True
    if not row["all_risk_ready_end"] or not row["portfolio_actionable_ready_end"]:
        raise AssertionError("Full portfolio did not become risk-ready")
    if not burst and row["paced_event_failures"]:
        raise AssertionError("Microbatch failed the bounded risk-ready wait")
    if row["scheduler_overflows"]:
        raise AssertionError("Microbatch overflowed the pending scheduler")
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", type=int, choices=(6, 12, 24), default=12)
    parser.add_argument("--seed", type=int, default=20261123)
    parser.add_argument("--batch-size", type=int, choices=(4, 8), default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = {
        schedule: replay_microbatch(
            options_count=args.options, events=args.options * 6, seed=args.seed,
            batch_size=args.batch_size, burst=schedule == "burst",
        )
        for schedule in ("paced", "burst")
    }
    result = {
        "kind": "optionmaster_offline_owner_integrated_microbatch",
        "production_approved": False,
        "environment": {
            "platform": platform.platform(), "python": platform.python_version(),
            "options": args.options, "events": args.options * 6, "seed": args.seed,
            "batch_size": args.batch_size, "synthetic": True,
        },
        "risks": [
            "The prototype Worker lacks production selftest and real gateway integration.",
            "A grouped response is committed only by owner after exact per-option freshness checks.",
            "The risk-first LatestWins policy is reused but held-position latency during long batches needs approved SLO.",
            "This replay is NOT a real source-time or Windows/CTP production acceptance.",
        ],
        "runs": cases,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()

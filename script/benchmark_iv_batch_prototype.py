"""Isolated IV batch-IPC feasibility benchmark. NEVER a production Worker protocol.

Compare the existing SingleIVWorker (one IVWorkRequest per IPC round trip)
to a disposable subprocess that receives bounded tuples of IVWorkRequest.
Both use the exact same calculate_batch/WorkerCache/Numerical IVResult path.

This is a numeric-response transport test only. It does NOT integrate with
IVDispatcher LatestWins, capture/commit, risk snapshots, source chronology,
market-data ingestion or outbound trading; it cannot prove production G5.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
from dataclasses import dataclass, replace
from multiprocessing import get_context
from pathlib import Path
from queue import Empty, Full
from time import monotonic, perf_counter, sleep
from typing import Any

from vnpy_optionmaster.pricing.iv_worker import (
    IVWorkRequest, IVWorkResult, SingleIVWorker, WorkerCache, calculate_batch,
)

if __package__:
    from .benchmark_iv_attribution import quantile, requests_for
else:
    from benchmark_iv_attribution import quantile, requests_for

MAX_REQUESTS = 24
POLL_SLEEP_S = 0.0005


@dataclass(frozen=True)
class BatchEnvelope:
    batch_id: int
    generation: int
    requests: tuple[IVWorkRequest, ...]


@dataclass(frozen=True)
class BatchReply:
    batch_id: int
    generation: int
    results: tuple[IVWorkResult, ...]
    cache_hits: int
    failed: bool = False


def calculate_group(batch: BatchEnvelope, cache: WorkerCache) -> BatchReply:
    """Same single-request numerical algorithm, grouped without parallelism."""
    if not 1 <= len(batch.requests) <= MAX_REQUESTS:
        raise ValueError("Prototype batch exceeds bounded numerical request limit")
    if len({req.symbol for req in batch.requests}) != len(batch.requests):
        raise ValueError("Duplicate symbol within one prototype batch")
    if any(req.generation != batch.generation for req in batch.requests):
        raise ValueError("Mixed Worker generations in the same batch")
    results = tuple(calculate_batch(req, cache) for req in batch.requests)
    return BatchReply(
        batch_id=batch.batch_id,
        generation=batch.generation,
        results=results,
        cache_hits=sum(result.cache_hits for result in results),
    )


def validate_group(
    sent: BatchEnvelope,
    reply: BatchReply,
    current: dict[str, IVWorkRequest],
) -> bool:
    """Owner-local all-or-none eligibility check for numerical results only.

    It intentionally does not mutate any option/portfolio risk state.
    Full production admission still requires live source/version/position guards.
    """
    if (
        reply.failed or sent.batch_id != reply.batch_id
        or sent.generation != reply.generation
        or not 1 <= len(sent.requests) <= MAX_REQUESTS
        or len(sent.requests) != len(reply.results)
        or len({req.symbol for req in sent.requests}) != len(sent.requests)
    ):
        return False
    for req, result in zip(sent.requests, reply.results, strict=True):
        if (
            req.generation != sent.generation
            or current.get(req.symbol) != req
            or result.symbol != req.symbol
            or result.generation != req.generation
            or result.sequence != req.sequence
            or result.quote_identity != req.quote_identity
            or result.model_version != req.model_version
            or not result.mid.valid
        ):
            return False
    return True


def _batch_worker_main(inbox: Any, outbox: Any, ready: Any, capacity: int) -> None:
    """Independent throwaway process; no import of gateways or order senders."""
    cache = WorkerCache(capacity)
    ready.set()
    while True:
        message = inbox.get()
        if message is None:
            return
        try:
            answer = calculate_group(message, cache)
        except Exception:
            # A failed batch must not be accepted as a partial risk publication.
            answer = BatchReply(message.batch_id, message.generation, (), 0, failed=True)
        outbox.put(answer)


class PrototypeBatchWorker:
    """Disposable batch IPC test double, NOT the production SingleIVWorker."""

    def __init__(self, *, cache_entries: int, queue_capacity: int, max_batch: int):
        if cache_entries <= 0 or queue_capacity <= 0 or not 1 <= max_batch <= MAX_REQUESTS:
            raise ValueError("All prototype capacities must be positive and bounded")
        self.cache_entries = cache_entries
        self.queue_capacity = queue_capacity
        self.max_batch = max_batch
        self.generation = 0
        self._ctx = get_context("spawn")
        self._process: Any = None
        self._inbox: Any = None
        self._outbox: Any = None

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def start(self, *, ready_timeout: float = 5.0) -> bool:
        if self.alive:
            return True
        self.close()
        self.generation += 1
        self._inbox = self._ctx.Queue(maxsize=self.queue_capacity)
        self._outbox = self._ctx.Queue(maxsize=self.queue_capacity)
        ready = self._ctx.Event()
        self._process = self._ctx.Process(
            target=_batch_worker_main,
            args=(self._inbox, self._outbox, ready, self.cache_entries),
            daemon=True,
        )
        self._process.start()
        if not ready.wait(timeout=ready_timeout) or not self.alive:
            self.close()
            return False
        return True

    def submit(self, batch: BatchEnvelope) -> bool:
        if (
            not self.alive or batch.generation != self.generation
            or not 1 <= len(batch.requests) <= self.max_batch
            or len({req.symbol for req in batch.requests}) != len(batch.requests)
            or any(req.generation != self.generation for req in batch.requests)
        ):
            return False
        try:
            self._inbox.put_nowait(batch)
        except Full:
            return False
        return True

    def poll(self) -> BatchReply | None:
        if not self.alive:
            return None
        try:
            result: BatchReply = self._outbox.get_nowait()
        except Empty:
            return None
        return result if result.generation == self.generation else None

    def close(self) -> None:
        proc = self._process
        if proc is not None:
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)
        for q in (self._inbox, self._outbox):
            if q is not None:
                q.close()
                q.cancel_join_thread()
        self._process = self._inbox = self._outbox = None


def _ordered_inputs(options_count: int, seed: int) -> tuple[IVWorkRequest, ...]:
    if not 2 <= options_count <= MAX_REQUESTS:
        raise ValueError("Option count exceeds experiment bounds")
    inputs = requests_for(options_count)
    random.Random(seed).shuffle(inputs)
    return tuple(inputs)


def _request_hash(inputs: tuple[IVWorkRequest, ...]) -> str:
    payload = [
        [req.symbol, req.model, req.spot, req.strike, req.rate,
         req.expiry, req.cp, req.bid, req.ask, req.mid]
        for req in inputs
    ]
    return hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()


def run_variant(
    *, options_count: int, seed: int, batch_size: int, wait_seconds: float = 5.0
) -> dict[str, Any]:
    """All contracts numeric-ready only after every validated response arrives."""
    if not 1 <= batch_size <= MAX_REQUESTS or not 0.5 <= wait_seconds <= 10.0:
        raise ValueError("Experiment cap exceeded")
    inputs = _ordered_inputs(options_count, seed)
    cache_entries = 3 * options_count + 6
    worker = (
        SingleIVWorker(cache_entries=cache_entries, queue_capacity=2)
        if batch_size == 1 else
        PrototypeBatchWorker(
            cache_entries=cache_entries, queue_capacity=2,
            max_batch=batch_size,
        )
    )
    startup = perf_counter()
    if not worker.start():
        raise RuntimeError("Offline numerical process did not start")
    startup_ms = (perf_counter() - startup) * 1e3
    output: dict[str, Any] = {}
    try:
        for phase in ("cold", "warm"):
            # Replacing sequence does not change the exact numeric cache key.
            current_inputs = tuple(replace(
                req, generation=worker.generation,
                sequence=req.sequence + (options_count if phase == "warm" else 0),
            ) for req in inputs)
            current = {req.symbol: req for req in current_inputs}
            if len(current) != len(current_inputs):
                raise AssertionError("Duplicate input symbols")
            responses: list[IVWorkResult] = []
            batches: list[BatchReply] = []
            polls = 0
            group_ms: list[float] = []
            start = perf_counter()
            groups = [
                current_inputs[i:i + batch_size]
                for i in range(0, len(current_inputs), batch_size)
            ]
            for idx, group in enumerate(groups, 1):
                envelope = BatchEnvelope(
                    batch_id=idx + (1000 if phase == "warm" else 0),
                    generation=worker.generation,
                    requests=group,
                )
                started_group = perf_counter()
                if batch_size == 1:
                    if not worker.submit(group[0]):
                        raise RuntimeError("Existing SingleIVWorker rejected a request")
                elif not worker.submit(envelope):
                    raise RuntimeError("Prototype batch process rejected group")
                deadline = monotonic() + wait_seconds
                reply: Any = None
                while reply is None and monotonic() < deadline:
                    polls += 1
                    reply = worker.poll()
                    if reply is None:
                        sleep(POLL_SLEEP_S)
                group_ms.append((perf_counter() - started_group) * 1e3)
                if reply is None:
                    raise TimeoutError("Numerical group response timed out")
                if batch_size == 1:
                    responses.append(reply)
                else:
                    batches.append(reply)
                    responses.extend(reply.results)
            elapsed = (perf_counter() - start) * 1e3
            # Validation is deliberately after timing so the two process modes
            # compare transport + numerical compute, not reference recompute.
            if len(responses) != len(current_inputs):
                raise AssertionError("Partial numerical group response")
            if batch_size > 1:
                if not all(validate_group(
                    BatchEnvelope(
                        batch_id=i + 1 + (1000 if phase == "warm" else 0),
                        generation=worker.generation, requests=group,
                    ), reply, current,
                ) for i, (group, reply) in enumerate(zip(groups, batches, strict=True))):
                    raise AssertionError("Stale or invalid batch result")
            hits = 0
            for req, result in zip(current_inputs, responses, strict=True):
                reference = calculate_batch(req, WorkerCache(6))
                if (
                    result.symbol != req.symbol
                    or result.generation != req.generation
                    or result.sequence != req.sequence
                    or result.quote_identity != req.quote_identity
                    or result.model_version != req.model_version
                    or any(
                        getattr(result, side) != getattr(reference, side)
                        for side in ("bid", "ask", "mid")
                    )
                    or not result.mid.valid
                ):
                    raise AssertionError("Numerical result differs from identical solver")
                hits += result.cache_hits
            output[phase] = {
                "validated_responses": len(responses),
                "full_numerical_batch_ready": True,
                "full_portfolio_risk_committed": False,
                "cache_hits": hits,
                "batch_count": len(groups),
                "total_wall_ms": round(elapsed, 3),
                "group_p50_ms": quantile(group_ms, 0.5),
                "group_p95_ms": quantile(group_ms, 0.95),
                "poll_count": polls,
            }
        return {
            "mode": "serial_existing" if batch_size == 1 else "batch_prototype",
            "batch_size": batch_size,
            "options_count": options_count,
            "seed": seed,
            "request_input_hash": _request_hash(inputs),
            "worker_startup_ms": round(startup_ms, 3),
            **output,
        }
    finally:
        worker.close()


def run_experiment(options_count: int, seed: int) -> dict[str, Any]:
    if options_count not in (6, 12, 24):
        raise ValueError("Supported sizes are 6, 12, 24")
    sizes = sorted({1, options_count, *(size for size in (4, 8) if size <= options_count)})
    modes = [run_variant(
        options_count=options_count, seed=seed, batch_size=size
    ) for size in sizes]
    expected_hashes = {m["request_input_hash"] for m in modes}
    if len(expected_hashes) != 1:
        raise AssertionError("Not the same numerical input stream")
    return {
        "kind": "optionmaster_offline_grouped_iv_ipc_prototype",
        "production_approved": False,
        "no_order_or_portfolio_commit": True,
        "options_count": options_count,
        "seed": seed,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "source": "pure_synthetic_static_iv_requests",
        },
        "methods": [
            "Serial baseline is the EXISTING SingleIVWorker; batch is an isolated prototype subprocess.",
            "Both share exact Black-76 structured safeguarded Newton calculate_batch and WorkerCache.",
            "Same shuffled synthetic request set for all modes; seed only changes request order.",
            "One Worker subprocess at a time and one IPC exchange per group, 0.5-ms polling.",
            "All numerical IVResults are reference-checked after timing, no owner risk commit.",
            "No LatestWins/fair scheduling, live Tick staleness, partial portfolio updates, or order permissions represented.",
            "Startup reported separately; no real market feed, approved SLO, CTP or Windows evidence.",
        ],
        "runs": modes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", type=int, choices=(6, 12, 24), default=12)
    parser.add_argument("--seed", type=int, default=20261123)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_experiment(args.options, args.seed)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()

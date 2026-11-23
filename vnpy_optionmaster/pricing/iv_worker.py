"""One-worker numerical request contract, exact IV LRU and bounded dispatch.

All data crossing this boundary is immutable and pickle-safe. This module does
not access portfolio state, gateway connections or order submission.
"""
from collections import OrderedDict
from dataclasses import dataclass
from math import isfinite
from multiprocessing import get_context
from queue import Empty, Full
from time import monotonic
from typing import Any

from .iv_solver import IVResult, IVStatus


@dataclass(frozen=True)
class IVWorkRequest:
    symbol: str
    generation: int
    sequence: int
    model: str
    spot: float
    strike: float
    rate: float
    expiry: float
    cp: int
    bid: float
    ask: float
    mid: float
    quote_identity: tuple[str, str]
    model_version: int


@dataclass(frozen=True)
class IVWorkResult:
    symbol: str
    generation: int
    sequence: int
    quote_identity: tuple[str, str]
    model_version: int
    bid: IVResult
    ask: IVResult
    mid: IVResult
    cache_hits: int


class WorkerCache:
    """Worker-scoped bounded cache; only valid numerics enter the table."""

    def __init__(self, max_entries: int) -> None:
        if max_entries <= 0:
            raise ValueError("Cache must be bounded")
        self.max_entries = max_entries
        self._values: OrderedDict[tuple[Any, ...], IVResult] = OrderedDict()

    @property
    def entry_count(self) -> int:
        return len(self._values)

    def get(self, key: tuple[Any, ...]) -> IVResult | None:
        result = self._values.get(key)
        if result is not None:
            self._values.move_to_end(key)
        return result

    def put(self, key: tuple[Any, ...], value: IVResult) -> None:
        if not value.valid:
            return
        self._values[key] = value
        self._values.move_to_end(key)
        if len(self._values) > self.max_entries:
            self._values.popitem(last=False)


class LatestWinsScheduler:
    """Owner-side bounded pending work, risk-first with ordinary-service bound."""

    def __init__(self, max_pending: int, max_priority_streak: int) -> None:
        if max_pending <= 0 or max_priority_streak <= 0:
            raise ValueError("Both queue bounds must be positive")
        self.max_pending = max_pending
        self.max_priority_streak = max_priority_streak
        self._pending: OrderedDict[str, tuple[IVWorkRequest, bool]] = OrderedDict()
        self._streak = 0
        self.overflows = 0
        self.superseded = 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def offer(self, request: IVWorkRequest, *, active_risk: bool) -> bool:
        if request.symbol in self._pending:
            old, _ = self._pending[request.symbol]
            if (request.generation, request.sequence) <= (old.generation, old.sequence):
                return False
            self._pending.pop(request.symbol)
            self.superseded += 1
        elif len(self._pending) >= self.max_pending:
            self.overflows += 1
            return False
        self._pending[request.symbol] = (request, active_risk)
        return True

    def take(self) -> IVWorkRequest | None:
        if not self._pending:
            return None
        pending = list(self._pending.items())
        priorities = [key for key, (_, is_priority) in pending if is_priority]
        ordinary = [key for key, (_, is_priority) in pending if not is_priority]
        if priorities and (self._streak < self.max_priority_streak or not ordinary):
            key = priorities[0]
            self._streak += 1
        else:
            key = ordinary[0]
            self._streak = 0
        return self._pending.pop(key)[0]


_MODELS = frozenset(("black_76", "black_scholes"))


def calculate_batch(request: IVWorkRequest, cache: WorkerCache) -> IVWorkResult:
    """Calculate the three quote sides with the same model/input snapshot.

    The cache deliberately omits Tick identity; the calling owner must compare
    generations and quote identity before committing any risk.
    """
    results: list[IVResult] = []
    hits = 0
    valid_model = request.model in _MODELS
    if valid_model:
        # Pickle-safe named functions are imported lazily in the worker process.
        from . import black_76, black_scholes
        model = black_76 if request.model == "black_76" else black_scholes
    for premium in (request.bid, request.ask, request.mid):
        key = (
            request.model, request.model_version, premium, request.spot,
            request.strike, request.rate, request.expiry, request.cp,
        )
        if (not valid_model or not request.symbol or request.model_version <= 0
                or not all(isfinite(x) for x in
                           (premium, request.spot, request.strike, request.rate, request.expiry))):
            results.append(IVResult(None, IVStatus.INVALID_INPUT))
            continue
        previous = cache.get(key)
        if previous is not None:
            hits += 1
            results.append(previous)
            continue
        computed = model.calculate_impv_result(
            premium, request.spot, request.strike, request.rate, request.expiry, request.cp
        )
        if computed.valid:
            cache.put(key, computed)
        results.append(computed)
    return IVWorkResult(
        symbol=request.symbol,
        generation=request.generation,
        sequence=request.sequence,
        quote_identity=request.quote_identity,
        model_version=request.model_version,
        bid=results[0],
        ask=results[1],
        mid=results[2],
        cache_hits=hits,
    )
def _worker_main(
    inbox: Any,
    outbox: Any,
    ready: Any,
    cache_size: int,
) -> None:
    """Dedicated computation process: no gateways, shared portfolio or orders."""
    startup_cache = WorkerCache(6)
    # Before the parent may trust IPC, prove the selected Python IV path works.
    # The test is isolated to this process and grants no live trading authority.
    try:
        from . import black_76, black_scholes
        for label, model in (
            ("black_76", black_76),
            ("black_scholes", black_scholes),
        ):
            price = model.calculate_price(100, 100, 0.02, 0.5, 0.2, 1)
            check = calculate_batch(IVWorkRequest(
                "IV-SELFTEST", 0, 0, label,
                100.0, 100.0, 0.02, 0.5, 1,
                price, price, price, ("selftest", "selftest"), 1,
            ), startup_cache)
            if not check.mid.valid or check.mid.volatility is None:
                return
            if abs(check.mid.volatility - 0.2) > 1e-6:
                return
    except (ValueError, ArithmeticError, ImportError):
        return
    # The operational LRU must be cold after every Worker generation change.
    # Numerical selftest samples must never contaminate live cache statistics.
    cache = WorkerCache(cache_size)
    ready.set()
    while True:
        request = inbox.get()
        if request is None:
            break
        try:
            reply = calculate_batch(request, cache)
        except (ValueError, OverflowError, ArithmeticError):
            invalid = IVResult(None, IVStatus.NUMERICAL_FAILURE)
            reply = IVWorkResult(
                request.symbol, request.generation, request.sequence,
                request.quote_identity, request.model_version,
                invalid, invalid, invalid, 0,
            )
        outbox.put(reply)


class SingleIVWorker:
    """Process lifecycle wrapper. The caller alone owns accepted risk commits.

    It deliberately never makes a trading decision or transparently restarts a
    crashed worker; the owner must revoke risk and explicitly start a new epoch.
    """

    def __init__(self, *, cache_entries: int, queue_capacity: int) -> None:
        if cache_entries <= 0 or queue_capacity <= 0:
            raise ValueError("Process, cache and IPC queues must be bounded")
        self.cache_entries = cache_entries
        self.queue_capacity = queue_capacity
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
            target=_worker_main,
            args=(self._inbox, self._outbox, ready, self.cache_entries),
            daemon=True,
        )
        self._process.start()
        if not ready.wait(timeout=ready_timeout) or not self.alive:
            self.close()
            return False
        return True

    def submit(self, request: IVWorkRequest) -> bool:
        if not self.alive or request.generation != self.generation:
            return False
        try:
            self._inbox.put_nowait(request)
        except Full:
            return False
        return True

    def poll(self) -> IVWorkResult | None:
        if not self.alive:
            return None
        try:
            result: IVWorkResult = self._outbox.get_nowait()
        except Empty:
            return None
        return result if result.generation == self.generation else None

    def close(self) -> None:
        proc = self._process
        if proc is not None:
            if proc.is_alive():
                # Terminate rather than blocking the event loop on a full queue.
                proc.terminate()
            proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)
        for queue in (self._inbox, self._outbox):
            if queue is not None:
                queue.close()
                queue.cancel_join_thread()
        self._process = None
        self._inbox = None
        self._outbox = None
class IVDispatcher:
    """Owner-process dispatch seam: at most one request in flight.

    Caller must revoke outdated risk upon each new quote and must independently
    verify live option/underlying/settings identities before committing results.
    """

    def __init__(
        self, worker: SingleIVWorker, scheduler: LatestWinsScheduler,
        *, inflight_timeout_seconds: float | None = None,
    ) -> None:
        if inflight_timeout_seconds is not None and (
            not isfinite(inflight_timeout_seconds) or inflight_timeout_seconds <= 0
        ):
            raise ValueError("An explicitly configured timeout must be finite and positive")
        self.worker = worker
        self.scheduler = scheduler
        self.inflight_timeout_seconds = inflight_timeout_seconds
        self._in_flight: IVWorkRequest | None = None
        self._inflight_since: float | None = None
        self._latest: dict[str, IVWorkRequest] = {}
        self.stale_results = 0
        self.timed_out = False

    def offer(self, request: IVWorkRequest, *, active_risk: bool) -> bool:
        if self.timed_out or not self.worker.alive or request.generation != self.worker.generation:
            return False
        previous = self._latest.get(request.symbol)
        if previous is not None and (
            request.generation, request.sequence
        ) <= (previous.generation, previous.sequence):
            return False
        if not self.scheduler.offer(request, active_risk=active_risk):
            # Do not advertise the request as processed when it was dropped.
            return False
        self._latest[request.symbol] = request
        return True

    def pump(self) -> IVWorkResult | None:
        if not self.worker.alive or self.timed_out:
            self._in_flight = None
            self._inflight_since = None
            return None
        if (
            self._in_flight is not None
            and self._inflight_since is not None
            and self.inflight_timeout_seconds is not None
            and monotonic() - self._inflight_since >= self.inflight_timeout_seconds
        ):
            # A possibly stuck numerical process must never release cached risk.
            self.timed_out = True
            self._in_flight = None
            self._inflight_since = None
            self._latest.clear()
            self.worker.close()
            return None
        result: IVWorkResult | None = None
        if self._in_flight is not None:
            received = self.worker.poll()
            if received is not None:
                expected = self._in_flight
                self._in_flight = None
                self._inflight_since = None
                latest = self._latest.get(received.symbol)
                if (
                    latest == expected
                    and received.generation == expected.generation
                    and received.sequence == expected.sequence
                    and received.quote_identity == expected.quote_identity
                    and received.model_version == expected.model_version
                ):
                    result = received
                    # Completed requests do not need a lifetime symbol cache:
                    # only in-flight and pending work may occupy metadata.
                    self._latest.pop(received.symbol, None)
                else:
                    self.stale_results += 1
        if self._in_flight is None:
            next_request = self.scheduler.take()
            if next_request is not None and self.worker.submit(next_request):
                self._in_flight = next_request
                self._inflight_since = monotonic()
            elif next_request is not None:
                # Explicit failed dispatch: never claim completed risk.
                self._latest.pop(next_request.symbol, None)
        return result

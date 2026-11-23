"""Offline worker contract: bounded Latest-Wins and numeric-only exact cache."""
from dataclasses import replace

import pytest

from vnpy_optionmaster.pricing.iv_solver import IVStatus
from vnpy_optionmaster.pricing.iv_worker import (
    IVWorkRequest, LatestWinsScheduler, WorkerCache, calculate_batch,
)


def _request(symbol="IO.CFFEX", generation=1, sequence=1, *, premium=2.5):
    return IVWorkRequest(
        symbol=symbol,
        generation=generation,
        sequence=sequence,
        model="black_76",
        spot=100.0,
        strike=100.0,
        rate=0.02,
        expiry=0.5,
        cp=1,
        bid=premium,
        ask=premium + 0.5,
        mid=premium + 0.25,
        quote_identity=("opt-1", "under-1"),
        model_version=1,
    )


def test_scheduling_latest_wins_and_fairness():
    schedule = LatestWinsScheduler(max_pending=3, max_priority_streak=2)
    schedule.offer(_request("active", sequence=1), active_risk=True)
    schedule.offer(_request("active", sequence=2), active_risk=True)
    schedule.offer(_request("quiet"), active_risk=False)
    assert schedule.pending_count == 2
    assert schedule.take().sequence == 2
    assert schedule.take().symbol == "quiet"
    assert schedule.take() is None


def test_scheduling_bounded_queue_rejects_new_requests_fail_closed():
    schedule = LatestWinsScheduler(max_pending=1, max_priority_streak=1)
    assert schedule.offer(_request("A"), active_risk=True)
    assert not schedule.offer(_request("B"), active_risk=False)
    assert schedule.overflows == 1
    assert schedule.take().symbol == "A"


def test_worker_exact_input_cache_is_numerical_only():
    cache = WorkerCache(max_entries=8)
    first = _request()
    result = calculate_batch(first, cache)
    assert result.sequence == 1
    assert result.mid.status is IVStatus.SUCCESS
    assert result.cache_hits == 0
    again = calculate_batch(replace(first, sequence=2, quote_identity=("opt-2", "under-2")), cache)
    assert again.cache_hits == 3
    assert again.quote_identity == ("opt-2", "under-2")
    different = calculate_batch(replace(first, sequence=3, rate=0.03), cache)
    assert different.cache_hits == 0
    assert cache.entry_count <= 8


def test_worker_failure_never_cached_and_one_side_not_mid():
    cache = WorkerCache(max_entries=3)
    data = _request(premium=0.0)
    outcome = calculate_batch(data, cache)
    assert not outcome.bid.valid
    assert outcome.mid.valid
    assert cache.entry_count == 2
    again = calculate_batch(replace(data, sequence=2), cache)
    assert again.cache_hits == 2
    assert not again.bid.valid


def test_invalid_worker_configuration_rejected():
    with pytest.raises(ValueError):
        WorkerCache(max_entries=0)
    with pytest.raises(ValueError):
        LatestWinsScheduler(max_pending=0, max_priority_streak=1)


def test_worker_unknown_model_fails_closed():
    result = calculate_batch(replace(_request(), model="fake"), WorkerCache(5))
    assert not result.mid.valid
    assert result.mid.status is IVStatus.INVALID_INPUT
def test_spawned_worker_process_isolated_round_trip_and_generation():
    from time import monotonic, sleep
    from vnpy_optionmaster.pricing.iv_worker import SingleIVWorker

    worker = SingleIVWorker(cache_entries=8, queue_capacity=2)
    try:
        assert worker.start()
        generation = worker.generation
        assert worker.submit(_request(generation=generation))
        deadline = monotonic() + 5
        result = None
        while monotonic() < deadline:
            result = worker.poll()
            if result is not None:
                break
            sleep(0.01)
        assert result is not None
        assert result.generation == generation
        assert result.mid.valid
        assert worker.submit(replace(
            _request(generation=generation), sequence=2,
            model="black_scholes",
        ))
        deadline = monotonic() + 5
        second = None
        while monotonic() < deadline:
            second = worker.poll()
            if second is not None:
                break
            sleep(0.01)
        assert second is not None and second.mid.valid
        assert second.sequence == 2
        worker.close()
        assert not worker.submit(_request(generation=generation))
        assert worker.start()
        assert worker.generation == generation + 1
        assert not worker.submit(_request(generation=generation))
    finally:
        worker.close()
def test_dispatcher_discards_old_inflight_result_after_latest_quote():
    from vnpy_optionmaster.pricing.iv_worker import IVDispatcher

    class FakeWorker:
        alive = True
        generation = 1

        def __init__(self):
            self.sent = []
            self.ready = []

        def submit(self, request):
            self.sent.append(request)
            return True

        def poll(self):
            return self.ready.pop(0) if self.ready else None

    fake = FakeWorker()
    coordinator = IVDispatcher(fake, LatestWinsScheduler(max_pending=3, max_priority_streak=2))
    first = _request(sequence=1)
    replacement = replace(first, sequence=2, quote_identity=("opt2", "under1"))
    assert coordinator.offer(first, active_risk=True)
    assert coordinator.pump() is None
    assert fake.sent == [first]
    assert coordinator.offer(replacement, active_risk=True)
    fake.ready.append(calculate_batch(first, WorkerCache(8)))
    assert coordinator.pump() is None
    assert coordinator.stale_results == 1
    assert fake.sent == [first, replacement]
    fake.ready.append(calculate_batch(replacement, WorkerCache(8)))
    accepted = coordinator.pump()
    assert accepted is not None
    assert accepted.sequence == 2
    assert accepted.quote_identity == replacement.quote_identity


def test_dispatcher_inflight_timeout_shuts_worker_and_rejects_future_work(monkeypatch):
    import vnpy_optionmaster.pricing.iv_worker as iv_worker
    from vnpy_optionmaster.pricing.iv_worker import IVDispatcher

    class HungWorker:
        alive = True
        generation = 1
        close_calls = 0

        def submit(self, request):
            return True

        def poll(self):
            return None

        def close(self):
            self.alive = False
            self.close_calls += 1

    clock = [100.0]
    monkeypatch.setattr(iv_worker, "monotonic", lambda: clock[0])
    worker = HungWorker()
    dispatcher = IVDispatcher(
        worker, LatestWinsScheduler(2, 1), inflight_timeout_seconds=1.0,
    )
    request = _request(generation=1)
    assert dispatcher.offer(request, active_risk=True)
    assert dispatcher.pump() is None
    clock[0] = 101.5
    assert dispatcher.pump() is None
    assert dispatcher.timed_out
    assert worker.close_calls == 1
    assert not dispatcher.offer(replace(request, sequence=2), active_risk=True)


def test_dispatcher_requires_explicit_positive_finite_timeout():
    from vnpy_optionmaster.pricing.iv_worker import IVDispatcher
    with pytest.raises(ValueError):
        IVDispatcher(None, LatestWinsScheduler(2, 1), inflight_timeout_seconds=0)
    with pytest.raises(ValueError):
        IVDispatcher(None, LatestWinsScheduler(2, 1), inflight_timeout_seconds=float("inf"))


def test_dispatcher_releases_completed_symbol_metadata_in_long_session():
    from vnpy_optionmaster.pricing.iv_worker import IVDispatcher

    class FakeWorker:
        alive = True
        generation = 1

        def __init__(self):
            self.queued = []

        def submit(self, request):
            self.queued.append(calculate_batch(request, WorkerCache(5)))
            return True

        def poll(self):
            return self.queued.pop(0) if self.queued else None

    coordinator = IVDispatcher(FakeWorker(), LatestWinsScheduler(2, 1))
    for i in range(120):
        request = _request(symbol=f"OPT{i}", sequence=i + 1)
        assert coordinator.offer(request, active_risk=True)
        coordinator.pump()
        result = coordinator.pump()
        assert result is not None
        assert result.symbol == request.symbol
        assert len(coordinator._latest) <= 2
    assert coordinator._latest == {}


def test_worker_startup_numerical_selftest_does_not_warm_real_lru():
    from time import monotonic, sleep
    from vnpy_optionmaster.pricing import black_76
    from vnpy_optionmaster.pricing.iv_worker import SingleIVWorker

    price = black_76.calculate_price(100, 100, 0.02, 0.5, 0.2, 1)
    worker = SingleIVWorker(cache_entries=8, queue_capacity=2)
    try:
        assert worker.start()
        request = replace(
            _request(generation=worker.generation),
            bid=price, ask=price, mid=price,
        )
        assert worker.submit(request)
        deadline = monotonic() + 5
        result = None
        while monotonic() < deadline:
            result = worker.poll()
            if result is not None:
                break
            sleep(0.01)
        assert result is not None and result.mid.valid
        # Three identical sides have exactly two *within-batch* cache hits.
        # A pre-warmed startup cache would incorrectly return three.
        assert result.cache_hits == 2
    finally:
        worker.close()

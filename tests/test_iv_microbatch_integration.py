"""Test-only microbatch routing through real IV owner and portfolio semantics."""
from dataclasses import replace
from time import monotonic, sleep

import pytest

from script.benchmark_iv_microbatch_integration import (
    MicrobatchDispatcher, MicrobatchIVRuntime, replay_microbatch,
)
from script.benchmark_iv_batch_prototype import BatchReply
from vnpy_optionmaster.pricing.iv_worker import (
    LatestWinsScheduler, WorkerCache, calculate_batch,
)
from test_iv_worker_contract import _request


class FakeBatchWorker:
    alive = True
    generation = 1

    def __init__(self) -> None:
        self.sent = []
        self.results = []
        self.closed = False

    def submit(self, envelope) -> bool:
        self.sent.append(envelope)
        return True

    def poll(self):
        return self.results.pop(0) if self.results else None

    def close(self):
        self.closed = True
        self.alive = False

    def reply(self, envelope, *, tamper: bool = False):
        outcomes = tuple(calculate_batch(r, WorkerCache(48)) for r in envelope.requests)
        if tamper:
            outcomes = (replace(outcomes[0], model_version=-100), *outcomes[1:])
        self.results.append(BatchReply(
            envelope.batch_id, envelope.generation, outcomes,
            sum(x.cache_hits for x in outcomes),
        ))


def test_microbatch_scheduler_preserves_risk_first_and_bounded_fairness():
    worker = FakeBatchWorker()
    dispatcher = MicrobatchDispatcher(
        worker, LatestWinsScheduler(max_pending=8, max_priority_streak=2),
        max_batch=4, inflight_timeout_seconds=3.0,
    )
    assert dispatcher.offer(_request("held-1"), active_risk=True)
    assert dispatcher.offer(_request("held-2"), active_risk=True)
    assert dispatcher.offer(_request("held-3"), active_risk=True)
    assert dispatcher.offer(_request("ordinary"), active_risk=False)
    assert dispatcher.pump() is None
    assert len(worker.sent) == 1
    assert [x.symbol for x in worker.sent[0].requests] == [
        "held-1", "held-2", "ordinary", "held-3",
    ]


def test_inflight_stale_version_never_commits_and_fresh_siblings_remain_available():
    worker = FakeBatchWorker()
    dispatcher = MicrobatchDispatcher(
        worker, LatestWinsScheduler(12, 2), max_batch=4,
        inflight_timeout_seconds=3.0,
    )
    a, b = _request("A"), _request("B")
    assert dispatcher.offer(a, active_risk=True)
    assert dispatcher.offer(b, active_risk=True)
    assert dispatcher.pump() is None
    older = worker.sent[0]
    assert len(older.requests) == 2

    latest = replace(a, sequence=2, quote_identity=("option-later", "same-underlying"))
    assert dispatcher.offer(latest, active_risk=True)
    worker.reply(older)
    got = dispatcher.pump()
    assert got is not None and got.symbol == "B"
    assert dispatcher.stale_results == 1
    assert dispatcher.pump() is None
    assert worker.sent[-1].requests == (latest,)
    worker.reply(worker.sent[-1])
    got = dispatcher.pump()
    assert got is not None and got.symbol == "A" and got.sequence == 2


def test_malformed_group_fails_closed_without_owner_result_commit():
    worker = FakeBatchWorker()
    dispatcher = MicrobatchDispatcher(
        worker, LatestWinsScheduler(4, 2), max_batch=4,
        inflight_timeout_seconds=3.0,
    )
    assert dispatcher.offer(_request("A"), active_risk=True)
    assert dispatcher.pump() is None
    worker.reply(worker.sent[0], tamper=True)
    assert dispatcher.pump() is None
    assert dispatcher.timed_out
    assert worker.closed
    assert not dispatcher.offer(_request("B"), active_risk=True)


def test_microbatch_timeout_is_fail_closed(monkeypatch):
    import script.benchmark_iv_microbatch_integration as module

    clock = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    worker = FakeBatchWorker()
    dispatcher = MicrobatchDispatcher(
        worker, LatestWinsScheduler(4, 1), max_batch=4,
        inflight_timeout_seconds=0.5,
    )
    assert dispatcher.offer(_request(), active_risk=True)
    assert dispatcher.pump() is None
    clock[0] = 100.51
    assert dispatcher.pump() is None
    assert dispatcher.timed_out and worker.closed


def test_worker_process_death_revokes_actionable_portfolio_snapshot_immediately():
    from test_iv_chain_burst import _real_chain_fixture

    _, portfolio, _, options = _real_chain_fixture(option_count=6)
    runtime = MicrobatchIVRuntime(
        batch_size=4, cache_entries=64, queue_capacity=2,
        max_pending=12, max_priority_streak=2,
        inflight_timeout_seconds=4.0,
    )
    try:
        assert runtime.start()
        assert all(runtime.attach(option) for option in options)
        assert portfolio.actionable_risk_snapshot is None
        for option in options:
            runtime.submit(option)
        deadline = monotonic() + 5.0
        while portfolio.actionable_risk_snapshot is None and monotonic() < deadline:
            runtime.pump()
            sleep(0.001)
        assert portfolio.actionable_risk_snapshot is not None

        runtime.worker.close()
        assert portfolio.actionable_risk_snapshot is None
        assert not runtime.pump()
        assert all(not option.greeks_valid for option in options)
    finally:
        runtime.close()


@pytest.mark.parametrize("batch_size", [4, 8])
def test_offline_full_chain_replay_reaches_genuine_owner_portfolio_risk_ready(batch_size):
    row = replay_microbatch(
        options_count=6, events=24, seed=20261123,
        batch_size=batch_size, burst=False, wait_seconds=4.0,
    )
    assert row["portfolio_actionable_ready_end"]
    assert row["all_risk_ready_end"]
    assert row["paced_event_failures"] == 0
    assert row["scheduler_overflows"] == 0
    assert row["readiness_scope"] == "portfolio"
    assert row["risk_implementation"] == "real_owner_capture_commit"



@pytest.mark.parametrize("batch_size", [4, 8])
def test_urgent_held_risk_cannot_preempt_inflight_ordinary_batch(batch_size):
    """Held request is first in NEXT batch, not an interruption of current work."""
    worker = FakeBatchWorker()
    dispatcher = MicrobatchDispatcher(
        worker, LatestWinsScheduler(24, 2), max_batch=batch_size,
        inflight_timeout_seconds=3.0,
    )
    ordinary = [_request(f"ordinary-{i}") for i in range(batch_size)]
    for request in ordinary:
        assert dispatcher.offer(request, active_risk=False)
    assert dispatcher.pump() is None
    first = worker.sent[0]
    assert [req.symbol for req in first.requests] == [
        req.symbol for req in ordinary
    ]

    urgent = _request("held-urgent")
    assert dispatcher.offer(urgent, active_risk=True)
    assert dispatcher.pump() is None
    assert len(worker.sent) == 1  # No preemption of the in-flight batch.

    worker.reply(first)
    for _ in range(batch_size):
        got = dispatcher.pump()
        assert got is not None
        assert got.symbol.startswith("ordinary-")
        assert got.symbol != urgent.symbol
    assert dispatcher.pump() is None
    assert len(worker.sent) == 2
    assert worker.sent[1].requests == (urgent,)
    worker.reply(worker.sent[1])
    assert dispatcher.pump().symbol == urgent.symbol


@pytest.mark.parametrize("batch_size", [4, 8])
def test_urgent_request_supersedes_stale_member_but_not_valid_sibling(batch_size):
    """A newer held risk quote cannot make the old in-flight quote actionable."""
    worker = FakeBatchWorker()
    dispatcher = MicrobatchDispatcher(
        worker, LatestWinsScheduler(24, 2), max_batch=batch_size,
        inflight_timeout_seconds=3.0,
    )
    old = _request("held-urgent")
    sibling = _request("held-sibling")
    assert dispatcher.offer(old, active_risk=True)
    assert dispatcher.offer(sibling, active_risk=True)
    for i in range(batch_size - 2):
        assert dispatcher.offer(_request(f"ordinary-{i}"), active_risk=False)
    assert dispatcher.pump() is None
    first = worker.sent[0]
    newest = replace(
        old, sequence=2, quote_identity=("urgent-new", "same-underlying"),
    )
    assert dispatcher.offer(newest, active_risk=True)
    worker.reply(first)
    accepted = [dispatcher.pump() for _ in range(batch_size)]
    valid = [x for x in accepted if x is not None]
    assert len(valid) == batch_size - 1
    assert all(x.symbol != old.symbol for x in valid)
    assert any(x.symbol == sibling.symbol for x in valid)
    assert dispatcher.stale_results == 1
    assert dispatcher.pump() is None
    assert worker.sent[-1].requests == (newest,)

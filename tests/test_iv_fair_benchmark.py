"""Offline same-solver IV benchmark contracts; no trading or live gateway."""
from dataclasses import replace

from script import benchmark_iv_chain
from script.benchmark_iv_fair import (
    InProcessIVWorker, fair_chain_pair, microbenchmark,
)
from script.benchmark_iv_attribution import requests_for
from vnpy_optionmaster.pricing.iv_worker import WorkerCache, calculate_batch


def test_inprocess_transport_uses_identical_structured_batch_and_cache() -> None:
    worker = InProcessIVWorker(cache_entries=24, queue_capacity=2)
    assert worker.start()
    try:
        request = replace(requests_for(4)[0], generation=worker.generation)
        assert worker.submit(request)
        result = worker.poll()
        assert result is not None
        expected = calculate_batch(request, WorkerCache(24))
        assert result == expected
        assert worker.submit(replace(request, sequence=2))
        warm = worker.poll()
        assert warm is not None
        assert warm.cache_hits == 3
        assert (warm.bid, warm.ask, warm.mid) == (
            expected.bid, expected.ask, expected.mid,
        )
    finally:
        worker.close()
    assert not worker.alive


def test_same_solver_microbenchmark_keeps_valid_current_outputs() -> None:
    data = microbenchmark(requests_count=4, wait_seconds=3.0)
    assert set(data) == {"inprocess", "process"}
    for run in data.values():
        assert run["cold"]["requests"] == 4
        assert run["warm"]["requests"] == 4
        assert run["valid_outputs"] == 8
        assert run["warm_cache_hits"] == 12
        assert run["p95_warm_ms"] >= 0


def test_full_chain_worker_and_inline_share_portfolio_readiness_contract() -> None:
    runs = fair_chain_pair(
        options_count=2, events=8, seed=20261123,
        burst=False, wait_seconds=3.0,
    )
    assert set(runs) == {"inprocess", "process"}
    for mode, record in runs.items():
        assert record["readiness_scope"] == "portfolio"
        assert record["portfolio_actionable_ready_end"]
        assert record["all_risk_ready_end"]
        assert record["paced_event_failures"] == 0
        assert record["paced_event_to_ready_p95_ms"] is not None
        assert record["path"] == "worker"
        assert record["transport"] == mode


def test_existing_chain_benchmark_keeps_affected_default() -> None:
    row = benchmark_iv_chain.replay(
        options_count=2, count=8, seed=20261123,
        worker=False, burst=False, wait_seconds=3.0,
    )
    assert row["readiness_scope"] == "affected"

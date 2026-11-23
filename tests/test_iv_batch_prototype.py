"""Offline batched IV IPC proof-of-concept; never production order authority."""
from dataclasses import replace

import pytest

from script.benchmark_iv_attribution import requests_for
from script.benchmark_iv_batch_prototype import (
    BatchEnvelope, PrototypeBatchWorker, calculate_group,
    run_variant, validate_group,
)
from vnpy_optionmaster.pricing.iv_worker import WorkerCache


def test_group_matches_exact_individual_iv_results_and_warm_cache() -> None:
    requests = tuple(requests_for(4))
    cache = WorkerCache(16)
    first = calculate_group(BatchEnvelope(1, 1, requests), cache)
    assert not first.failed
    assert len(first.results) == len(requests)
    assert first.cache_hits == 0
    assert validate_group(BatchEnvelope(1, 1, requests), first, {r.symbol: r for r in requests})
    warm = calculate_group(BatchEnvelope(2, 1, requests), cache)
    assert not warm.failed
    assert warm.cache_hits == 12
    assert (warm.results[0].bid, warm.results[0].ask, warm.results[0].mid) == (
        first.results[0].bid, first.results[0].ask, first.results[0].mid,
    )


def test_group_rejects_one_stale_dependency_without_partial_approval() -> None:
    requests = tuple(requests_for(4))
    envelope = BatchEnvelope(1, 1, requests)
    result = calculate_group(envelope, WorkerCache(20))
    current = {r.symbol: r for r in requests}
    assert validate_group(envelope, result, current)
    current[requests[2].symbol] = replace(
        requests[2], quote_identity=("new-option-tick", "same-underlying-tick")
    )
    assert not validate_group(envelope, result, current)
    assert not validate_group(envelope, replace(result, batch_id=99), {
        r.symbol: r for r in requests
    })


def test_invalid_mid_or_duplicate_symbol_fails_closed_at_group_boundary() -> None:
    requests = list(requests_for(4))
    requests[1] = replace(requests[1], mid=-10.0)
    envelope = BatchEnvelope(10, 1, tuple(requests))
    response = calculate_group(envelope, WorkerCache(16))
    assert not response.results[1].mid.valid
    assert not validate_group(envelope, response, {r.symbol: r for r in requests})
    duplicate = BatchEnvelope(11, 1, (requests[0], requests[0]))
    with pytest.raises(ValueError, match="Duplicate"):
        calculate_group(duplicate, WorkerCache(16))


def test_prototype_worker_rejects_oversized_or_mixed_generation_groups() -> None:
    worker = PrototypeBatchWorker(cache_entries=64, queue_capacity=2, max_batch=4)
    assert worker.start()
    try:
        req = tuple(replace(r, generation=worker.generation) for r in requests_for(5))
        assert not worker.submit(BatchEnvelope(1, worker.generation, req))
        assert not worker.submit(BatchEnvelope(2, worker.generation + 1, req[:4]))
        assert worker.submit(BatchEnvelope(3, worker.generation, req[:4]))
    finally:
        worker.close()


@pytest.mark.parametrize("count", [4, 8])
def test_serial_and_batch_numeric_outputs_match_with_identical_inputs(count: int) -> None:
    serial = run_variant(options_count=count, seed=20261123, batch_size=1)
    grouped = run_variant(options_count=count, seed=20261123, batch_size=count)
    for result in (serial, grouped):
        assert result["cold"]["validated_responses"] == count
        assert result["warm"]["validated_responses"] == count
        assert result["warm"]["cache_hits"] == count * 3
        assert result["cold"]["full_numerical_batch_ready"]
        assert result["warm"]["full_numerical_batch_ready"]
    assert serial["request_input_hash"] == grouped["request_input_hash"]

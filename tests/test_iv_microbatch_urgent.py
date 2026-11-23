"""No live gateway: urgent quote arrives while a slow ordinary IPC group is active."""
import pytest

from script.benchmark_iv_microbatch_urgent import measure_urgent, run_study


@pytest.mark.parametrize("batch_size", [4, 8])
@pytest.mark.parametrize("arrival_fraction", [0.0, 0.5])
def test_urgent_risk_wait_is_at_least_inflight_nonpreemptible_service(
    batch_size, arrival_fraction,
):
    sample = measure_urgent(
        batch_size=batch_size, seed=20261123,
        arrival_fraction=arrival_fraction, delay_per_member_s=0.012,
    )
    assert sample["test_only"]
    assert sample["portfolio_risk_actionable"]
    assert sample["ordinary_batch_size"] == batch_size
    assert sample["urgent_next_batch"]
    assert sample["scheduler_overflows"] == 0
    assert sample["ordinary_service_ms"] >= batch_size * 12 - 1
    assert sample["urgent_worker_service_ms"] >= 12 - 1
    assert sample["remaining_nonpreemptible_ms"] > 0
    # Queue-to-dispatch necessarily contains the uninterruptible remainder.
    assert sample["urgent_queue_to_dispatch_ms"] >= (
        sample["remaining_nonpreemptible_ms"] - 0.01
    )
    assert sample["urgent_quote_to_portfolio_ready_ms"] >= (
        sample["urgent_queue_to_dispatch_ms"]
    )


@pytest.mark.parametrize("kwargs", [
    {"batch_size": 0, "arrival_fraction": 0.0},
    {"batch_size": 9, "arrival_fraction": 0.0},
    {"batch_size": 4, "arrival_fraction": -0.25},
    {"batch_size": 4, "arrival_fraction": 1.0},
    {"batch_size": 4, "arrival_fraction": 0.0, "option_count": 14},
    {"batch_size": 4, "arrival_fraction": 0.0, "delay_per_member_s": 0.0},
])
def test_bounded_urgent_study_rejects_unsupported_config(kwargs):
    with pytest.raises(ValueError):
        measure_urgent(seed=20261123, **kwargs)


def test_study_aggregation_marks_injected_delay_not_production(monkeypatch):
    import script.benchmark_iv_microbatch_urgent as module

    def fake_measure(*, batch_size, seed, arrival_fraction, delay_per_member_s):
        return {
            "batch_size": batch_size, "seed": seed,
            "arrival_fraction": arrival_fraction,
            "urgent_quote_to_portfolio_ready_ms": float(batch_size),
            "urgent_queue_to_dispatch_ms": batch_size * 0.5,
            "remaining_nonpreemptible_ms": batch_size * 0.25,
        }

    monkeypatch.setattr(module, "measure_urgent", fake_measure)
    result = run_study(seeds=(20261123, 20261124), delay_per_member_s=0.012)
    assert result["synthetic"]
    assert result["production_approved"] is False
    assert result["batch1_is_test_prototype"]
    assert result["n_records"] == 12
    assert len(result["summary"]) == 6
    assert [x["median_urgent_quote_to_portfolio_ready_ms"] for x in result["summary"]] == [
        1, 4, 8, 1, 4, 8,
    ]
